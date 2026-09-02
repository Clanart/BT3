### Title
Duplicate `withdrawal_utxo` across two withdrawal indices lets one payout transaction satisfy two deposits' reimbursement claims - (File: core/src/database/verifier.rs)

### Summary
`get_payout_txs_for_withdrawal_utxos` joins the `withdrawals` table to `bitcoin_syncer_spent_utxos` purely on `(txid, vout)`, with no uniqueness constraint preventing two different `idx` rows from sharing the same `withdrawal_utxo_txid`/`withdrawal_utxo_vout`. If Citrea's Bridge contract state (untrusted input) registers the same withdrawal UTXO bytes for two distinct withdrawal indices `i` and `j`, a single on-chain Bitcoin transaction spending that UTXO gets matched to both rows, and `update_payout_txs_and_payer_operator_xonly_pk` writes the identical `payout_txid`/operator xonly pk into both. This lets `handle_finalized_payout` be invoked twice - once per index - reimbursing an operator from two separate deposits' vaults for a single fronted payment.

### Finding Description
Binding claimed: the number of distinct withdrawal indices credited with `is_payout_handled = true` for one on-chain payout transaction == 1.

Trace:
1. `Verifier::update_citrea_deposit_and_withdrawals` (`core/src/verifier.rs:2205-2281`) iterates `new_withdrawals` returned by `collect_withdrawal_utxos` and calls `update_withdrawal_utxo_from_citrea_withdrawal` for every `(idx, withdrawal_utxo_outpoint)` pair without any check that the same `OutPoint` was already assigned to a different `idx`. [1](#0-0) 
2. `update_withdrawal_utxo_from_citrea_withdrawal` writes `withdrawal_utxo_txid`/`withdrawal_utxo_vout` per `idx` unconditionally. [2](#0-1) 
3. The `withdrawals` table schema has `idx` as its only primary key/unique constraint; there is no `UNIQUE(withdrawal_utxo_txid, withdrawal_utxo_vout)` constraint, so two rows can legally hold identical UTXO bytes. [3](#0-2) 
4. Once the real Bitcoin payout transaction spends that (shared) UTXO, `get_payout_txs_for_withdrawal_utxos` matches on `bsu.txid = w.withdrawal_utxo_txid AND bsu.vout = w.withdrawal_utxo_vout` with no `DISTINCT`/uniqueness enforcement, so it returns **both** `(i, payout_txid)` and `(j, payout_txid)`. [4](#0-3) 
5. `update_finalized_payouts` feeds every returned `(idx, payout_txid)` pair, together with the operator xonly pk parsed once from the tx's single OP_RETURN, into `update_payout_txs_and_payer_operator_xonly_pk`, which does a bulk `UPDATE ... FROM (VALUES ...) WHERE w.idx = c.idx`, writing the same `payout_txid`/operator pk into both rows. [5](#0-4) [6](#0-5) 
6. `get_first_unhandled_payout_by_operator_xonly_pk` (used by `PayoutCheckerTask`) selects unhandled rows filtered only by `payout_txid IS NOT NULL AND is_payout_handled = FALSE AND payout_payer_operator_xonly_pk = $1`, so it will surface index `i` and, on a later poll after `i` is marked handled, index `j` as well, since both carry the same operator pk and non-null `payout_txid`. [7](#0-6) [8](#0-7) 
7. Each poll calls `Operator::handle_finalized_payout` with the *deposit-specific* outpoint (`deposit_data.get_deposit_outpoint()`), which is different for deposit `i` and deposit `j`, and then `mark_payout_handled` sets `is_payout_handled = TRUE` for that `idx`. Nothing in the traced path re-validates that the underlying Bitcoin UTXO spend actually corresponds one-to-one with the deposit being reimbursed - the join key is purely the withdrawal UTXO bytes, which the attacker can duplicate across indices.

Root cause: the DB schema and the join query treat `(withdrawal_utxo_txid, withdrawal_utxo_vout)` as if it uniquely identified one withdrawal index, but nothing enforces that uniqueness, and no downstream check re-derives that the specific deposit's promised withdrawal amount/utxo was actually satisfied by a payout uniquely tied to that deposit.

### Impact Explanation
An operator (who could be the same party performing the two `withdraw()` registrations, or colluding with the withdrawer) is reimbursed via `handle_finalized_payout`/kickoff+Reimburse transactions for deposit `j`'s vault despite never having fronted a separate BTC payment for it - only one payout transaction was ever broadcast. This drains a second deposit's move-to-vault BTC with no corresponding withdrawal having been fronted, matching the Critical category "an operator reimbursed for a payout it never funded" / "BTC leaving a move-to-vault UTXO without a matching fronted withdrawal." The attack is repeatable across any pair of deposits/withdrawal indices for which the attacker can get Citrea's Bridge contract to register a duplicate `withdrawal_utxo`.

### Likelihood Explanation
Preconditions: the attacker (withdrawer) must be able to get Citrea's Bridge contract to expose the same withdrawal UTXO bytes under two distinct withdrawal indices (assumed as given/untrusted input per the question's scope), and an operator must front exactly one real payout for that UTXO. No verifier privileges, no key compromise, and no majority hashrate are required from this repo's perspective - only ordinary Bitcoin transaction fees for the single payout. Given the schema has no defensive uniqueness constraint and the query/update path has no additional cross-check, exploitation is straightforward once the precondition state exists.

### Recommendation
Add a uniqueness constraint on `(withdrawal_utxo_txid, withdrawal_utxo_vout)` in the `withdrawals` table (or reject/log-and-skip in `update_withdrawal_utxo_from_citrea_withdrawal` if the UTXO is already assigned to a different `idx`). Additionally, harden `get_payout_txs_for_withdrawal_utxos`/`update_finalized_payouts` to detect and reject (or flag for manual review) any spent UTXO that matches more than one withdrawal row, rather than crediting all matching indices.

### Proof of Concept
```rust
#[tokio::test]
async fn duplicate_withdrawal_utxo_double_credits_payout() {
    let config = create_test_config_with_thread_name().await;
    let db = Database::new(&config).await.unwrap();
    let mut dbtx = db.begin_transaction().await.unwrap();

    let shared_utxo = bitcoin::OutPoint {
        txid: bitcoin::Txid::from_byte_array([0x11; 32]),
        vout: 0,
    };
    let payout_txid = Txid::from_byte_array([0x22; 32]);
    let idx_i = 1u32;
    let idx_j = 2u32;
    let move_txid_i = Txid::from_byte_array([0xAA; 32]);
    let move_txid_j = Txid::from_byte_array([0xBB; 32]);
    let operator_pk = generate_random_xonly_pk();
    let block_hash = BlockHash::all_zeros();

    let block_id = db.insert_block_info(Some(&mut dbtx), &block_hash, &block_hash, 0).await.unwrap();
    db.insert_txid_to_block(&mut dbtx, block_id, &payout_txid).await.unwrap();
    db.insert_spent_utxo(&mut dbtx, block_id, &payout_txid, &shared_utxo.txid, shared_utxo.vout.into()).await.unwrap();

    // Two distinct deposits register the SAME withdrawal_utxo bytes at two different idx
    db.upsert_move_to_vault_txid_from_citrea_deposit(Some(&mut dbtx), idx_i, &move_txid_i).await.unwrap();
    db.update_withdrawal_utxo_from_citrea_withdrawal(Some(&mut dbtx), idx_i, shared_utxo, block_id).await.unwrap();

    db.upsert_move_to_vault_txid_from_citrea_deposit(Some(&mut dbtx), idx_j, &move_txid_j).await.unwrap();
    db.update_withdrawal_utxo_from_citrea_withdrawal(Some(&mut dbtx), idx_j, shared_utxo, block_id).await.unwrap();

    // Query matches BOTH indices for the single spending tx
    let txs = db.get_payout_txs_for_withdrawal_utxos(Some(&mut dbtx), block_id).await.unwrap();
    assert!(txs.contains(&(idx_i, payout_txid)));
    assert!(txs.contains(&(idx_j, payout_txid))); // BROKEN BINDING: should be only one index

    db.update_payout_txs_and_payer_operator_xonly_pk(
        Some(&mut dbtx),
        vec![
            (idx_i, payout_txid, Some(operator_pk), block_hash),
            (idx_j, payout_txid, Some(operator_pk), block_hash),
        ],
    ).await.unwrap();

    db.mark_payout_handled(Some(&mut dbtx), idx_i, Txid::from_byte_array([0xCC; 32])).await.unwrap();
    db.mark_payout_handled(Some(&mut dbtx), idx_j, Txid::from_byte_array([0xDD; 32])).await.unwrap();

    // Both indices are is_payout_handled = true off a single fronted BTC transaction
    assert!(db.get_handled_payout_kickoff_txid(Some(&mut dbtx), payout_txid).await.unwrap().is_some());
}
```

### Citations

**File:** core/src/verifier.rs (L2248-2262)
```rust
        for (idx, withdrawal_utxo_outpoint) in new_withdrawals {
            tracing::info!(
                "Saving withdrawal utxo {:?} with index {} for Citrea withdrawals",
                withdrawal_utxo_outpoint,
                idx
            );
            self.db
                .update_withdrawal_utxo_from_citrea_withdrawal(
                    Some(dbtx),
                    idx as u32,
                    withdrawal_utxo_outpoint,
                    block_height,
                )
                .await?;
        }
```

**File:** core/src/verifier.rs (L2283-2350)
```rust
    async fn update_finalized_payouts(
        &self,
        dbtx: DatabaseTransaction<'_>,
        block_id: u32,
        block_cache: &block_cache::BlockCache,
    ) -> Result<(), BridgeError> {
        let payout_txids = self
            .db
            .get_payout_txs_for_withdrawal_utxos(Some(dbtx), block_id)
            .await?;

        let block = &block_cache.block;

        let block_hash = block.block_hash();

        let mut payout_txs_and_payer_operator_idx = vec![];
        for (idx, payout_txid) in payout_txids {
            let payout_tx_idx = block_cache.txids.get(&payout_txid);
            if payout_tx_idx.is_none() {
                tracing::error!(
                    "Payout tx not found in block cache: {:?} and in block: {:?}",
                    payout_txid,
                    block_id
                );
                tracing::error!("Block cache: {:?}", block_cache);
                return Err(eyre::eyre!("Payout tx not found in block cache").into());
            }
            let payout_tx_idx = payout_tx_idx.expect("Payout tx not found in block cache");
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

**File:** core/src/database/verifier.rs (L108-135)
```rust
    pub async fn update_withdrawal_utxo_from_citrea_withdrawal(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        citrea_idx: u32,
        withdrawal_utxo: OutPoint,
        withdrawal_batch_proof_bitcoin_block_height: u32,
    ) -> Result<(), BridgeError> {
        let query = sqlx::query(
            "UPDATE withdrawals
             SET withdrawal_utxo_txid = $2,
                 withdrawal_utxo_vout = $3,
                 withdrawal_batch_proof_bitcoin_block_height = $4
             WHERE idx = $1",
        )
        .bind(i32::try_from(citrea_idx).wrap_err("Failed to convert citrea index to i32")?)
        .bind(TxidDB(withdrawal_utxo.txid))
        .bind(
            i32::try_from(withdrawal_utxo.vout)
                .wrap_err("Failed to convert withdrawal utxo vout to i32")?,
        )
        .bind(
            i32::try_from(withdrawal_batch_proof_bitcoin_block_height)
                .wrap_err("Failed to convert withdrawal batch proof bitcoin block height to i32")?,
        );

        execute_query_with_tx!(self.connection, tx, query, execute)?;
        Ok(())
    }
```

**File:** core/src/database/verifier.rs (L170-196)
```rust
    pub async fn get_payout_txs_for_withdrawal_utxos(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        block_id: u32,
    ) -> Result<Vec<(u32, Txid)>, BridgeError> {
        let query = sqlx::query_as::<_, (i32, TxidDB)>(
            "SELECT w.idx, bsu.spending_txid
             FROM withdrawals w
             JOIN bitcoin_syncer_spent_utxos bsu
                ON bsu.txid = w.withdrawal_utxo_txid
                AND bsu.vout = w.withdrawal_utxo_vout
             WHERE bsu.block_id = $1",
        )
        .bind(i32::try_from(block_id).wrap_err("Failed to convert block id to i32")?);

        let results = execute_query_with_tx!(self.connection, tx, query, fetch_all)?;

        results
            .into_iter()
            .map(|(idx, txid)| {
                Ok((
                    u32::try_from(idx).wrap_err("Failed to convert withdrawal index to u32")?,
                    txid.0,
                ))
            })
            .collect()
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

**File:** core/src/database/schema.sql (L269-281)
```sql
create table if not exists withdrawals (
    idx int primary key,
    move_to_vault_txid bytea not null,
    withdrawal_utxo_txid bytea,
    withdrawal_utxo_vout int,
    withdrawal_batch_proof_bitcoin_block_height int,
    payout_txid bytea,
    payout_payer_operator_xonly_pk text,
    payout_tx_blockhash text check (payout_tx_blockhash ~ '^[a-fA-F0-9]{64}'),
    is_payout_handled boolean not null default false,
    kickoff_txid bytea,
    created_at timestamp not null default now()
);
```

**File:** core/src/task/payout_checker.rs (L41-106)
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

        // fetch and save the LCP for if we get challenged and need to provide proof of payout later
        let (_, payout_block_height) = self
            .operator
            .db
            .get_block_info_from_hash(Some(&mut dbtx), payout_tx_blockhash)
            .await?
            .ok_or_eyre("Couldn't find payout blockhash in bitcoin sync")?;

        let _ = self
            .operator
            .citrea_client
            .fetch_validate_and_store_lcp(
                payout_block_height as u64,
                citrea_idx,
                &self.operator.db,
                Some(&mut dbtx),
                self.operator.config.protocol_paramset(),
            )
            .await?;

        #[cfg(feature = "automation")]
        self.operator.end_round(&mut dbtx).await?;

        self.db
            .mark_payout_handled(Some(&mut dbtx), citrea_idx, kickoff_txid)
            .await?;
```
