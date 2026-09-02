### Title
Payout attribution (OP_RETURN operator pubkey) is unauthenticated, allowing an unprivileged party to hijack or destroy operator reimbursement credit - ([File: core/src/builder/transaction/operator_reimburse.rs])

### Summary
`create_payout_txhandler` builds the operator's payout transaction with a `KeySpend` witness signed only with the user's pre-obtained signature, which is explicitly documented and enforced to use `SIGHASH_SINGLE | ANYONECANPAY`. This sighash type does not commit to the anchor output or the OP_RETURN output that records which operator "fronted" the withdrawal. Because that attribution field is unsigned, anyone who observes the unconfirmed payout transaction (or otherwise obtains the witness) can rebuild a transaction spending the exact same input with the exact same user-signed output, but with an arbitrary or missing OP_RETURN payload, and get it confirmed instead. This breaks the equality that should hold: `operator credited in OP_RETURN == operator who actually funded/broadcast the payout`.

### Finding Description
`create_payout_txhandler` (core/src/builder/transaction/operator_reimburse.rs:407-436) constructs the payout tx as:
- Input 0: withdrawal UTXO, `SpendPath::KeySpend`, witness set via `set_p2tr_key_spend_witness(&user_sig, 0)`
- Output 0: user payout
- Output 1: anchor
- Output 2: OP_RETURN containing `operator_xonly_pk` (the party claiming to have fronted the withdrawal) [1](#0-0) 

In `Operator::withdraw` (core/src/operator.rs:620-637), the code explicitly documents and requires this signature to use `SinglePlusAnyoneCanPay`:
```
.wrap_err("Failed to verify signature received from user for payout txin. Ensure the signature uses SinglePlusAnyoneCanPay sighash type.")
``` [2](#0-1) 

`SIGHASH_SINGLE | ANYONECANPAY` only commits the input being spent to the *output at the same index* (output 0, the user payout) — it does not commit to output 1 (anchor) or output 2 (OP_RETURN operator attribution), and `ANYONECANPAY` allows arbitrary other inputs to be added/removed without invalidating the signature. The transaction is then funded via `fund_raw_transaction` with `add_inputs: true` and RBF (core/src/operator.rs, funding logic following line 637), so it circulates unconfirmed with a publicly-visible witness before being mined.

Downstream, the OP_RETURN payload is the *sole* source of attribution used by the protocol to determine who gets reimbursed:
- `update_finalized_payouts` parses the OP_RETURN xonly pubkey from the mined payout tx and stores it as `payout_payer_operator_xonly_pk` (NULL if missing/invalid) [3](#0-2) 
- `validate_payer_is_operator` requires payer_xonly_pk, payout_blockhash, and kickoff_txid to all be `Some` and match the requesting operator, otherwise it hard-errors with "Payer info not found for deposit" [4](#0-3) 
- `is_kickoff_malicious` treats a missing/mismatched operator xonly pk as proof the kickoff is malicious [5](#0-4) 

Because the OP_RETURN is not covered by any signature, any observer of the mempool can:
1. Take the honest operator's broadcast payout tx (input, user signature, output 0 unchanged).
2. Rebuild a competing/higher-fee transaction with the same input/signature/output-0, but a different OP_RETURN (their own xonly pubkey, or garbage/no OP_RETURN at all), and their own fee-paying inputs (allowed via ANYONECANPAY).
3. Get this transaction confirmed first (front-running/malleability), since output 0 (the actual user payment) is unaffected, the user's withdrawal completes normally, but the reimbursement attribution is hijacked or destroyed.

This directly breaks the binding "the operator credited versus the party that paid" listed as an in-scope custody-binding violation.

### Impact Explanation
- If the attacker substitutes another *registered* operator's xonly pubkey (e.g. a competing operator griefing another), the wrong operator becomes eligible for reimbursement via `get_first_unhandled_payout_by_operator_xonly_pk` and `handle_finalized_payout`, even though they never funded the withdrawal, while the operator who actually incurred the cost of paying the user can never satisfy `validate_payer_is_operator` (their pubkey no longer matches) and is permanently unable to claim reimbursement. This matches the Critical impact "an honest operator permanently unable to be reimbursed."
- If the attacker (who need not be a registered operator at all — any unprivileged party with mempool visibility) removes or garbles the OP_RETURN, `payout_payer_operator_xonly_pk` becomes NULL, the payout is never attributed to any operator, `get_first_unhandled_payout_by_operator_xonly_pk` never surfaces it for the true payer, and `is_kickoff_malicious`/`validate_payer_is_operator` treat any subsequent kickoff for that deposit as malicious or unmatched — permanently freezing the honest operator's reimbursement path for that withdrawal, again a Critical-severity outcome per the given rubric.
- No verifier, operator, watchtower, aggregator, or key-compromise privilege is required by the attacker — only the ability to observe an unconfirmed transaction in the mempool and broadcast a competing one with a higher fee (or win the propagation race), which is a standard unprivileged blockchain capability.

### Likelihood Explanation
Every payout transaction (operator-fronted, non-optimistic path) is signed this way, so the vulnerable window (input signed, tx unconfirmed in mempool) occurs on every withdrawal. Exploitation requires only mempool monitoring and constructing a standard PSBT reusing the leaked SIGHASH_SINGLE|ANYONECANPAY witness plus adding one's own funding inputs — a well-known Bitcoin technique, not requiring any protocol-level access. The main variable is the race window before confirmation, but this is a realistic and repeatable attack surface, not merely theoretical.

### Recommendation
Do not rely on `SIGHASH_SINGLE | ANYONECANPAY` for a transaction whose non-input-adjacent outputs (anchor, and especially the OP_RETURN operator-attribution output) are security-critical. Either:
- Require the withdrawal pre-signature to use `SIGHASH_ALL` (or `SIGHASH_ALL | ANYONECANPAY` if additional funding inputs must remain permissible) so that the OP_RETURN operator attribution output is committed to by the user's signature, or
- Move operator attribution out of an unsigned output and into a covenant/witness structure that is itself bound by a signature or script commitment that cannot be altered while preserving the original signature's validity (e.g., commit the operator pubkey into the sighash-covered input's script-path spend conditions instead of a separate OP_RETURN output).

### Proof of Concept
1. Operator O1 receives a user-presigned `SIGHASH_SINGLE|ANYONECANPAY` signature for withdrawal UTXO `W`, and constructs+broadcasts `payout_tx_1` via `Operator::withdraw`/`create_payout_txhandler`, with output 0 = user payment, output 2 = OP_RETURN(O1's xonly pubkey).
2. Before `payout_tx_1` confirms, an unprivileged observer extracts the witness (signature) for input 0 from the mempool.
3. The observer constructs `payout_tx_2` reusing the same input `W` and signature, keeping output 0 identical (same script_pubkey/amount, satisfying `SIGHASH_SINGLE`), but changes/removes output 2 (OP_RETURN), and funds it with its own fee inputs (allowed by `ANYONECANPAY`), using a higher fee rate.
4. `payout_tx_2` replaces/out-races `payout_tx_1` and gets mined.
5. `update_finalized_payouts` (core/src/verifier.rs:2283-2353) records `payout_payer_operator_xonly_pk` as NULL (or another operator's pubkey) for this withdrawal instead of O1's.
6. `validate_payer_is_operator` (core/src/operator.rs:1687-1740) subsequently rejects O1's reimbursement request ("Payer info not found for deposit" / operator mismatch), while O1 already paid the user out of pocket — permanent inability to be reimbursed.

### Citations

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

**File:** core/src/operator.rs (L1703-1729)
```rust
        // first check if the payer is the operator, and the kickoff is handled
        // by the PayoutCheckerTask, meaning kickoff_txid is set
        let (payout_blockhash, kickoff_txid) = match (
            payer_xonly_pk,
            payout_blockhash,
            kickoff_txid,
        ) {
            (Some(payer_xonly_pk), Some(payout_blockhash), Some(kickoff_txid)) => {
                if payer_xonly_pk != self.signer.xonly_public_key {
                    return Err(eyre::eyre!(
                        "Payer is not own operator for deposit, payer xonly pk: {:?}, operator xonly pk: {:?}",
                        payer_xonly_pk,
                        self.signer.xonly_public_key
                    )
                    .into());
                }
                (payout_blockhash, kickoff_txid)
            }
            _ => {
                return Err(eyre::eyre!(
                    "Payer info not found for deposit, payout blockhash: {:?}, kickoff txid: {:?}",
                    payout_blockhash,
                    kickoff_txid
                )
                .into());
            }
        };
```

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
