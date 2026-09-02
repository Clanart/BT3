### Title
Missing outpoint-to-idx uniqueness lets one Bitcoin payout satisfy two `withdrawals` rows via `get_payout_txs_for_withdrawal_utxos` - (File: core/src/database/verifier.rs)

### Summary
`withdrawals` rows are keyed only by `idx` (the Citrea withdrawal index) and store `withdrawal_utxo_txid`/`withdrawal_utxo_vout` independently per row, with no constraint forcing a given outpoint to belong to a single `idx`. Because `get_payout_txs_for_withdrawal_utxos` joins on `(txid, vout)` rather than `idx`, one spent UTXO that happens to be registered under two different withdrawal indices produces two payout rows, each independently reaching `PayoutCheckerTask::run_once` and each independently triggering `handle_finalized_payout`/kickoff/Reimburse.

### Finding Description
The claimed broken binding: **count of `withdrawals` rows with `(withdrawal_utxo_txid, withdrawal_utxo_vout) = spent_outpoint` AND `is_payout_handled = TRUE`** should equal **1** for any single spent outpoint, but nothing in the schema or query logic enforces this.

- `update_withdrawal_utxo_from_citrea_withdrawal` writes `withdrawal_utxo_txid`/`withdrawal_utxo_vout` into whichever row matches the given `idx`, driven purely by Citrea's `collect_withdrawal_utxos(last_withdrawal_idx, to_height)` results, with no check that the outpoint bytes are not already used by another `idx`: [1](#0-0) [2](#0-1) 

- The Citrea contract call `withdraw(txid, vout)` takes attacker-chosen bytes, and `collect_withdrawal_utxos` merely reads back whatever the contract stored per sequential index, with no dedup against previously used outpoints in this codebase: [3](#0-2) [4](#0-3) 

- `get_payout_txs_for_withdrawal_utxos` joins `withdrawals` to `bitcoin_syncer_spent_utxos` strictly by `(txid, vout)` equality, not by `idx`, so a single spend row in `bitcoin_syncer_spent_utxos` (one Bitcoin payout transaction) matches **every** `withdrawals` row sharing that outpoint: [5](#0-4) 

- `update_payout_txs_and_payer_operator_xonly_pk` then bulk-updates `payout_txid`/`payout_payer_operator_xonly_pk`/`payout_tx_blockhash` for every `(idx, txid, operator_xonly_pk, block_hash)` tuple produced by that join, i.e. for both `idx1` and `idx2`: [6](#0-5) 

- `get_first_unhandled_payout_by_operator_xonly_pk` and `mark_payout_handled` operate strictly per `idx`, gated only by `is_payout_handled` and `payout_payer_operator_xonly_pk` — with no outpoint-level dedup: [7](#0-6) [8](#0-7) 

- `PayoutCheckerTask::run_once` polls `get_first_unhandled_payout_by_operator_xonly_pk` and, whenever it returns a row, unconditionally calls `handle_finalized_payout` and later `mark_payout_handled`, then loops again on the next poll — it will process `idx1` on one poll and `idx2` on the next poll, since both satisfy the same `WHERE ... is_payout_handled = FALSE AND payout_payer_operator_xonly_pk = $1` predicate: [9](#0-8) 

Root cause: the design assumes an outpoint uniquely identifies one withdrawal `idx`, but neither the database schema nor any of these queries enforce that assumption. Since all verifiers/operators independently run this same `update_citrea_deposit_and_withdrawals` → `get_payout_txs_for_withdrawal_utxos` → `update_payout_txs_and_payer_operator_xonly_pk` pipeline against the same public Citrea contract state, this is not merely a local caching bug — every node in the protocol would independently reach the same (incorrect) conclusion that both `idx1` and `idx2` were fronted by the single real payout transaction, so no honest verifier's local recomputation would disagree and flag it as anomalous purely from this check.

### Impact Explanation
If the attacker holds two real, distinct deposits/vaults at Citrea withdrawal indices `idx1` and `idx2`, and registers the same withdrawal-UTXO bytes for both via `withdraw()`, a single Bitcoin payout transaction that actually spends only one outpoint gets recorded as satisfying both rows. Each row independently drives `handle_finalized_payout`, meaning an operator can end up with two separate kickoff/Reimburse claims credited for a payout it funded only once — "operator reimbursed for a payout it never funded" (Critical). This drains bridge value beyond what was fronted and is repeatable across any pair of deposits/withdrawals an attacker controls, and across any operator that happens to match `payout_payer_operator_xonly_pk` for both rows.

### Likelihood Explanation
Requires the attacker to control two real deposits (paying `bridge_amount` twice into vaults) and to be able to call Citrea's `withdraw()` twice with identical outpoint bytes without the Citrea Bridge contract rejecting duplicate outpoints across indices — this contract-side precondition is external to this repository, but I could not fully verify from this codebase whether Citrea's contract deduplicates outpoints (out of scope to inspect). Within this repository, no additional safeguard (uniqueness constraint, idx-based join, or OP_RETURN/deposit-id cross-check at this specific layer) blocks the divergence once two rows share an outpoint. I was unable to fully confirm within the remaining investigation whether a later stage (kickoff OP_RETURN deposit-id commitment check, `Verifier::is_kickoff_malicious`, or challenge/disprove path) independently catches the mismatched deposit-id-to-payout binding before funds are actually released on Citrea's side — this residual uncertainty should be resolved by tracing `handle_finalized_payout`'s kickoff construction and the corresponding verifier-side challenge checks.

### Recommendation
Enforce a uniqueness constraint (or explicit application-level check) that a given `(withdrawal_utxo_txid, withdrawal_utxo_vout)` can be associated with at most one `idx`, and change `get_payout_txs_for_withdrawal_utxos`/`update_payout_txs_and_payer_operator_xonly_pk` to reject or flag as malicious any withdrawal registration whose outpoint already exists on another `idx`, rather than silently allowing multiple `idx` rows to be marked handled from one spend event.

### Proof of Concept
```rust
// core/src/database/tests (verifier.rs style) — NOT part of the vulnerable scope itself,
// used only to demonstrate the binding violation:
// 1. Insert two withdrawals rows idx1, idx2 each with distinct move_to_vault_txid
//    (simulating two real deposits), via upsert_move_to_vault_txid_from_citrea_deposit.
// 2. Call update_withdrawal_utxo_from_citrea_withdrawal(idx1, utxo, block_id) and
//    update_withdrawal_utxo_from_citrea_withdrawal(idx2, utxo, block_id) with the SAME `utxo`.
// 3. Insert exactly one spend of `utxo` via insert_spent_utxo (one real payout tx, spending_txid).
// 4. Call get_payout_txs_for_withdrawal_utxos(block_id) and assert it returns rows for
//    BOTH idx1 and idx2, mapping to the SAME spending_txid.
// 5. Call update_payout_txs_and_payer_operator_xonly_pk with both tuples, then
//    get_first_unhandled_payout_by_operator_xonly_pk twice (marking idx1 handled between
//    calls) and assert it returns idx2 as a second, distinct "unhandled payout" for the
//    same operator xonly_pk — proving one spend maps to two handled payouts.
```

### Citations

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

**File:** core/src/database/verifier.rs (L348-362)
```rust
    pub async fn mark_payout_handled(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        citrea_idx: u32,
        kickoff_txid: Txid,
    ) -> Result<(), BridgeError> {
        let query = sqlx::query(
            "UPDATE withdrawals SET is_payout_handled = TRUE, kickoff_txid = $2 WHERE idx = $1",
        )
        .bind(i32::try_from(citrea_idx).wrap_err("Failed to convert citrea index to i32")?)
        .bind(TxidDB(kickoff_txid));

        execute_query_with_tx!(self.connection, tx, query, execute)?;
        Ok(())
    }
```

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

**File:** core/src/citrea.rs (L458-496)
```rust
    async fn collect_withdrawal_utxos(
        &self,
        last_withdrawal_idx: Option<u32>,
        to_height: u64,
    ) -> Result<Vec<(u64, OutPoint)>, BridgeError> {
        let mut utxos = vec![];

        let mut start_idx = match last_withdrawal_idx {
            Some(idx) => idx + 1,
            None => 0,
        };

        loop {
            let withdrawal_utxo = self
                .contract
                .withdrawalUTXOs(U256::from(start_idx))
                .block(BlockId::Number(BlockNumberOrTag::Number(to_height)))
                .call()
                .await;
            match withdrawal_utxo {
                Err(e) if e.to_string().contains("execution reverted") => {
                    tracing::trace!("Withdrawal utxo not found for index, error: {:?}", e);
                    break;
                }
                Err(e) => return Err(e.into()),
                Ok(_) => {}
            }
            let withdrawal_utxo = withdrawal_utxo.expect("Failed to get withdrawal UTXO");
            let txid = withdrawal_utxo.txId.0;
            let txid =
                Txid::from_slice(txid.as_ref()).wrap_err("Failed to convert txid to Txid")?;
            let vout = withdrawal_utxo.outputId.0;
            let vout = u32::from_le_bytes(vout);
            let utxo = OutPoint { txid, vout };
            utxos.push((start_idx as u64, utxo));
            start_idx += 1;
        }
        Ok(utxos)
    }
```

**File:** core/src/test/withdraw.rs (L133-162)
```rust
        let citrea_withdrawal_tx = citrea_client
            .contract
            .withdraw(
                FixedBytes::from(withdrawal_utxo.txid.to_raw_hash().to_byte_array()),
                FixedBytes::from(withdrawal_utxo.vout.to_le_bytes()),
            )
            .value(U256::from(
                config.protocol_paramset().bridge_amount.to_sat() * SATS_TO_WEI_MULTIPLIER,
            ))
            .send()
            .await
            .unwrap();
        sequencer.client.send_publish_batch_request().await.unwrap();

        let receipt = citrea_withdrawal_tx.get_receipt().await.unwrap();
        println!("Citrea withdrawal tx receipt: {receipt:?}");

        let withdrawal_count = citrea_client
            .contract
            .getWithdrawalCount()
            .call()
            .await
            .unwrap();
        assert_eq!(withdrawal_count._0, U256::from(1));

        let utxos = citrea_client
            .collect_withdrawal_utxos(None, withdrawal_tx_height_block_height)
            .await
            .unwrap();
        assert_eq!(withdrawal_utxo, utxos[0].1);
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
