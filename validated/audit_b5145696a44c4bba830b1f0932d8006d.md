### Title
Duplicate reimbursement of a single Bitcoin payout via colliding `withdrawal_utxo_txid`/`vout` across two `withdrawals.idx` rows - (File: `core/src/database/verifier.rs`)

### Summary
Because `withdrawals.withdrawal_utxo_txid`/`withdrawal_utxo_vout` has no uniqueness constraint [1](#0-0) , two distinct `idx` rows can be populated with identical UTXO coordinates, causing the join in `get_payout_txs_for_withdrawal_utxos` to attach the *same* on-chain payout transaction to *both* rows [2](#0-1) . `update_finalized_payouts` then derives the operator's xonly pubkey once from that single tx's OP_RETURN and writes it into both rows via `update_payout_txs_and_payer_operator_xonly_pk` [3](#0-2) , so `get_first_unhandled_payout_by_operator_xonly_pk` will surface both `idx`s as unhandled for that operator across successive polls [4](#0-3) .

### Finding Description
Binding that should hold: for a given real BTC payout transaction `T` that fronted withdrawal `i`, exactly one `withdrawals.idx` row should be attributed and reimbursed to the operator who broadcast `T` — i.e. `count({idx : payout_txid(idx) == T ∧ handled(idx)}) == 1`.

The DB schema places no `UNIQUE(withdrawal_utxo_txid, withdrawal_utxo_vout)` constraint on `withdrawals` [1](#0-0) , so two different `idx` values (two different Citrea withdrawal requests/deposits) can end up with identical `withdrawal_utxo_txid`/`withdrawal_utxo_vout`. `get_payout_txs_for_withdrawal_utxos` performs a plain `JOIN` between `withdrawals` and `bitcoin_syncer_spent_utxos` keyed only on `(txid, vout)` [5](#0-4) . Since a real Bitcoin UTXO can only be spent once, there is exactly one `bitcoin_syncer_spent_utxos` row for that outpoint, but it fans out to both colliding `idx` rows, producing two `(idx, spending_txid)` pairs pointing at the same physical spending transaction.

`update_finalized_payouts` iterates these pairs, reads the OP_RETURN from the *same* transaction twice (once per idx), extracting the same `operator_xonly_pk`, and pushes both `(idx, payout_txid, operator_xonly_pk, block_hash)` tuples into `update_payout_txs_and_payer_operator_xonly_pk` [6](#0-5) . That function performs a bulk `UPDATE ... FROM (VALUES ...) WHERE w.idx = c.idx`, which writes the identical operator pubkey into both rows without any check for pre-existing UTXO/payout_txid collisions [7](#0-6) .

`get_first_unhandled_payout_by_operator_xonly_pk` selects `idx`s where `payout_txid IS NOT NULL AND is_payout_handled = FALSE AND payout_payer_operator_xonly_pk = $1`, ordered by `idx`, `LIMIT 1` [8](#0-7) . It has no awareness of `payout_txid` duplication across rows. `PayoutCheckerTask::run_once` fetches one unhandled row at a time, resolves `deposit_data` from that row's *own* `move_to_vault_txid` (a distinct deposit per idx), calls `handle_finalized_payout` to build/sign a kickoff, then `mark_payout_handled` for that idx only [9](#0-8) . Because the task is polled repeatedly, once idx1 is marked handled the next poll will find idx2 still unhandled (same operator pk, same underlying payout tx) and process it independently — producing a second kickoff/round reimbursement flow for the same fronted BTC payout.

None of the existing guards catch this: there is no uniqueness constraint on `(withdrawal_utxo_txid, withdrawal_utxo_vout)`, no dedup by `payout_txid` in `update_finalized_payouts`, and `get_first_unhandled_payout_by_operator_xonly_pk`/`mark_payout_handled` operate strictly per `idx`, not per physical spent UTXO or `payout_txid`.

### Impact Explanation
Once both `idx` rows are attributed to the same operator, the operator is reimbursed twice from round/reimbursement UTXOs for a single BTC outflow it only made once — an operator credited/reimbursed for a payout it never separately funded for the second `idx`. This matches the Critical category "an operator reimbursed for a payout it never funded." Blast radius: any pair (or more) of withdrawal indices whose `withdrawal_utxo_txid`/`vout` collide will duplicate reimbursement flows; this can repeat across every additional colliding `idx` and across operators observing the same collision.

### Likelihood Explanation
The precondition (two `withdrawals.idx` rows sharing the same `withdrawal_utxo_txid`/`vout`) must already exist — established as given in the referenced "join collision" context. Given that precondition, the propagation traced here through `update_finalized_payouts`, `update_payout_txs_and_payer_operator_xonly_pk`, `get_first_unhandled_payout_by_operator_xonly_pk`, and `PayoutCheckerTask::run_once` is deterministic and requires no additional attacker action beyond letting the normal bitcoin-syncer/task polling loop run; it triggers automatically once the collision and a single real payout broadcast exist.

### Recommendation
Add a `UNIQUE(withdrawal_utxo_txid, withdrawal_utxo_vout)` constraint (or an application-level check before writing) on the `withdrawals` table, and/or change `get_payout_txs_for_withdrawal_utxos`/`update_finalized_payouts` to detect and reject/collapse multiple `idx` rows resolving to the same `payout_txid`, ensuring only one `idx` is ever attributed and marked handled per physically spent UTXO.

### Proof of Concept
`cargo test` in `core/src/database/verifier.rs` (extending `update_get_payout_txs_from_citrea_withdrawal`):
1. Insert two withdrawal rows `idx1`, `idx2` with distinct `move_to_vault_txid`s but call `update_withdrawal_utxo_from_citrea_withdrawal` with the identical `OutPoint` for both.
2. Insert a single `bitcoin_syncer_spent_utxos` row spending that outpoint with `spending_txid = T`.
3. Call `get_payout_txs_for_withdrawal_utxos` and assert it returns two entries `(idx1, T)` and `(idx2, T)` (demonstrating the collision fan-out).
4. Call `update_payout_txs_and_payer_operator_xonly_pk` with both entries carrying the same `operator_xonly_pk`; assert both rows now have `payout_payer_operator_xonly_pk = operator_xonly_pk`.
5. Call `get_first_unhandled_payout_by_operator_xonly_pk` twice with intervening `mark_payout_handled` calls (simulating two `PayoutCheckerTask::run_once` iterations) and assert that it returns `idx1` then `idx2` — i.e., `mark_payout_handled` is invoked twice for one physical `spending_txid = T`, violating "at most once per distinct spent UTXO."

### Citations

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

**File:** core/src/database/verifier.rs (L174-196)
```rust
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

**File:** core/src/database/verifier.rs (L226-248)
```rust
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

**File:** core/src/verifier.rs (L2298-2350)
```rust
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

**File:** core/src/task/payout_checker.rs (L39-111)
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

        dbtx.commit().await?;

        Ok(true)
    }
```
