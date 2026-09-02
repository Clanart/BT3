### Title
Payout transaction's operator-credit OP_RETURN is not covered by the user's SIGHASH_SINGLE|ANYONECANPAY signature, letting anyone reassign "who fronted the withdrawal" credit - (File: `core/src/builder/transaction/operator_reimburse.rs`)

### Summary
### Finding Description
`create_payout_txhandler` builds the payout transaction that an operator broadcasts to front a Citrea withdrawal. The single input is the user's dust UTXO, signed with `SinglePlusAnyoneCanPay`: [1](#0-0) 

Because the sighash type is `SIGHASH_SINGLE|ANYONECANPAY`, the user's signature commits **only** to input index 0 and the output at the same index (the user payout output). It does **not** commit to the anchor output or, critically, to the OP_RETURN output that carries `operator_xonly_pk` (the value used later to decide which operator gets credited/reimbursed): [2](#0-1) 

Later, when the payout transaction confirms, the verifier scans the block, extracts whatever xonly public key is present in that OP_RETURN, and stores it as the "payer" for that withdrawal: [3](#0-2) 

This attribution is used verbatim, with no cryptographic link back to who actually supplied the withdrawal funds, by `PayoutCheckerTask`, which automatically triggers the reimbursement/kickoff flow for whichever operator's key matches the stored value: [4](#0-3) 

And by `validate_payer_is_operator`, which only allows the operator whose key is stored in the DB to claim reimbursement for that withdrawal: [5](#0-4) 

Because the OP_RETURN is unsigned data, anyone who observes an operator's unconfirmed payout transaction in the mempool (the tx and its witness are public once broadcast) can build a *different* transaction that reuses the same signed input/output (satisfying the same `SIGHASH_SINGLE|ANYONECANPAY` signature), supplies their own funding for the withdrawal output and anchor, and writes an arbitrary `operator_xonly_pk` into the OP_RETURN. If this replacement transaction confirms instead of the original, the withdrawal's "payer" attribution recorded on-chain has no necessary relationship to whoever actually funded it: the constructing party can name themselves, a completely different operator, or a non-participating key.

This breaks exactly the "operator credited versus the party that paid" binding: the DB's `payout_payer_operator_xonly_pk` is derived solely from unauthenticated OP_RETURN bytes, not from a signature proving that key's owner supplied the payout funds.

### Impact Explanation
- If operator A broadcasts a payout intending to front a withdrawal and get reimbursed, and a rival replaces it (before confirmation) with a transaction that reuses A's signed input/output but funds it itself and writes its own (or a third party's) key into the OP_RETURN, then A's on-chain payout never confirms, and A is never recorded as `payout_payer_operator_xonly_pk`. If a functionally equivalent tx confirms attributing payment to another operator, `validate_payer_is_operator` will reject A's later `GetReimbursementTxs`/kickoff attempt ("Payer is not own operator for deposit"), permanently denying A any path to reimbursement for that withdrawal even though A may have already spent resources or intended to front it.
- Conversely, whoever controls the confirmed transaction's OP_RETURN can direct the reimbursement/kickoff credit to any operator key of their choosing (including one that did not construct or fund the transaction at all, since the field is fully attacker-controlled data, not a signature). This causes `PayoutCheckerTask` for that named operator to automatically start the kickoff/reimbursement process for a payout that operator did not actually construct or authorize, consuming that operator's kickoff connectors/collateral resources for a transaction outside its control.
- This matches the Critical-tier impacts "an operator reimbursed for a payout it never funded" and "an honest operator permanently unable to be reimbursed."

### Likelihood Explanation
Exploitation requires no privileged role: the withdrawal's `SinglePlusAnyoneCanPay` signature and the entire unconfirmed transaction become visible to any mempool observer once any party (operator or otherwise) broadcasts the payout. Constructing a replacement transaction that reuses the same signed input/output while substituting funding inputs and OP_RETURN bytes is straightforward Bitcoin transaction construction, no signature forgery is needed. The only cost to an attacker is funding the withdrawal amount itself (as any operator would), but the credit-misattribution consequence (denying the original fronting operator/rewarding an unrelated key) is unrelated to that cost and always reachable given a race on an unconfirmed payout.

### Recommendation
Commit the reimbursement-attribution data to the same signature that authorizes the withdrawal, e.g. by using a sighash mode that covers all outputs (or at least the OP_RETURN output) of the payout transaction, or by having verifiers additionally require a fresh N-of-N/aggregator co-signature over the OP_RETURN operator key before accepting a payout as attributable, rather than trusting bare OP_RETURN bytes parsed from the confirmed transaction.

### Proof of Concept
1. Operator A prepares and broadcasts `payout_tx_A`: input = user's dust UTXO with `SinglePlusAnyoneCanPay` signature (per `create_payout_txhandler`), output 0 = user payout, output 1 = anchor, output 2 = OP_RETURN(`A.xonly_pk`), funded by A's additional inputs.
2. Before `payout_tx_A` confirms, an observer copies the signed input/output-0 witness data from the mempool and constructs `payout_tx_B`: same input 0 (same signature, valid because SIGHASH_SINGLE|ANYONECANPAY only commits input 0/output 0), same output 0, but output 2 = OP_RETURN(`B.xonly_pk`) and its own funding inputs for output 0's amount/fees.
3. `payout_tx_B` is broadcast with a higher fee/faster propagation and confirms instead of `payout_tx_A`.
4. `update_finalized_payouts` (core/src/verifier.rs:2312-2343) parses `payout_tx_B`'s OP_RETURN and stores `payout_payer_operator_xonly_pk = B.xonly_pk` for that withdrawal index.
5. `PayoutCheckerTask` for operator B automatically begins the kickoff/reimbursement flow for a payout B did not sign or intend, while operator A's later `GetReimbursementTxs`/kickoff attempt fails `validate_payer_is_operator` because the DB attributes the payout to B, not A.

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

**File:** core/src/verifier.rs (L2312-2343)
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

            tracing::info!(
                "A new payout tx detected for withdrawal {}, payout txid: {:?}, operator xonly pk: {:?}",
                idx,
                payout_txid,
                operator_xonly_pk
            );

            payout_txs_and_payer_operator_idx.push((
                idx,
                payout_txid,
                operator_xonly_pk,
                block_hash,
            ));
        }
```

**File:** core/src/task/payout_checker.rs (L39-47)
```rust
    async fn run_once(&mut self) -> Result<Self::Output, BridgeError> {
        let mut dbtx = self.db.begin_transaction().await?;
        let unhandled_payout = self
            .db
            .get_first_unhandled_payout_by_operator_xonly_pk(
                Some(&mut dbtx),
                self.operator.signer.xonly_public_key,
            )
            .await?;
```

**File:** core/src/operator.rs (L1687-1729)
```rust
    async fn validate_payer_is_operator(
        &self,
        dbtx: Option<DatabaseTransaction<'_>>,
        deposit_id: u32,
    ) -> Result<(BlockHash, Txid), BridgeError> {
        let (payer_xonly_pk, payout_blockhash, kickoff_txid) = self
            .db
            .get_payer_xonly_pk_blockhash_and_kickoff_txid_from_deposit_id(dbtx, deposit_id)
            .await?;

        tracing::info!(
            "Payer xonly pk and kickoff txid found for the requested deposit, payer xonly pk: {:?}, kickoff txid: {:?}",
            payer_xonly_pk,
            kickoff_txid
        );

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
