### Title
Payout attribution is unauthenticated, allowing a payout to be minted for an operator who never funded it - ([File: core/src/builder/transaction/operator_reimburse.rs])

### Summary
`create_payout_txhandler` builds the operator payout transaction using a user-supplied `SIGHASH_SINGLE|ANYONECANPAY` signature that commits only to input 0 and output 0 (the user payout). The OP_RETURN output that records *which operator fronted the withdrawal* (`operator_xonly_pk`) sits at output index 2 and is completely outside the signed message. Since the transaction is broadcast to the public Bitcoin network (`send_raw_transaction`), any unprivileged network observer can pull the user's signature and the withdrawal UTXO out of the broadcast/mempool transaction and construct a competing transaction that reuses the same signed input/output but substitutes an arbitrary `operator_xonly_pk` in the OP_RETURN — crediting a completely uninvolved operator for a payout that operator never funded.

### Finding Description
`create_payout_txhandler` [1](#0-0)  constructs the payout transaction with:
- input 0: the withdrawal UTXO, spent with the user's `taproot::Signature` via key-path spend
- output 0: the user payout
- output 1: anchor
- output 2: an OP_RETURN carrying `operator_xonly_pk` — the attribution field

The witness is set with `set_p2tr_key_spend_witness(&user_sig, 0)` [2](#0-1) . `parse_withdrawal_sig_params` enforces the sighash type to be `SinglePlusAnyoneCanPay` [3](#0-2) . Under `SIGHASH_SINGLE | ANYONECANPAY`, the signature commits only to the single input being spent and the output at the *same index* — it does not commit to any other inputs, nor to outputs at other indices (including the OP_RETURN at index 2). `Operator::withdraw` then funds, signs, and broadcasts this transaction directly to the Bitcoin network via `sign_raw_transaction_with_wallet` / `send_raw_transaction` [4](#0-3) , making the user signature and withdrawal outpoint publicly observable in the mempool before confirmation.

On the verifier side, `update_finalized_payouts` trusts this unsigned OP_RETURN as the sole source of truth for who fronted the payout: it scans the confirmed payout transaction for an OP_RETURN and parses `operator_xonly_pk` from it, defaulting to `None` if absent/invalid [5](#0-4) , then persists it as `payout_payer_operator_xonly_pk` [6](#0-5) . This value is later used to authorize the reimbursement flow: `validate_payer_is_operator` checks only that `payer_xonly_pk == self.signer.xonly_public_key` [7](#0-6) , and `PayoutCheckerTask` looks up "first unhandled payout" filtered strictly by `payout_payer_operator_xonly_pk` [8](#0-7)  to automatically drive the kickoff/reimbursement transaction chain for that operator [9](#0-8) .

Because the OP_RETURN is never cryptographically bound to the signature that authorizes spending the withdrawal UTXO, an unprivileged party who observes the broadcast (or mempool) transaction can:
1. Reuse the same input 0 + user signature (still valid, since the sighash doesn't cover anything else) and same output 0 (payout, required to remain unchanged by SIGHASH_SINGLE),
2. Add their own funding inputs/change outputs (unconstrained by the signature),
3. Replace output 2's OP_RETURN with an arbitrary, uninvolved operator's `xonly_pk`,
4. Broadcast this variant so it confirms instead of (or as) the payout transaction.

This directly parallels the EigenPod bug class: a critical binding field (`eigenPod` address / here, `operator_xonly_pk` payer attribution) is populated by an implicit/uncommitted mechanism rather than being explicitly authenticated, breaking the invariant "the operator credited == the party that paid."

### Impact Explanation
This breaks the custody binding "operator credited == party that paid": the attacker's forged OP_RETURN attributes the payout to an operator (e.g. an honest bystander "Bob") who did not fund it. Bob's own automated `PayoutCheckerTask` will then detect the (forged) unhandled payout matching his own `xonly_public_key` and automatically drive `handle_finalized_payout` and the kickoff/reimburse transaction chain, ultimately reimbursing Bob the full `bridge_amount` from the Reimburse transaction — money he never fronted. This matches the Critical impact category "an operator reimbursed for a payout it never funded." Alternately, an attacker can leave the OP_RETURN blank/invalid, causing `payout_payer_operator_xonly_pk` to remain `NULL` permanently — no operator's automation will ever pick up this withdrawal for reimbursement, and since the withdrawal UTXO is already spent, the optimistic-payout fallback (`sign_optimistic_payout`, which checks `is_utxo_spent`) is also blocked, permanently freezing the deposit's move-to-vault UTXO — matching the Critical impact category "a vault UTXO permanently frozen."

### Likelihood Explanation
The attacker needs no privileged role (no verifier/operator/aggregator credential) — only the ability to observe an in-flight, unconfirmed payout transaction on the Bitcoin P2P network/mempool (or via the operator's own broadcast) and to construct/broadcast a conflicting transaction, both standard unprivileged Bitcoin capabilities. The vulnerable code path (SIGHASH_SINGLE|ANYONECANPAY leaving the OP_RETURN uncommitted, combined with unauthenticated trust of that OP_RETURN for reimbursement attribution) is present on every normal (non-optimistic) withdrawal.

### Recommendation
Bind the operator attribution cryptographically to the signed message rather than relying on an unsigned OP_RETURN output. Options:
- Include the operator's `xonly_pk` as part of the data the user signs (e.g., require a `SIGHASH_ALL` component or add the OP_RETURN into the singly-signed output, or use a separate covenant/committed script path that verifies the OP_RETURN content against the intended operator).
- Alternatively, do not rely on the mined transaction's OP_RETURN alone; require the aggregator/verifiers to record and attest (via their own signature) which operator was authorized to front a given withdrawal *before* broadcast, and reconcile the on-chain payer only against that pre-committed value, rejecting mismatches instead of blindly trusting the mined OP_RETURN.

### Proof of Concept
1. Operator A calls `withdraw()`; this builds and broadcasts a payout tx: input 0 = dust withdrawal UTXO + user sig (SIGHASH_SINGLE|ANYONECANPAY), output 0 = user payout, output 1 = anchor, output 2 = OP_RETURN(A's xonly_pk), plus A's own funding inputs added by `fund_raw_transaction`.
2. Attacker (any mempool observer) extracts the withdrawal outpoint, the payout output (script/value), and the user's Schnorr signature from the unconfirmed transaction.
3. Attacker builds a new transaction: same input 0 with the same signature/witness (valid since SIGHASH_SINGLE|ANYONECANPAY doesn't cover it), same output 0 (payout, required for sighash validity), attacker's own funding inputs/change, and output 2 replaced with OP_RETURN(Bob's xonly_pk) — Bob being an arbitrary, honest, uninvolved operator.
4. Attacker broadcasts this variant with a higher fee so it confirms instead of A's tx (or races it directly).
5. `update_finalized_payouts` parses OP_RETURN=Bob's key from the confirmed tx and sets `payout_payer_operator_xonly_pk = Bob` in the DB.
6. Bob's `PayoutCheckerTask` (running standard automation) detects this as its own unhandled payout and automatically proceeds through `handle_finalized_payout` → kickoff → reimburse, netting Bob the full bridge amount for a withdrawal he never funded.

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

**File:** core/src/operator.rs (L676-689)
```rust
        let signed_tx = self
            .rpc
            .sign_raw_transaction_with_wallet(&funded_tx, None, None)
            .await
            .wrap_err("Failed to sign withdrawal transaction")?
            .hex;

        let signed_tx: Transaction = bitcoin::consensus::deserialize(&signed_tx)
            .wrap_err("Failed to deserialize signed withdrawal transaction")?;

        self.rpc
            .send_raw_transaction(&signed_tx)
            .await
            .wrap_err("Failed to send withdrawal transaction")?;
```

**File:** core/src/operator.rs (L1705-1719)
```rust
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

**File:** core/src/verifier.rs (L2345-2350)
```rust
        self.db
            .update_payout_txs_and_payer_operator_xonly_pk(
                Some(dbtx),
                payout_txs_and_payer_operator_idx,
            )
            .await?;
```

**File:** core/src/database/verifier.rs (L282-298)
```rust
    pub async fn get_first_unhandled_payout_by_operator_xonly_pk(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        operator_xonly_pk: XOnlyPublicKey,
    ) -> Result<Option<(u32, Txid, BlockHash)>, BridgeError> {
        let query = sqlx::query_as::<_, (i32, Option<TxidDB>, Option<BlockHashDB>)>(
            "SELECT w.idx, w.move_to_vault_txid, w.payout_tx_blockhash
             FROM withdrawals w
             WHERE w.payout_txid IS NOT NULL
                AND w.is_payout_handled = FALSE
                AND w.payout_payer_operator_xonly_pk = $1
                ORDER BY w.idx ASC
             LIMIT 1",
        )
        .bind(XOnlyPublicKeyDB(operator_xonly_pk));

        let results = execute_query_with_tx!(self.connection, tx, query, fetch_optional)?;
```

**File:** core/src/task/payout_checker.rs (L72-79)
```rust
        let kickoff_txid = self
            .operator
            .handle_finalized_payout(
                &mut dbtx,
                deposit_data.get_deposit_outpoint(),
                payout_tx_blockhash,
            )
            .await?;
```
