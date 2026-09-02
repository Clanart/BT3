I found a solid, unprivileged-attacker analog. Here is the analysis and finding.

## Finding

The reported bug class (limit check bound to a manipulable "receiver" field instead of the actual value-provider) maps onto how Clementine attributes **who fronted a withdrawal payout** for later BTC reimbursement. The binding that should hold is:

`party whose funds paid for the user's payout output == operator_xonly_pk credited for reimbursement`

but the protocol derives the right-hand side purely from an **unauthenticated OP_RETURN data push** that is not covered by the only signature present in the transaction.

### Root cause

`create_payout_txhandler` builds the payout transaction with:
- Input 0: the user's withdrawal UTXO, spent via `SpendPath::KeySpend` with the **user's** `SIGHASH_SINGLE | SIGHASH_ANYONECANPAY` signature.
- Output 0: the user's payout (committed by the `SINGLE` sighash).
- Output 2: an `OP_RETURN` containing `operator_xonly_pk` — the claimed identity of whichever operator is fronting the payout. [1](#0-0) 

`SIGHASH_SINGLE | ANYONECANPAY` commits **only** to input 0 and output 0. It does not commit to any other input or to the OP_RETURN output. `ANYONECANPAY` explicitly allows anyone holding the user's signature to attach arbitrary *other* inputs (to fund output 0's value, since the dust withdrawal UTXO alone cannot cover it) and arbitrary *other* outputs — including a different OP_RETURN payload.

Verifiers later scan the chain and blindly trust this OP_RETURN field as the ground truth for "who paid": [2](#0-1) 

That value is stored as `payout_payer_operator_xonly_pk` and used directly to decide which operator is entitled to reimbursement, with no cross-check against which inputs actually funded the transaction: [3](#0-2) [4](#0-3) 

An operator's own node later polls for payouts attributed to *its own* `xonly_pk` and automatically starts the kickoff/round reimbursement flow for it: [5](#0-4) 

`is_kickoff_malicious` on the verifier side only checks that the kickoff operator's key equals the OP_RETURN-derived key — it never checks that the OP_RETURN-named operator actually supplied the funding inputs of the payout tx: [6](#0-5) 

### Attack path (no privileged role required)

1. A withdrawal's dust UTXO + the user's `SIGHASH_SINGLE|ANYONECANPAY` signature becomes visible once any operator broadcasts the intended payout transaction to the public Bitcoin mempool.
2. An attacker (not an operator, no key compromise) builds a **replacement** transaction reusing input 0 and the same signature (valid because `ANYONECANPAY` allows attaching different other inputs), keeping output 0 identical (fixed by `SINGLE`), but funding it with the attacker's own additional input(s), and writes an **arbitrary operator's `xonly_pk`** (e.g., an uninvolved, honest operator) into the OP_RETURN.
3. If this attacker-funded transaction gets confirmed for the withdrawal's outpoint (e.g. via a higher fee / RBF race, or simply being first), `update_finalized_payouts` records the framed operator as `payout_payer_operator_xonly_pk`.
4. The framed operator's own `PayoutCheckerTask` will pick this up as its own unhandled payout and automatically pursue kickoff/round/reimburse, receiving BTC reimbursement out of the bridge vault for a withdrawal it never actually funded.

This is a direct structural analog of the PearVault bug: the party that is *credited* (the operator named in OP_RETURN) is decoupled from the party whose funds actually *paid*, and the mismatch is enforced by nothing cryptographic — it's a self-reported, unsigned data field.

### Impact

Matches the Critical category "an operator reimbursed for a payout it never funded" — bridge value (the vault's move-to-vault BTC) is paid out to an operator that supplied no funds for the corresponding withdrawal, while whoever actually funded the user's payout output gets no credit. This is a genuine custody/attribution binding failure, not a privileged-role issue: the attacker needs no verifier/operator/aggregator role, no key compromise — only the ability to observe a mempool transaction and spend their own BTC to fund a replacement.

### Recommendation
Bind the OP_RETURN operator identity to the transaction cryptographically — e.g., have `create_payout_txhandler` include the operator's own signature/commitment over the OP_RETURN output (or over the whole transaction) rather than leaving it as an unsigned data push outside the `SIGHASH_SINGLE|ANYONECANPAY` commitment, and/or have verifiers additionally check that funding inputs of the confirmed payout transaction are attributable to the operator being credited before recording `payout_payer_operator_xonly_pk`.

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

**File:** core/src/verifier.rs (L1857-1890)
```rust
    /// Checks if the operator who sent the kickoff matches the payout data saved in our db
    /// Payout data in db is updated during citrea sync.
    async fn is_kickoff_malicious(
        &self,
        kickoff_witness: Witness,
        deposit_data: &mut DepositData,
        kickoff_data: KickoffData,
        dbtx: DatabaseTransaction<'_>,
    ) -> Result<bool, BridgeError> {
        let move_txid =
            create_move_to_vault_txhandler(deposit_data, self.config.protocol_paramset())?
                .get_cached_tx()
                .compute_txid();

        let payout_info = self
            .db
            .get_payout_info_from_move_txid(Some(dbtx), move_txid)
            .await?;
        let Some((operator_xonly_pk_opt, payout_blockhash, _, _)) = payout_info else {
            tracing::warn!(
                "No payout info found in db for move txid {move_txid}, assuming malicious"
            );
            return Ok(true);
        };

        let Some(operator_xonly_pk) = operator_xonly_pk_opt else {
            tracing::warn!("No operator xonly pk found in payout tx OP_RETURN, assuming malicious");
            return Ok(true);
        };

        if operator_xonly_pk != kickoff_data.operator_xonly_pk {
            tracing::warn!("Operator xonly pk for the payout does not match with the kickoff_data");
            return Ok(true);
        }
```

**File:** core/src/verifier.rs (L2311-2343)
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
```

**File:** core/src/database/verifier.rs (L198-251)
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

        Ok(())
    }
```

**File:** core/src/database/verifier.rs (L282-313)
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

        results
            .map(|(citrea_idx, move_to_vault_txid, payout_tx_blockhash)| {
                Ok((
                    u32::try_from(citrea_idx).wrap_err("Failed to convert citrea index to u32")?,
                    move_to_vault_txid
                        .expect("move_to_vault_txid Must be Some")
                        .0,
                    payout_tx_blockhash
                        .expect("payout_tx_blockhash Must be Some")
                        .0,
                ))
            })
            .transpose()
    }
```

**File:** core/src/task/payout_checker.rs (L41-79)
```rust
        let unhandled_payout = self
            .db
            .get_first_unhandled_payout_by_operator_xonly_pk(
                Some(&mut dbtx),
                self.operator.signer.xonly_public_key,
            )
            .await?;

        if unhandled_payout.is_none() {
            return Ok(false);
        }

        let (citrea_idx, move_to_vault_txid, payout_tx_blockhash) =
            unhandled_payout.expect("Must be Some");

        tracing::info!(
            "Unhandled payout found for withdrawal {}, move_txid: {}",
            citrea_idx,
            move_to_vault_txid
        );

        let deposit_data = self
            .db
            .get_deposit_data_with_move_tx(Some(&mut dbtx), move_to_vault_txid)
            .await?;
        if deposit_data.is_none() {
            return Err(eyre::eyre!("Fronted withdrawal for move tx {move_to_vault_txid} found, but the signatures for the deposit are not found in the db.").into());
        }

        let deposit_data = deposit_data.expect("Must be Some");

        let kickoff_txid = self
            .operator
            .handle_finalized_payout(
                &mut dbtx,
                deposit_data.get_deposit_outpoint(),
                payout_tx_blockhash,
            )
            .await?;
```
