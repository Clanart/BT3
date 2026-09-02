## Finding is confirmed valid

### Title
Third-party SIGHASH_SINGLE|ANYONECANPAY malleation of the payout tx's OP_RETURN output causes a legitimate operator to be challenged and its collateral burned - (`core/src/verifier.rs`)

### Summary
The payout transaction's user signature uses `SinglePlusAnyoneCanPay`, which commits only to the input being spent and the single corresponding output (index 0). It does not commit to output 1 (anchor) or output 2 (OP_RETURN with the operator's `xonly_pk`). This lets a third party rebroadcast a malleated variant of the same payout tx, keeping the signed input/output-0 pair intact but stripping or corrupting the OP_RETURN output, so `update_finalized_payouts` records `operator_xonly_pk = None`, which then causes `is_kickoff_malicious` to unconditionally return `true` for the honest operator's kickoff, triggering a Challenge and burning the operator's collateral.

### Finding Description
The binding `Verifier::is_kickoff_malicious` relies on is: `operator_xonly_pk` recovered from the *finalized* payout transaction's OP_RETURN (stored via `update_finalized_payouts`) `== kickoff_data.operator_xonly_pk` of the operator who funded the payout [1](#0-0) .

The payout tx is built by `create_payout_txhandler`, which has one input (the user's withdrawal UTXO, key-path spent with the user's `SinglePlusAnyoneCanPay` signature) and three outputs: user payout (index 0), anchor (index 1), and the operator's OP_RETURN xonly_pk (index 2) [2](#0-1) . The user signature is verified only against the input-0/output-0 sighash [3](#0-2) . Because `SinglePlusAnyoneCanPay` (BIP-341 `SIGHASH_SINGLE|ANYONECANPAY`) only commits to the signed input and its same-index output, outputs 1 and 2 (anchor, OP_RETURN) are unsigned by the user and can be freely altered by anyone who has seen the broadcast transaction/witness, as long as output 0 and input 0 are preserved and the taproot key-path witness (a 64/65-byte Schnorr signature) remains structurally valid for the new transaction. The operator does append its own wallet inputs for fees via `fund_raw_transaction`/`sign_raw_transaction_with_wallet` [4](#0-3) , but these operator-owned inputs and any change output are also not bound to a single, non-malleable structure by the user's ANYONECANPAY signature — a third party can construct a replacement transaction spending the same withdrawal UTXO input (reusing the extracted user witness), with the exact same output 0, but omit or corrupt the OP_RETURN output (output 2), and get it confirmed instead (e.g. with higher fee/priority), given `ANYONECANPAY` explicitly permits input additions/removals by anyone besides the signer of that one input.

`update_finalized_payouts` then reads whichever payout transaction actually confirmed for that withdrawal UTXO, extracts `get_first_op_return_output`/`parse_op_return_data`, and if it fails to find a valid xonly_pk, stores `operator_xonly_pk = None` in the DB for that payout [5](#0-4) . `is_kickoff_malicious` then hits the `None` branch and returns `true` unconditionally [6](#0-5) , regardless of whether the kickoff's `kickoff_data.operator_xonly_pk` correctly identifies the operator who genuinely fronted the withdrawal (verified earlier by the operator against the user's signature before broadcasting). This causes `handle_kickoff` to treat a legitimate operator kickoff as malicious and send a Challenge, burning that operator's collateral.

No existing guard prevents this: `is_deposit_valid`, `is_profitable`, and `SECP.verify_schnorr` only validate that the *original* payout tx the operator constructed had a correct signature and profitable amount at broadcast time — none of them re-check or pin down the final on-chain OP_RETURN content against a malleation-resistant commitment, and the sighash type accepted (`SinglePlusAnyoneCanPay`) is exactly the type that leaves the OP_RETURN unauthenticated.

### Impact Explanation
An operator that genuinely fronted a withdrawal loses its collateral (burned via the Challenge path) purely because a third party altered non-signed outputs of the confirmed payout transaction. This is repeatable for every withdrawal processed with the vulnerable sighash type, across any operator and deposit, since the flaw is structural to `create_payout_txhandler`'s signature scope and not particular to any single deposit. This matches the Critical category "an honest operator's collateral burned."

### Likelihood Explanation
Preconditions: a withdrawal must be registered/broadcast with `SinglePlusAnyoneCanPay` (the only sighash type accepted by `parse_withdrawal_sig_params`, which the protocol enforces/normalizes to for all withdrawals — so every withdrawal is exposed) [7](#0-6) . The attacker needs only to observe the operator's broadcast (mempool) payout transaction, extract the witness, and race a replacement transaction before confirmation — a standard, low-cost third-party transaction malleation requiring no special privileges, keys, or collateral, only the ability to pay fees to get their variant confirmed instead. This is feasible with a mempool-monitoring bot and is repeatable across every payout an operator makes.

### Recommendation
Change the payout transaction's user signature to a sighash type that commits to all outputs (e.g. `SIGHASH_ALL` or `SIGHASH_ALL|ANYONECANPAY` for the single spent input, or otherwise cryptographically commit the OP_RETURN/operator xonly_pk content within the signed message) so the OP_RETURN output cannot be stripped or altered without invalidating the user's signature. Alternatively, bind the operator's identity to the payout via a mechanism that doesn't depend on the OP_RETURN of the finally-confirmed transaction (e.g., require the operator's own signature over the full payout tx including OP_RETURN, or derive the operator binding from the kickoff's committed data rather than trusting the mutable on-chain OP_RETURN).

### Proof of Concept
```
cargo test <bridge_e2e_test> that:
1. Registers a withdrawal and generates the user's SinglePlusAnyoneCanPay signature over
   (withdrawal_utxo, payout_output) via generate_withdrawal_transaction_and_signature.
2. Operator calls withdraw(...) to build+broadcast payout_tx via create_payout_txhandler,
   containing: output0 = user payout, output1 = anchor, output2 = OP_RETURN(operator_xonly_pk).
   Assert operator_xonly_pk_from_op_return(payout_tx) == operator.signer.xonly_public_key.
3. Before confirmation, construct a malleated_payout_tx: same input (withdrawal_utxo) with the
   same extracted witness, same output0, but drop/corrupt output2 (OP_RETURN), add attacker's
   own fee input/change. Broadcast and confirm malleated_payout_tx instead of the original.
4. Run Citrea sync / update_finalized_payouts against the block containing malleated_payout_tx.
   Assert DB payout_info.operator_xonly_pk == None (binding broken: recovered pk != operator's
   real kickoff_data.operator_xonly_pk, which is Some(operator_pk)).
5. Operator sends kickoff with kickoff_data.operator_xonly_pk == operator.signer.xonly_public_key.
   Call Verifier::is_kickoff_malicious(...) and assert it returns true, proving the honest
   operator gets challenged solely due to third-party OP_RETURN malleation.
```

### Citations

**File:** core/src/verifier.rs (L1882-1890)
```rust
        let Some(operator_xonly_pk) = operator_xonly_pk_opt else {
            tracing::warn!("No operator xonly pk found in payout tx OP_RETURN, assuming malicious");
            return Ok(true);
        };

        if operator_xonly_pk != kickoff_data.operator_xonly_pk {
            tracing::warn!("Operator xonly pk for the payout does not match with the kickoff_data");
            return Ok(true);
        }
```

**File:** core/src/verifier.rs (L2312-2328)
```rust
            // Find the first output that contains OP_RETURN
            let circuit_payout_tx = CircuitTransaction::from(payout_tx.clone());
            let op_return_output = get_first_op_return_output(&circuit_payout_tx);

            // If OP_RETURN doesn't exist in any outputs, or the data in OP_RETURN is not a valid xonly_pubkey,
            // operator_xonly_pk will be set to None, and the corresponding column in DB set to NULL.
            // This can happen if optimistic payout is used, or an operator constructs the payout tx wrong.
            let operator_xonly_pk = op_return_output
                .and_then(|output| parse_op_return_data(&output.script_pubkey))
                .and_then(|bytes| XOnlyPublicKey::from_slice(bytes).ok());

            if operator_xonly_pk.is_none() {
                tracing::info!(
                    "No valid operator xonly pk found in payout tx {:?} OP_RETURN. Either it is an optimistic payout or the operator constructed the payout tx wrong",
                    payout_txid
                );
            }
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

**File:** core/src/operator.rs (L620-637)
```rust
        let payout_txhandler = builder::transaction::create_payout_txhandler(
            input_utxo,
            output_txout,
            self.signer.xonly_public_key,
            in_signature,
            self.config.protocol_paramset().network,
        )?;

        // tracing::info!("Payout txhandler: {:?}", hex::encode(bitcoin::consensus::serialize(&payout_txhandler.get_cached_tx())));

        let sighash = payout_txhandler.calculate_sighash_txin(0, in_signature.sighash_type)?;

        SECP.verify_schnorr(
            &in_signature.signature,
            &Message::from_digest(*sighash.as_byte_array()),
            user_xonly_pk,
        )
        .wrap_err("Failed to verify signature received from user for payout txin. Ensure the signature uses SinglePlusAnyoneCanPay sighash type.")?;
```

**File:** core/src/operator.rs (L651-681)
```rust
        // send payout tx using RBF
        let funded_tx = self
            .rpc
            .fund_raw_transaction(
                payout_txhandler.get_cached_tx(),
                Some(&bitcoincore_rpc::json::FundRawTransactionOptions {
                    add_inputs: Some(true),
                    include_unsafe: Some(false),
                    change_address: None,
                    change_position: Some(1),
                    change_type: None,
                    include_watching: None,
                    lock_unspents: Some(false),
                    fee_rate: Some(Amount::from_sat(fee_rate.to_sat_per_kvb())),
                    subtract_fee_from_outputs: None,
                    replaceable: None,
                    conf_target: None,
                    estimate_mode: None,
                }),
                None,
            )
            .await
            .wrap_err("Failed to fund raw transaction")?
            .hex;

        let signed_tx = self
            .rpc
            .sign_raw_transaction_with_wallet(&funded_tx, None, None)
            .await
            .wrap_err("Failed to sign withdrawal transaction")?
            .hex;
```

**File:** core/src/rpc/parser/operator.rs (L170-187)
```rust
    // If the Taproot sighash type is Default (no explicit type attached; i.e. a 64-byte
    // signature without a sighash flag), normalize it to SinglePlusAnyoneCanPay.
    // Prior to v0.5 this was Clementine's implicit behavior; we retain it here for
    // backwards compatibility when a 64-byte signature is provided.
    if input_signature.sighash_type == TapSighashType::Default {
        tracing::warn!(
            "Input signature for withdrawal {} has sighash type default, setting to SinglePlusAnyoneCanPay", params.withdrawal_id,
        );
        input_signature.sighash_type = TapSighashType::SinglePlusAnyoneCanPay;
    }

    // enforce sighash type here
    if input_signature.sighash_type != TapSighashType::SinglePlusAnyoneCanPay {
        return Err(Status::invalid_argument(format!(
            "Input signature has wrong sighash type, SinglePlusAnyoneCanPay expected, got {}",
            input_signature.sighash_type
        )));
    }
```
