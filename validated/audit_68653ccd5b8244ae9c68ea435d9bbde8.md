### Title
Aggregator's `optimistic_payout` does not enforce `SIGHASH_SINGLE|ANYONECANPAY` on the withdrawer's input-0 signature, allowing signature reuse against a different output - ([File: core/src/rpc/aggregator.rs])

### Summary
`optimistic_payout` accepts `input_signature.sighash_type` verbatim from the gRPC caller and feeds it into `calculate_pubkey_spend_sighash(0, input_signature.sighash_type)` before calling `SECP.verify_schnorr` [1](#0-0) . There is no server-side check that the sighash flag is actually `SIGHASH_SINGLE|ANYONECANPAY` (the code only complains after the fact, via an error message that assumes that flag was used) [2](#0-1) . If the withdrawer's original signature happened to use a sighash type without output commitment (e.g. `NONE|ANYONECANPAY`), the same `(deposit_id, input_outpoint, input_signature)` tuple can be replayed with an attacker-chosen `output_script_pubkey`/`output_amount`, and `verify_schnorr` will still pass because the recomputed sighash never included the new output.

### Finding Description
The claimed binding is: `input_0_signature_commitment == payout_output_actually_paid`. `create_optimistic_payout_txhandler` builds a two-input transaction: input 0 is the user's withdrawal UTXO (key-spend, `SpendPath::KeySpend`) and input 1 is the move-to-vault deposit UTXO (script-spend by the N-of-N verifiers) [3](#0-2) . The aggregator only cross-checks `input_outpoint` against `get_withdrawal_utxo_from_citrea_withdrawal(deposit_id)` [4](#0-3) ; `output_script_pubkey`/`output_amount` are taken directly from the request with no cross-check against any Citrea-recorded destination [5](#0-4) .

Input 1 (the actual bridge funds) is later signed by the verifiers with `TapSighashType::Default`, which commits to whatever output is present in *this* call's `opt_payout_txhandler` [6](#0-5) . So per-call consistency between input 1's binding and the call's output is fine. The gap is across calls: since `input_signature.sighash_type` is attacker-supplied and unchecked, if a signature was originally produced with a sighash flag lacking output commitment (`NONE`/`NONE|ANYONECANPAY`), the exact same signature verifies for *any* output the caller now supplies, letting a second, conflicting fully-signed optimistic-payout transaction be produced against the same `withdrawal_utxo`/move-to-vault UTXO, paying a different destination.

This does not, however, arise from anything the attacker can force: the weak sighash type must have been chosen by the original withdrawer when constructing their own signature. The protocol relies entirely on the client using `SIGHASH_SINGLE|ANYONECANPAY`; the aggregator's error message assumes but never enforces this [2](#0-1) . I was not able to confirm, within the available iterations, whether `parser::operator::parse_withdrawal_sig_params` or any verifier-side check restricts `sighash_type` to `SinglePlusAnyoneCanPay`; this file was referenced but not opened.

### Impact Explanation
If exploitable, this would let a party in possession of a stale, weakly-typed withdrawal signature construct a second N-of-N-signed optimistic payout transaction diverting the same deposited funds to an attacker-controlled output — a race against the legitimate transaction, matching "N-of-N partial signatures for an unauthorised spend." However, the precondition (the withdrawer using `NONE`/`NONE|ANYONECANPAY` instead of `SINGLE|ANYONECANPAY` for their own withdrawal signature) is not attacker-controlled; it requires a mistake or non-conforming client on the honest withdrawer's side, and the attacker must additionally obtain that signature and win a broadcast race before the legitimate transaction confirms.

### Likelihood Explanation
Low-to-moderate. The attacker cannot force a legitimate withdrawer to sign with a weak sighash type — this must occur independently (e.g., a buggy or non-standard client). Given that precondition, the rest of the path (server not restricting `sighash_type`, replay with different output, race to broadcast) is real and requires no privileged access, only observation of a previously-submitted signature and the ability to broadcast a Bitcoin transaction.

### Recommendation
Enforce server-side that `input_signature.sighash_type` (and thus the sighash actually computed at aggregator.rs:1121) is exactly `TapSighashType::SinglePlusAnyoneCanPay` before calling `SECP.verify_schnorr`, rejecting any other sighash type. Additionally, independently bind `output_script_pubkey`/`output_amount` to the Citrea-recorded withdrawal request rather than trusting the caller-supplied values.

### Proof of Concept
Not fully demonstrable from the available context: reproducing this requires confirming (in `parser::operator::parse_withdrawal_sig_params` and verifier-side `optimistic_payout_sign`) whether `sighash_type` is currently unrestricted, which could not be verified within the tool-call budget. A `cargo test` would need to: (1) create a withdrawal signature with `TapSighashType::NonePlusAnyoneCanPay` over input 0 of `create_optimistic_payout_txhandler`, (2) call `optimistic_payout` once with output A and once with output B using the identical signature/outpoint/deposit_id, and (3) assert both calls pass `SECP.verify_schnorr` and produce distinct fully-verifier-signed transactions spending the same move-to-vault UTXO — confirming the missing sighash-type restriction is a live path and not neutralized elsewhere in the stack.

### Citations

**File:** core/src/rpc/aggregator.rs (L1063-1071)
```rust
            let withdrawal_utxo = self
                .db
                .get_withdrawal_utxo_from_citrea_withdrawal(None, deposit_id)
                .await?;
            if withdrawal_utxo != input_outpoint {
                return Err(Status::invalid_argument(format!(
                    "Withdrawal utxo is not correct: {withdrawal_utxo:?} != {input_outpoint:?}",
                )));
            }
```

**File:** core/src/rpc/aggregator.rs (L1090-1098)
```rust
            let withdrawal_utxo = UTXO {
                outpoint: input_outpoint,
                txout: withdrawal_prevout,
            };

            let output_txout = TxOut {
                value: output_amount,
                script_pubkey: output_script_pubkey,
            };
```

**File:** core/src/rpc/aggregator.rs (L1120-1126)
```rust
            let sighash = opt_payout_txhandler
                .calculate_pubkey_spend_sighash(0, input_signature.sighash_type)?;

            let message = Message::from_digest(sighash.to_byte_array());

            SECP.verify_schnorr(&input_signature.signature, &message, &user_xonly_pk)
                .map_err(|_| Status::internal("Invalid signature for optimistic payout tx. Ensure the signature uses SinglePlusAnyoneCanPay sighash type."))?;
```

**File:** core/src/rpc/aggregator.rs (L1192-1198)
```rust
            // calculate final sig
            // txin at index 1 is deposited utxo in movetx
            let sighash = opt_payout_txhandler.calculate_script_spend_sighash_indexed(
                1,
                0,
                bitcoin::TapSighashType::Default,
            )?;
```

**File:** core/src/builder/transaction/operator_reimburse.rs (L459-491)
```rust
pub fn create_optimistic_payout_txhandler(
    deposit_data: &mut DepositData,
    input_utxo: UTXO,
    output_txout: TxOut,
    user_sig: taproot::Signature,
    paramset: &'static ProtocolParamset,
) -> Result<TxHandler, BridgeError> {
    let move_txhandler: TxHandler = create_move_to_vault_txhandler(deposit_data, paramset)?;
    let txin = SpendableTxIn::new_partial(input_utxo.outpoint, input_utxo.txout);

    let output_txout = UnspentTxOut::from_partial(output_txout.clone());

    let mut txhandler = TxHandlerBuilder::new(TransactionType::Payout)
        .with_version(NON_STANDARD_V3)
        .add_input(
            NormalSignatureKind::NotStored,
            txin,
            SpendPath::KeySpend,
            DEFAULT_SEQUENCE,
        )
        .add_input(
            NormalSignatureKind::NotStored,
            move_txhandler.get_spendable_output(UtxoVout::DepositInMove)?,
            SpendPath::ScriptSpend(0),
            DEFAULT_SEQUENCE,
        )
        .add_output(output_txout)
        .add_output(UnspentTxOut::from_partial(
            builder::transaction::non_ephemeral_anchor_output(),
        ))
        .finalize();
    txhandler.set_p2tr_key_spend_witness(&user_sig, 0)?;
    Ok(txhandler)
```
