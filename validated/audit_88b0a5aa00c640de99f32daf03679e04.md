### Title
Missing sighash-type enforcement in `Operator::withdraw` lets an attacker replay a `SIGHASH_NONE`/`SIGHASH_NONE|ANYONECANPAY` signature to redirect payout funds to an arbitrary output - ([File: core/src/operator.rs])

### Summary
`Operator::withdraw` accepts a caller-supplied `taproot::Signature` and uses its embedded `sighash_type` verbatim to compute the sighash it verifies, without ever checking that the type actually equals the documented `SinglePlusAnyoneCanPay`. Because `SIGHASH_NONE` (and `NonePlusAnyoneCanPay`) excludes all outputs from the signed message, a single signature produced under that flag remains valid for the same input regardless of what `out_script_pubkey`/`out_amount` are later plugged into the payout transaction, letting whoever holds that signature redirect the operator-fronted payout to an output of their choice.

### Finding Description
Binding claimed to hold: `sighash_type used to verify in_signature against user_xonly_pk` == `sighash_type that actually commits payout output (out_script_pubkey, out_amount)`.

In `core/src/operator.rs`, `Operator::withdraw` builds the payout transaction directly from caller-supplied `out_script_pubkey`/`out_amount`: [1](#0-0) 

It then computes the sighash using the attacker-controlled `in_signature.sighash_type` and verifies it against `user_xonly_pk` with no check that the type equals the intended `TapSighashType::SinglePlusAnyoneCanPay`: [2](#0-1) 

The error message even states the expectation as documentation only ("Ensure the signature uses SinglePlusAnyoneCanPay sighash type") — it is never enforced in code. `create_payout_txhandler` simply consumes whatever `output_txout` is passed and attaches the user's key-spend witness for the tx as constructed: [3](#0-2) 

Because BIP341 `SIGHASH_NONE` excludes all transaction outputs from the signed digest, a signature produced once under `SIGHASH_NONE` (or `NonePlusAnyoneCanPay`) for a given input is valid for *any* output configuration of that transaction — the digest is identical no matter what `output_txout` is. If the withdrawer's off-chain signature (transmitted via the same public, unauthenticated gRPC channel that carries `withdrawal_index`, `in_signature`, `in_outpoint`, `out_script_pubkey`, `out_amount`) is ever produced or observed with `SIGHASH_NONE`, any party who obtains those signature bytes can call `withdraw()` again with a *different* `out_script_pubkey`/`out_amount` and the `SECP.verify_schnorr` check at line 632-637 will still pass, since it re-derives the identical NONE-flavored digest. The operator has no independent source of truth for the intended payout destination/amount other than this signature — Citrea's `get_withdrawal_utxo_from_citrea_withdrawal` only supplies the input outpoint, not the intended output: [4](#0-3) 

`Operator::is_profitable` only bounds the operator's own profit margin, it does not bind `out_amount`/`out_script_pubkey` to any Citrea-recorded value: [5](#0-4) 

No other guard (`is_deposit_valid`, `SPV::verify`, `lc_proof_verifier`, the presigned tx graph) checks the payout output against the withdrawal record either — reimbursement via `handle_finalized_payout` only checks the kickoff's OP_RETURN commitment (move_txid + operator xonly pk) and blockhash, not the payout output contents, so a redirected payout still lets the operator claim reimbursement.

### Impact Explanation
An attacker who obtains a `SIGHASH_NONE`-flavored signature for a withdrawal input (e.g., by being the withdrawer and choosing that flag, or by intercepting it once it is used) can resubmit it to `withdraw()` with an arbitrary destination and amount, causing the operator to fund a payout that does not correspond to the actual withdrawal intent recorded by the real recipient. The operator then proceeds through the normal kickoff/reimburse flow and is reimbursed for a payout that was diverted from its true recipient — matching the Critical category "an operator reimbursed for a payout it never actually funded/committed" or BTC leaving vault custody without matching the real withdrawal. This is repeatable for every withdrawal whose off-chain signature uses a non-`SinglePlusAnyoneCanPay` flag, across any operator that processes the request.

### Likelihood Explanation
The precondition is that a signature with `SIGHASH_NONE`/`NonePlusAnyoneCanPay` reaches an attacker who is not the intended beneficiary — this can occur if the withdrawer's off-chain relaying path (public, unauthenticated gRPC) exposes the signature to observers, or if malicious tooling on the withdrawer side deliberately signs this way to grief an operator. No special privileges, BTC value, or majority hashrate are required — only observing/relaying one gRPC call with attacker-chosen `out_script_pubkey`/`out_amount`. The vulnerability is entirely code-enforced (a missing `if in_signature.sighash_type != TapSighashType::SinglePlusAnyoneCanPay { return Err(...) }` check), making it deterministic and reproducible in tests without mainnet or live Citrea.

### Recommendation
In `Operator::withdraw` (core/src/operator.rs), explicitly reject any `in_signature.sighash_type` other than `TapSighashType::SinglePlusAnyoneCanPay` before computing the sighash or calling `SECP.verify_schnorr`, e.g.:
```rust
if in_signature.sighash_type != TapSighashType::SinglePlusAnyoneCanPay {
    return Err(eyre::eyre!("Invalid sighash type for withdrawal signature").into());
}
```

### Proof of Concept
`cargo test` in `core/src/test` (or a new unit test module) plan:
1. Generate a keypair for a simulated withdrawer, construct `input_utxo` with a taproot script pubkey for that key (as `try_get_taproot_pk` expects).
2. Build a `payout_txhandler` via `builder::transaction::create_payout_txhandler` with `output_txout_A` (script/amount A), compute its `TapSighashType::None` sighash via `calculate_sighash_txin(0, TapSighashType::None)`, and sign it with the withdrawer's key to produce `in_signature` with `sighash_type = TapSighashType::None`.
3. Register a fake `withdrawal_utxo` in the test DB pointing at `input_utxo.outpoint` (mirroring `get_withdrawal_utxo_from_citrea_withdrawal`).
4. Call `Operator::withdraw(withdrawal_index, in_signature, in_outpoint, out_script_pubkey_B, out_amount_B)` with a *different* output B (attacker-controlled), and assert that `SECP.verify_schnorr` inside `withdraw` succeeds (no error returned) even though the signature was never produced over output B.
5. Assert `signed_tx.output[0]` equals output B, not output A, demonstrating that the same signature validates for two disjoint output sets — violating the equality "sighash flag verified" == "flag that commits the actual payout output."

### Citations

**File:** core/src/operator.rs (L502-537)
```rust
    /// Checks if the withdrawal amount is within the acceptable range.
    fn is_profitable(
        input_amount: Amount,
        withdrawal_amount: Amount,
        bridge_amount_sats: Amount,
        operator_withdrawal_fee_sats: Amount,
    ) -> bool {
        // Use checked_sub to safely handle potential underflow
        let withdrawal_diff = match withdrawal_amount
            .to_sat()
            .checked_sub(input_amount.to_sat())
        {
            Some(diff) => Amount::from_sat(diff),
            None => {
                // input amount is greater than withdrawal amount, so it's profitable but doesn't make sense
                tracing::warn!(
                    "Some user gave more amount than the withdrawal amount as input for withdrawal"
                );
                return true;
            }
        };

        if withdrawal_diff > bridge_amount_sats {
            return false;
        }

        // Calculate net profit after the withdrawal using checked_sub to prevent panic
        let net_profit = match bridge_amount_sats.checked_sub(withdrawal_diff) {
            Some(profit) => profit,
            None => return false, // If underflow occurs, it's not profitable
        };

        // Net profit must be bigger than withdrawal fee.
        // net profit doesn't take into account the fees, but operator_withdrawal_fee_sats should
        net_profit >= operator_withdrawal_fee_sats
    }
```

**File:** core/src/operator.rs (L589-596)
```rust
        let withdrawal_utxo = self
            .db
            .get_withdrawal_utxo_from_citrea_withdrawal(None, withdrawal_index)
            .await?;

        if withdrawal_utxo != input_utxo.outpoint {
            return Err(eyre::eyre!("Input UTXO does not match withdrawal UTXO from Citrea: Input Outpoint: {0}, Withdrawal Outpoint (from Citrea): {1}", input_utxo.outpoint, withdrawal_utxo).into());
        }
```

**File:** core/src/operator.rs (L620-626)
```rust
        let payout_txhandler = builder::transaction::create_payout_txhandler(
            input_utxo,
            output_txout,
            self.signer.xonly_public_key,
            in_signature,
            self.config.protocol_paramset().network,
        )?;
```

**File:** core/src/operator.rs (L630-637)
```rust
        let sighash = payout_txhandler.calculate_sighash_txin(0, in_signature.sighash_type)?;

        SECP.verify_schnorr(
            &in_signature.signature,
            &Message::from_digest(*sighash.as_byte_array()),
            user_xonly_pk,
        )
        .wrap_err("Failed to verify signature received from user for payout txin. Ensure the signature uses SinglePlusAnyoneCanPay sighash type.")?;
```

**File:** core/src/builder/transaction/operator_reimburse.rs (L407-436)
```rust
pub fn create_payout_txhandler(
    input_utxo: UTXO,
    output_txout: TxOut,
    operator_xonly_pk: XOnlyPublicKey,
    user_sig: taproot::Signature,
    _network: bitcoin::Network,
) -> Result<TxHandler<Signed>, BridgeError> {
    let txin = SpendableTxIn::new_partial(input_utxo.outpoint, input_utxo.txout);

    let output_txout = UnspentTxOut::from_partial(output_txout.clone());

    let op_return_txout = op_return_txout(PushBytesBuf::from(operator_xonly_pk.serialize()));

    let mut txhandler = TxHandlerBuilder::new(TransactionType::Payout)
        .with_version(NON_STANDARD_V3)
        .add_input(
            NormalSignatureKind::NotStored,
            txin,
            SpendPath::KeySpend,
            DEFAULT_SEQUENCE,
        )
        .add_output(output_txout)
        .add_output(UnspentTxOut::from_partial(anchor_output(
            NON_EPHEMERAL_ANCHOR_AMOUNT,
        )))
        .add_output(UnspentTxOut::from_partial(op_return_txout))
        .finalize();
    txhandler.set_p2tr_key_spend_witness(&user_sig, 0)?;
    txhandler.promote()
}
```
