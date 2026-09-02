Based on my investigation, I found a valid analog that matches the bug class described in the report — a binding that should hold but is broken due to unauthenticated data in a signed transaction.

### Title
Unauthenticated OP_RETURN operator identity in payout_tx allows misattribution of reimbursement (front-running the fee-payer) - (File: core/src/builder/transaction/operator_reimburse.rs)

### Summary
The `payout_tx` construction signs the user's withdrawal input with `TapSighashType::SinglePlusAnyoneCanPay`, which — per BIP341 — only commits the input's own signature to output index 0. The second output (OP_RETURN containing the operator's x-only pubkey, used later to attribute/credit reimbursement) is **not covered by the user's signature**, since `SIGHASH_SINGLE` only binds to the sole output whose index matches the signing input's index (index 0), leaving all subsequent outputs unauthenticated.

### Finding Description
`create_payout_txhandler` builds a 3-output transaction: (1) user payout, (2) anchor, (3) OP_RETURN with `operator_xonly_pk`. [1](#0-0) 
The single witness signature is validated with `TapSighashType::SinglePlusAnyoneCanPay`: [2](#0-1) 
and the sighash type is strictly enforced to be `SinglePlusAnyoneCanPay`: [3](#0-2) 

With `SIGHASH_SINGLE`, BIP341/legacy semantics only commit to the output at the *same index* as the signed input (index 0, the user payout output). The anchor output (index 1) and, critically, the OP_RETURN output (index 2) carrying the `operator_xonly_pk` are outside the commitment. Because `ANYONECANPAY` is also set, the transaction's other inputs aren't constrained either.

Downstream, the verifier reads back this exact OP_RETURN data from the confirmed transaction to determine who gets credited as payer/operator for reimbursement: [4](#0-3) 
That `operator_xonly_pk` is stored and later used by `validate_payer_is_operator` to gate which operator is authorized to claim the kickoff/reimburse flow: [5](#0-4) 

Since the OP_RETURN is not covered by the signature, an unprivileged party who observes an unconfirmed `payout_tx` in the mempool (with the legitimate operator's key spend, output 0 unchanged) can reconstruct and rebroadcast (or RBF, if sequence allows) a variant transaction with the **same input, same signature, same output[0]**, but a **different OP_RETURN payload** naming a different operator's `xonly_pk` — without invalidating the SIGHASH_SINGLE|ANYONECANPAY signature.

### Impact Explanation
If the substituted transaction confirms instead of (or before) the original, `update_finalized_payouts` records the attacker-chosen `operator_xonly_pk` as the payer. Consequently, that operator (not the one who actually funded the user's payout) becomes eligible via `validate_payer_is_operator` / `handle_finalized_payout` to claim the kickoff and Reimburse transaction, i.e., to receive the bridge's reimbursement for a payout it never funded — matching the report's "operator credited versus the party that paid" custody-binding violation, classified Critical in the rules (operator reimbursed for a payout it never funded), while the operator that actually paid the user is left unable to be reimbursed for its fronted BTC.

### Likelihood Explanation
Exploitation requires only mempool visibility of the broadcast `payout_tx` (or knowledge of the withdrawal signature, which per the code comments is shared with multiple operators via the aggregator's `Withdraw` RPC across `operator_xonly_pks`), and the ability to broadcast a competing transaction with equal or higher fee before the original confirms — no privileged role, key compromise, or majority hashrate is needed. This is a pure malleability/attribution bug rooted in sighash-type semantics rather than a race condition dependent on infrastructure weaknesses.

### Recommendation
Do not rely on unauthenticated transaction data (OP_RETURN) for financial attribution. Either:
- Change the payout signature's sighash type to `AllPlusAnyoneCanPay` (or `Default`) so it commits to all outputs including the OP_RETURN, or
- Bind the operator identity into data that IS covered by the signature (e.g., have the user sign a message/commitment that includes the specific operator's xonly pubkey, verified independently of the on-chain OP_RETURN), or
- Cross-check the confirmed payout's OP_RETURN operator pubkey against the operator that actually possesses/registered the corresponding withdrawal signature off-chain before crediting reimbursement eligibility.

### Proof of Concept
1. Operator A calls `Withdraw`/`InternalWithdraw`, producing `payout_tx` with output0 = user payout, output1 = anchor, output2 = OP_RETURN(A's xonly_pk), signed by the user with `SinglePlusAnyoneCanPay`. [6](#0-5) 
2. Attacker observes `payout_tx` in mempool, extracts the witness (signature + output0), and constructs `payout_tx'` with identical input/output0/witness, but replaces output2 with OP_RETURN(operator B's xonly_pk) — the signature remains valid since output2 is not covered by `SIGHASH_SINGLE`.
3. Attacker broadcasts `payout_tx'` with a higher fee (or RBFs it), and it confirms instead of the original.
4. `update_finalized_payouts` parses `operator_xonly_pk = B` from the confirmed OP_RETURN. [7](#0-6) 
5. `validate_payer_is_operator` for operator B's `handle_finalized_payout` call now succeeds, letting B claim kickoff/reimbursement for a payout A actually funded. [8](#0-7)

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

**File:** core/src/operator.rs (L1703-1719)
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
```

**File:** core/src/rpc/parser/operator.rs (L180-187)
```rust

    // enforce sighash type here
    if input_signature.sighash_type != TapSighashType::SinglePlusAnyoneCanPay {
        return Err(Status::invalid_argument(format!(
            "Input signature has wrong sighash type, SinglePlusAnyoneCanPay expected, got {}",
            input_signature.sighash_type
        )));
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
