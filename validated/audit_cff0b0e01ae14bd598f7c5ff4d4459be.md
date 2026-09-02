### Title
Payout attribution to an operator relies on an unauthenticated OP_RETURN, letting anyone frame or credit an operator that never fronted the withdrawal - (File: core/src/verifier.rs)

### Summary
The system determines *which operator gets reimbursement rights* for a Bitcoin withdrawal purely by scanning the confirmed payout transaction's OP_RETURN output for an x-only pubkey, with no cryptographic binding between that pubkey and the party who actually paid (signed/funded) the payout. This is the same class of bug as the source report: a value meant to be attributed to one entity (the right branch/the fronting operator) is instead silently attached to, or divorced from, the wrong holder, because the code trusts a plain data field instead of verifying provenance.

### Finding Description
`create_payout_txhandler` builds the payout transaction with the operator's x-only pubkey embedded in a bare `OP_RETURN` output (`op_return_txout(PushBytesBuf::from(operator_xonly_pk.serialize()))`), which carries no signature or commitment tying it to the operator's key: [1](#0-0) 

Only the user's UTXO input is signed (`user_sig`, key-spend witness at input 0); the OP_RETURN output is not covered by any binding signature from the operator or a verifier: [2](#0-1) 

Later, when verifiers process the withdrawal's spending transaction on-chain, `update_finalized_payouts` extracts the operator attribution **solely** from this OP_RETURN, explicitly acknowledging it can be arbitrary or absent ("operator constructed the payout tx wrong"), and writes it straight into the DB as the payer-of-record: [3](#0-2) 

This DB record is subsequently trusted as ground truth for reimbursement decisions: `validate_payer_is_operator` uses it to authorize which operator may claim reimbursement (`payer_xonly_pk != self.signer.xonly_public_key` check), and `is_kickoff_malicious` uses it to determine whether an operator's kickoff is legitimate: [4](#0-3) [5](#0-4) 

Because the withdrawal UTXO's outpoint, required output script/amount are all public (fetched from Citrea state via `get_withdrawal_utxo_from_citrea_withdrawal`), and the only signature required is the user's ANYONECANPAY-style signature over their own input/output, **any unprivileged party who obtains the user's signed payout input** (e.g., by observing the mempool, or by being handed it off-chain as any operator would be) can assemble their own transaction spending the same UTXO with the mandated user output, but stamp a different x-only pubkey into the OP_RETURN - crediting an arbitrary operator (honest or malicious) with having "fronted" the peg-out, without that operator's involvement or funds.

This breaks the intended binding: **operator credited (via OP_RETURN) == operator who actually paid (broadcast/funded the payout tx)**. Before the attack, only the operator who funds/broadcasts a payout tx is recorded as payer. After the attack, an attacker can insert an arbitrary/victim operator's pubkey as the payer of record while that operator never spent any funds, or can prevent an honest operator's real payout from being properly attributed by front-running with a rival OP_RETURN.

### Impact Explanation
If an attacker can get the withdrawal UTXO spent (with the user's off-chain signature) by broadcasting a variant transaction naming a victim operator in the OP_RETURN before the real operator's broadcast:
- The victim operator is marked in the DB as "payer" for a withdrawal they never funded (`payout_payer_operator_xonly_pk`), and `validate_payer_is_operator`/`get_reimbursement_txs` will treat them as entitled to reimbursement even though they fronted nothing — this can also mean the honest operator who actually would have paid is *shut out* since Citrea's withdrawal UTXO is already spent, so they cannot obtain the reimbursement path for funds they may separately try to front. Conversely, the real payer (attacker) is unattributed/None, going through the "optimistic payout"/no-operator branch, at the operator's/protocol's expense.
This matches the report's "operator credited versus the party that paid" custody-binding violation, potentially causing an honest operator to be permanently unable to be reimbursed for a payout, or an operator being wrongly credited for reimbursement it never funded.

### Likelihood Explanation
The withdrawal outpoint, required script pubkey/amount are all derived from Citrea (public L2 state) and are not secret. The user's payout signature is shared "off-chain" with operators per the code comments, but nothing in the protocol restricts who can hold or use that signature once it exists (it is not bound to a specific operator's identity). Any party capable of observing/obtaining a valid signed withdrawal input (which is by design shared broadly enough for any operator to redeem it) can rebroadcast a competing transaction with a forged OP_RETURN before the legitimate operator's transaction confirms. This requires no privileged role (verifier, watchtower, security council) — it is purely an unprivileged network participant race.

### Recommendation
Do not trust the raw OP_RETURN pubkey as attribution. Require verifiers to bind the operator identity into a signed/committed structure — e.g. have verifiers or the aggregator co-sign (or presign per-operator with a Musig2/BitVM commitment) the specific payout transaction template including the OP_RETURN, or otherwise cryptographically tie the OP_RETURN payload to a signature from the claiming operator (e.g., sign the sighash including the OP_RETURN output with the operator's own key rather than only the user's ANYONECANPAY signature over the withdrawal input/output). Additionally, treat "first valid spend wins" carefully: reconcile the DB attribution logic in `update_finalized_payouts` to reject/flag payouts whose OP_RETURN pubkey cannot be corroborated by an operator-signed commitment for that specific withdrawal.

### Proof of Concept
Conceptual (not executed, no test harness access in this pass):
1. Observe a pending withdrawal `w` with known `withdrawal_utxo`, `output_script_pubkey`, `output_amount` (public via Citrea state / `WithdrawParams`), and obtain the user's off-chain `input_signature` (as any operator could, since it's provided to "operators" broadly per the code comment, and is not bound to a specific operator key).
2. Attacker (or a rival operator) constructs their own payout transaction using `create_payout_txhandler` with the same `input_utxo`/`output_txout`/`user_sig`, but supplies an arbitrary `operator_xonly_pk` (e.g., victim operator B's key) instead of their own (operator A's) key.
3. Attacker broadcasts this transaction before operator B's or the legitimate payer's transaction.
4. Once finalized, `update_finalized_payouts` reads the OP_RETURN and records operator B as `payout_payer_operator_xonly_pk` in the `withdrawals` table (see `core/src/verifier.rs:2319-2321,2345-2349`), even though operator B never funded or signed this specific broadcast.
5. `validate_payer_is_operator`/`get_reimbursement_txs` (core/src/operator.rs:1686-1740, 2098-2150) will subsequently treat operator B as authorized payer for the deposit's reimbursement flow, while B never actually put up the funds — the binding "operator credited == operator who paid" is broken.

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

**File:** core/src/operator.rs (L1686-1729)
```rust
    /// For a deposit_id checks that the payer for that deposit is the operator, and the payout blockhash and kickoff txid are set.
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
