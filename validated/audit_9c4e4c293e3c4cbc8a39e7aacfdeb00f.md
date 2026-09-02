### Title
Payout OP_RETURN operator attribution is unauthenticated, allowing misattribution of reimbursement credit - (File: `core/src/builder/transaction/operator_reimburse.rs`)

### Summary
The user's payout signature only authorizes the withdrawal input/output; the `OP_RETURN` output that attributes the payout to a specific operator is not covered by that signature, and the verifier's bookkeeping trusts whatever `OP_RETURN` data appears on-chain to decide which operator gets credited (and later reimbursed) for having fronted a withdrawal.

### Finding Description
`create_payout_txhandler` builds the payout transaction with the operator's `OP_RETURN` output (`operator_xonly_pk.serialize()`) appended after the user output and anchor output, and only the withdrawal input is signed via the user's taproot key-spend signature (`user_sig`), which per the test-utility docstring is generated as `SinglePlusAnyoneCanPay` ("Generates withdrawal transaction and signs it with `SinglePlusAnyoneCanPay`" - `core/src/test/common/setup_utils.rs:430`). A `SIGHASH_SINGLE|ANYONECANPAY` signature over the payout input only commits the signer to a single specific output; it does not bind any additional outputs (such as the `OP_RETURN`) to the transaction. [1](#0-0) 

On the verifier side, `update_finalized_payouts` scans the confirmed payout transaction, extracts the `OP_RETURN` output, and parses the embedded x-only pubkey as the "payer operator," which is then persisted via `update_payout_txs_and_payer_operator_xonly_pk` into the `withdrawals` table binding `payout_payer_operator_xonly_pk` to that withdrawal index. [2](#0-1) [3](#0-2) 

Because the `OP_RETURN` value is not cryptographically bound to the user's signed input (it is not committed by the `SIGHASH_SINGLE|ANYONECANPAY` sighash and there is no operator signature over it either — the operator's own witness for this input is the user's signature only), any party capable of relaying or re-constructing the transaction before broadcast can swap the `OP_RETURN` payload to name a different operator x-only pubkey than the one that actually funded the withdrawal output, while keeping the same signed input/output. This breaks the intended binding: `operator that funded the withdrawal output == operator credited in payout_payer_operator_xonly_pk`.

The bug class mirrors the referenced report: a value-transfer event (the Burve token transfer / here, transaction relay before confirmation) is not accompanied by an update to the ownership/attribution record (`islandSharesPerOwner` / here, `payout_payer_operator_xonly_pk`), so the entity credited diverges from the entity that actually holds/paid.

### Impact Explanation
`payout_payer_operator_xonly_pk` is the record verifiers rely on to determine which operator is entitled to reimbursement for a given withdrawal. If this value can be set to an arbitrary operator's key by a party other than the one who funded the output, an honest operator who fronted the withdrawal could have their reimbursement credited to a different (possibly non-existent or attacker-controlled) operator identity, or a malicious actor could attempt to misattribute payouts to redirect downstream reimbursement bookkeeping. This falls into the "operator reimbursed for a payout it never funded" / "honest operator permanently unable to be reimbursed" class of High/Critical impact.

### Likelihood Explanation
Exploitability requires an actor to intercept or reconstruct the payout transaction between when the operator obtains the user's `SinglePlusAnyoneCanPay` signature and when it is confirmed on-chain, and substitute the `OP_RETURN` output before broadcasting/relaying it — no privileged role, key compromise, or majority hashrate is needed, only unauthenticated manipulation of an unsigned-covered output in an otherwise-valid signed transaction. This is a realistic unprivileged-attacker path given the documented use of `ANYONECANPAY`.

### Recommendation
Bind the `OP_RETURN` operator attribution to the same signature that authorizes the withdrawal, e.g., by having the operator co-sign the full transaction (covering the `OP_RETURN` output) rather than relying solely on the user's `SinglePlusAnyoneCanPay` signature, or by deriving/validating the credited operator from an authenticated source (such as the broadcasting operator's own signed submission via `withdraw()`/`internal_withdraw()`) instead of trusting unauthenticated on-chain `OP_RETURN` bytes.

### Proof of Concept
1. Operator O1 obtains the user's payout signature over `(input_outpoint, output_txout)` via `SinglePlusAnyoneCanPay`, as constructed in `create_payout_txhandler` (`core/src/builder/transaction/operator_reimburse.rs:407-436`).
2. Before this transaction is confirmed, a third party intercepts the raw signed transaction (e.g., from the mempool) and rebuilds it, replacing the `OP_RETURN` output's embedded xonly pubkey with a different operator O2's key — this does not invalidate the `SIGHASH_SINGLE|ANYONECANPAY` witness since additional/replaced outputs beyond the signed one are not covered.
3. The modified transaction confirms on Bitcoin.
4. `update_finalized_payouts` in `core/src/verifier.rs:2283-2350` parses the `OP_RETURN` and calls `update_payout_txs_and_payer_operator_xonly_pk`, recording O2 (not O1, who actually funded the output) as `payout_payer_operator_xonly_pk` for that withdrawal index in the `withdrawals` table.

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

**File:** core/src/verifier.rs (L2311-2350)
```rust
            let payout_tx = &block.txdata[*payout_tx_idx];
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

        self.db
            .update_payout_txs_and_payer_operator_xonly_pk(
                Some(dbtx),
                payout_txs_and_payer_operator_idx,
            )
            .await?;
```

**File:** core/src/database/verifier.rs (L198-248)
```rust
    /// Sets the given payout txs' txid and operator index for the given index.
    pub async fn update_payout_txs_and_payer_operator_xonly_pk(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        payout_txs_and_payer_operator_xonly_pk: Vec<(
            u32,
            Txid,
            Option<XOnlyPublicKey>,
            bitcoin::BlockHash,
        )>,
    ) -> Result<(), BridgeError> {
        if payout_txs_and_payer_operator_xonly_pk.is_empty() {
            return Ok(());
        }
        // Convert all values first, propagating any errors
        let converted_values: Result<Vec<_>, BridgeError> = payout_txs_and_payer_operator_xonly_pk
            .iter()
            .map(|(idx, txid, operator_xonly_pk, block_hash)| {
                Ok((
                    i32::try_from(*idx).wrap_err("Failed to convert payout index to i32")?,
                    TxidDB(*txid),
                    operator_xonly_pk.map(XOnlyPublicKeyDB),
                    BlockHashDB(*block_hash),
                ))
            })
            .collect();
        let converted_values = converted_values?;

        let mut query_builder = QueryBuilder::new(
            "UPDATE withdrawals AS w SET
                payout_txid = c.payout_txid,
                payout_payer_operator_xonly_pk = c.payout_payer_operator_xonly_pk,
                payout_tx_blockhash = c.payout_tx_blockhash
                FROM (",
        );

        query_builder.push_values(
            converted_values.into_iter(),
            |mut b, (idx, txid, operator_xonly_pk, block_hash)| {
                b.push_bind(idx)
                    .push_bind(txid)
                    .push_bind(operator_xonly_pk)
                    .push_bind(block_hash);
            },
        );

        query_builder
            .push(") AS c(idx, payout_txid, payout_payer_operator_xonly_pk, payout_tx_blockhash) WHERE w.idx = c.idx");

        let query = query_builder.build();
        execute_query_with_tx!(self.connection, tx, query, execute)?;
```
