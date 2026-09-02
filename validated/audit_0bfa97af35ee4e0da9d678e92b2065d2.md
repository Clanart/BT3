### Title
Duplicate withdrawal indices sharing one Bitcoin UTXO let one payout satisfy two `withdrawals` rows, letting an operator claim reimbursement for a payout it never funded twice - (File: core/src/database/verifier.rs)

### Summary
`get_payout_txs_for_withdrawal_utxos` joins the `withdrawals` table to `bitcoin_syncer_spent_utxos` only on `(txid, vout)`, with no constraint forcing a 1:1 mapping between a spent UTXO and a single withdrawal index. If two `withdrawals` rows (different `idx`, hence different deposits) end up carrying the *same* `withdrawal_utxo_txid/vout` — which nothing in this repo prevents, since that value is written verbatim from whatever Citrea's `withdrawalUTXOs(idx)` reports for each independently auto-incremented index — a single confirmed payout transaction gets attributed to both rows. `PayoutCheckerTask` then processes each row in turn and triggers `handle_finalized_payout` twice, releasing reimbursement collateral for two distinct deposits from one BTC outflow.

### Finding Description
The binding that should hold is: **for every confirmed payout transaction spending outpoint `O`, exactly one `withdrawals.idx` should be marked `payout_txid = txid(payout)`**, i.e. `|{idx : withdrawals[idx].withdrawal_utxo == O}| == 1`.

The attacker (unprivileged, only able to call Citrea's public `withdraw()` and broadcast Bitcoin transactions) can break this by calling Citrea's `withdraw(txid, outputId)` twice with byte-identical arguments. Citrea's withdrawal index is a simple auto-incrementing counter unrelated to UTXO uniqueness — this is confirmed by `collect_withdrawal_utxos` in `core/src/citrea.rs:458-496`, which just iterates `withdrawalUTXOs(start_idx)` sequentially until it reverts, and by the on-chain call shape in `core/src/test/withdraw.rs:133-138`, which shows `withdraw()` takes only `(txid, outputId)` with no de-duplication visible from this repo's client.

Clementine's block-sync path writes whatever Citrea reports for each index without checking for collisions against other rows: [1](#0-0) 
and the underlying UPDATE has no uniqueness enforcement, nor does the `withdrawals` schema declare `UNIQUE(withdrawal_utxo_txid, withdrawal_utxo_vout)`: [2](#0-1) [3](#0-2) 

`Operator::withdraw` only checks that the Citrea-recorded UTXO for the *specific* `withdrawal_index` passed in matches the caller's `in_outpoint` — it never checks whether some other index already claims the same UTXO: [4](#0-3) 
So the operator will happily attempt to build and broadcast payout transactions for both `withdraw(i, ...)` and `withdraw(j, ...)`. Only one of the two payouts can confirm on Bitcoin (the second is a double-spend and is rejected).

The root cause is the join used to detect confirmed payouts, which matches purely by `(txid, vout)` with no `LIMIT 1`/uniqueness guard, so if two `withdrawals` rows share that UTXO both match the single spent-UTXO event: [5](#0-4) 
The resulting duplicate `(idx, payout_txid)` pairs are both written by the batch update: [6](#0-5) [7](#0-6) 

`PayoutCheckerTask` then processes unhandled payouts one at a time, `ORDER BY idx ASC LIMIT 1`, marking each handled in turn on successive runs: [8](#0-7) [9](#0-8) 
Since each `withdrawals.idx` row is tied to a distinct deposit's `move_to_vault_txid` (rows are created via `upsert_move_to_vault_txid_from_citrea_deposit` per deposit index, and `update_withdrawal_utxo_from_citrea_withdrawal` only UPDATEs an existing row), processing idx=i and idx=j both call `handle_finalized_payout` against two *different* deposit outpoints, each of which triggers its own kickoff/reimbursement flow — releasing reimbursement collateral for two deposits although only one Bitcoin payout transaction ever actually moved funds to the withdrawer.

No existing guard (`Verifier::is_deposit_valid`, `Operator::is_profitable`, `SECP.verify_schnorr`, storage-proof verification, or a DB uniqueness constraint) checks that a withdrawal UTXO is unique across indices; the checks that exist operate strictly per-index.

### Impact Explanation
An operator that fronts exactly one Bitcoin payout can be credited with two reimbursement claims against two separate deposits/vault UTXOs, i.e. BTC leaves the bridge's collateral/reimbursement machinery for a payout that was never actually funded for one of the two deposits. This matches the Critical category "an operator reimbursed for a payout it never funded." The attack is repeatable per pair of Citrea withdrawal calls the attacker chooses to duplicate, and scales with however many times the attacker (or a colluding/careless operator) is willing to submit duplicate `withdraw()` calls to Citrea with identical UTXO bytes; the blast radius extends to any deposit/withdrawal pair since the join has no dependency on deposit identity, only on UTXO byte equality.

### Likelihood Explanation
The precondition is only that the attacker can call Citrea's public, unauthenticated `withdraw(txid, outputId)` contract entry point twice with identical bytes — an action explicitly within the stated attacker capabilities, requiring no verifier/operator privilege, no key compromise, and only the cost of the second sBTC withdrawal deposit plus normal Bitcoin fees for the reused signature/payout attempt. It does not require majority hashrate, TLS interception, or any Citrea/light-client circuit defect outside this repo — the defect is squarely in this repository's SQL join logic that fails to bind the join to a unique withdrawal index.

### Recommendation
Make the payout-to-withdrawal binding unique and index-specific:
- Add a uniqueness/idempotency check in `update_citrea_deposit_and_withdrawals`/`update_withdrawal_utxo_from_citrea_withdrawal` rejecting (or flagging) a withdrawal UTXO that is already registered under a different `idx`.
- Change `get_payout_txs_for_withdrawal_utxos`'s join to also verify no more than one `withdrawals.idx` maps to a given `bsu` row, and if a collision is detected, treat it as an anomaly requiring manual/verifier arbitration rather than auto-crediting all colliding indices.
- Optionally, add a DB-level `UNIQUE(withdrawal_utxo_txid, withdrawal_utxo_vout)` constraint (excluding NULLs) on the `withdrawals` table so duplicate UTXO registration across indices fails loudly instead of silently producing duplicate payout attributions.

### Proof of Concept
```rust
// cargo test --package clementine-core --test verifier_db -- duplicate_withdrawal_utxo_double_credit
#[tokio::test]
async fn duplicate_withdrawal_utxo_double_credit() {
    let config = create_test_config_with_thread_name().await;
    let db = Database::new(&config).await.unwrap();
    let mut dbtx = db.begin_transaction().await.unwrap();

    let move_txid_i = Txid::from_byte_array([0x11; 32]);
    let move_txid_j = Txid::from_byte_array([0x22; 32]);
    let idx_i = 0u32;
    let idx_j = 1u32;
    let shared_utxo = OutPoint { txid: Txid::from_byte_array([0x33; 32]), vout: 0 };

    // Two distinct deposits get distinct withdrawal rows.
    db.upsert_move_to_vault_txid_from_citrea_deposit(Some(&mut dbtx), idx_i, &move_txid_i).await.unwrap();
    db.upsert_move_to_vault_txid_from_citrea_deposit(Some(&mut dbtx), idx_j, &move_txid_j).await.unwrap();

    // Attacker calls Citrea's withdraw() twice with identical UTXO bytes -> both indices
    // get the SAME withdrawal_utxo written (simulating collect_withdrawal_utxos results).
    let block_id = db.insert_block_info(Some(&mut dbtx), &BlockHash::all_zeros(), &BlockHash::all_zeros(), 0).await.unwrap();
    db.update_withdrawal_utxo_from_citrea_withdrawal(Some(&mut dbtx), idx_i, shared_utxo, block_id).await.unwrap();
    db.update_withdrawal_utxo_from_citrea_withdrawal(Some(&mut dbtx), idx_j, shared_utxo, block_id).await.unwrap();

    // Only ONE payout tx actually confirms, spending shared_utxo once.
    let payout_txid = Txid::from_byte_array([0x44; 32]);
    db.insert_txid_to_block(&mut dbtx, block_id, &payout_txid).await.unwrap();
    db.insert_spent_utxo(&mut dbtx, block_id, &payout_txid, &shared_utxo.txid, shared_utxo.vout.into()).await.unwrap();

    // Assert the broken binding: BOTH idx_i and idx_j get matched to the same payout_txid.
    let rows = db.get_payout_txs_for_withdrawal_utxos(Some(&mut dbtx), block_id).await.unwrap();
    assert_eq!(rows.len(), 2); // BUG: should be 1, since only one Bitcoin payout confirmed
    assert!(rows.contains(&(idx_i, payout_txid)));
    assert!(rows.contains(&(idx_j, payout_txid)));

    // update_payout_txs_and_payer_operator_xonly_pk would then mark BOTH rows reimbursable,
    // and PayoutCheckerTask would call handle_finalized_payout for move_txid_i AND move_txid_j,
    // crediting the operator twice for one BTC outflow.
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

**File:** core/src/database/verifier.rs (L199-251)
```rust
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

**File:** core/src/operator.rs (L588-596)
```rust
        // Check Citrea for the withdrawal state.
        let withdrawal_utxo = self
            .db
            .get_withdrawal_utxo_from_citrea_withdrawal(None, withdrawal_index)
            .await?;

        if withdrawal_utxo != input_utxo.outpoint {
            return Err(eyre::eyre!("Input UTXO does not match withdrawal UTXO from Citrea: Input Outpoint: {0}, Withdrawal Outpoint (from Citrea): {1}", input_utxo.outpoint, withdrawal_utxo).into());
        }
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
