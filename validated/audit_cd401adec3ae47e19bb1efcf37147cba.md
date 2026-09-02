### Title
Duplicate withdrawal-UTXO attribution in `get_payout_txs_for_withdrawal_utxos` lets one payout tx trigger two reimbursement claims - (File: core/src/database/verifier.rs)

### Summary
`Database::get_payout_txs_for_withdrawal_utxos` joins `withdrawals` to `bitcoin_syncer_spent_utxos` purely on `(withdrawal_utxo_txid, withdrawal_utxo_vout)`, with no uniqueness constraint forcing a withdrawal UTXO to belong to only one `idx`. If two withdrawal indices `i` and `j` are ever recorded with the identical withdrawal UTXO, a single real payout transaction spending that UTXO gets attributed to both rows, and `update_payout_txs_and_payer_operator_xonly_pk` stamps the same operator and payout_txid on both. `PayoutCheckerTask::run_once` then independently drives `Operator::handle_finalized_payout` for each row, producing two separate kickoff/Reimburse chains for one fronted BTC payment.

### Finding Description
The binding that must hold is: for each withdrawal index `idx`, the operator credited via `payout_payer_operator_xonly_pk` and reimbursed via `handle_finalized_payout` must equal the operator that actually fronted a *distinct* BTC payment for that specific `idx`, i.e. `count(reimbursements for idx) == count(distinct fronted BTC payments for idx)`.

The withdrawals table has no uniqueness constraint on `(withdrawal_utxo_txid, withdrawal_utxo_vout)` — only `idx` is the primary key [1](#0-0) . `update_withdrawal_utxo_from_citrea_withdrawal` simply writes whatever UTXO Citrea reports for that `idx`, with no check against other rows [2](#0-1) .

`get_payout_txs_for_withdrawal_utxos` finds which withdrawals were paid in a given block by joining on the UTXO fields alone:
```
SELECT w.idx, bsu.spending_txid
FROM withdrawals w
JOIN bitcoin_syncer_spent_utxos bsu
   ON bsu.txid = w.withdrawal_utxo_txid AND bsu.vout = w.withdrawal_utxo_vout
WHERE bsu.block_id = $1
``` [3](#0-2) 

If rows `i` and `j` share the same `(withdrawal_utxo_txid, withdrawal_utxo_vout)`, this single query returns two rows — `(i, payout_txid)` and `(j, payout_txid)` — for one physical spend. `update_finalized_payouts` iterates this vector and calls `update_payout_txs_and_payer_operator_xonly_pk` with both entries, deriving `operator_xonly_pk` once from the single payout tx's OP_RETURN and writing it to both rows [4](#0-3) .

`get_first_unhandled_payout_by_operator_xonly_pk` selects unhandled rows purely by `payout_payer_operator_xonly_pk = $1 AND is_payout_handled = FALSE`, independently per `idx` [5](#0-4) . `PayoutCheckerTask::run_once` picks the lowest unhandled `idx`, calls `Operator::handle_finalized_payout` for that deposit, then marks only that `idx` handled via `mark_payout_handled` (`UPDATE withdrawals SET is_payout_handled = TRUE ... WHERE idx = $1`) [6](#0-5) [7](#0-6) . On the next poll, row `j` is still unhandled for the same operator and gets processed identically.

`handle_finalized_payout` performs no cross-check that the payout tx / blockhash was already used for another deposit; it looks up `deposit_id`/`deposit_data` from `deposit_outpoint` (which differs between `i` and `j` since each withdrawal `idx` maps to its own `move_to_vault_txid`/deposit), allocates a fresh unused kickoff connector via `get_unused_and_signed_kickoff_connector`, and signs/queues a full Kickoff→Reimburse chain [8](#0-7) . Nothing in this path checks whether the same `payout_txid`/`payout_tx_blockhash` has already funded a reimbursement for a different deposit.

Existing guards do not prevent this: `verify_storage_proofs` (used only during challenge/disprove) validates that Citrea's storage genuinely records UTXO=`X` at index `idx`, which would be true for both `i` and `j` if Citrea itself allowed registering the same UTXO under two withdrawal ids — the circuit has no notion of "this UTXO already backs another withdrawal's reimbursement." `SPV::verify` only proves the payout tx exists in the chain, not that it is unique to one withdrawal id. No SQL uniqueness constraint or application-level check exists on `withdrawal_utxo_txid/vout` across `idx` rows.

### Impact Explanation
An operator ends up reimbursed twice (extracting two Reimburse-connector payouts from the bridge's collateral/round UTXOs) for a single BTC payment it actually made once. This is a Critical-category impact ("an operator is reimbursed for a payout it never separately funded"), doubling BTC extracted per exploited pair of withdrawal indices sharing a UTXO. The bug is mechanical and repeatable: any pair (or more) of withdrawal indices that end up sharing the same recorded `withdrawal_utxo_txid/vout` triggers one extra full reimbursement cycle per duplicate index, scaling with however many indices share a UTXO and regardless of which operator ultimately handles the payout (whichever operator's `payout_payer_operator_xonly_pk` was parsed from the OP_RETURN gets both credits).

### Likelihood Explanation
The trigger condition — two Citrea withdrawal indices being recorded against the identical `(txid, vout)` — requires that Citrea's side permit registering the same outpoint under multiple withdrawal ids; this repo has no uniqueness enforcement to reject it once it happens, and the described attacker action assumes that registration succeeds. Given that precondition, the rest of the chain (join bug, independent `mark_payout_handled` per idx, unchecked `handle_finalized_payout`) is deterministic and requires no special timing beyond two normal `PayoutCheckerTask` poll cycles (250ms in tests, 60s in production). No signature forgery, no privileged access, and no majority hashrate are needed — only the ability to get two withdrawal ids pointed at one UTXO and one real payout transaction.

### Recommendation
Enforce that `(withdrawal_utxo_txid, withdrawal_utxo_vout)` is unique across `withdrawals` rows (reject/flag duplicate registrations at `update_withdrawal_utxo_from_citrea_withdrawal` time), and change `get_payout_txs_for_withdrawal_utxos` to guard against fan-out — e.g. also constrain the query so a single `bitcoin_syncer_spent_utxos` row can satisfy attribution for at most one unresolved `idx`, or add a DB-level check/trigger that prevents `update_payout_txs_and_payer_operator_xonly_pk` from marking two different `idx` rows with the same `payout_txid` unless that payout tx genuinely funds both (which should not be possible for a single-input spend).

### Proof of Concept
```rust
// core/src/database/tests (new test), using existing helpers as in
// update_get_payout_txs_from_citrea_withdrawal (core/src/database/verifier.rs:390)
#[tokio::test]
async fn duplicate_withdrawal_utxo_causes_double_attribution() {
    let config = create_test_config_with_thread_name().await;
    let db = Database::new(&config).await.unwrap();
    let mut dbtx = db.begin_transaction().await.unwrap();

    let shared_utxo = OutPoint { txid: Txid::from_byte_array([0xAA; 32]), vout: 0 };
    let payout_txid = Txid::from_byte_array([0xBB; 32]);
    let block_id = /* insert block, insert_txid_to_block, insert_spent_utxo for payout_txid spending shared_utxo */;

    // Two different deposits/move txids registered under idx 0 and idx 1,
    // both pointing at the SAME withdrawal_utxo (simulating Citrea double-registration).
    db.upsert_move_to_vault_txid_from_citrea_deposit(Some(&mut dbtx), 0, &move_txid_a).await.unwrap();
    db.upsert_move_to_vault_txid_from_citrea_deposit(Some(&mut dbtx), 1, &move_txid_b).await.unwrap();
    db.update_withdrawal_utxo_from_citrea_withdrawal(Some(&mut dbtx), 0, shared_utxo, block_id).await.unwrap();
    db.update_withdrawal_utxo_from_citrea_withdrawal(Some(&mut dbtx), 1, shared_utxo, block_id).await.unwrap();

    // ATTRIBUTION check: one real payout tx should map to at most one idx.
    let attributed = db.get_payout_txs_for_withdrawal_utxos(Some(&mut dbtx), block_id).await.unwrap();
    // BUG: this asserts the current (broken) behavior — both idx 0 and 1 attributed to the same payout_txid.
    assert_eq!(attributed.len(), 2);
    assert!(attributed.contains(&(0, payout_txid)));
    assert!(attributed.contains(&(1, payout_txid)));

    // Stamping both with the same operator confirms the double-credit setup.
    db.update_payout_txs_and_payer_operator_xonly_pk(
        Some(&mut dbtx),
        vec![(0, payout_txid, Some(op_pk), block_hash), (1, payout_txid, Some(op_pk), block_hash)],
    ).await.unwrap();

    // Both rows are now independently "unhandled" for the same operator,
    // so PayoutCheckerTask::run_once (driven twice) would call
    // Operator::handle_finalized_payout twice for two distinct deposits
    // off of a single real fronted payment — violating ATTRIBUTION.
    let first = db.get_first_unhandled_payout_by_operator_xonly_pk(Some(&mut dbtx), op_pk).await.unwrap();
    assert_eq!(first.unwrap().0, 0);
    db.mark_payout_handled(Some(&mut dbtx), 0, kickoff_txid_placeholder).await.unwrap();
    let second = db.get_first_unhandled_payout_by_operator_xonly_pk(Some(&mut dbtx), op_pk).await.unwrap();
    assert_eq!(second.unwrap().0, 1); // still unhandled -> second handle_finalized_payout/kickoff will fire
}
```

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

**File:** core/src/operator.rs (L839-915)
```rust
    pub async fn handle_finalized_payout<'a>(
        &'a self,
        dbtx: DatabaseTransaction<'a>,
        deposit_outpoint: OutPoint,
        payout_tx_blockhash: BlockHash,
    ) -> Result<bitcoin::Txid, BridgeError> {
        let (deposit_id, deposit_data) = self
            .db
            .get_deposit_data(Some(dbtx), deposit_outpoint)
            .await?
            .ok_or(BridgeError::DatabaseError(sqlx::Error::RowNotFound))?;

        // get unused kickoff connector
        let (round_idx, kickoff_idx) = self
            .db
            .get_unused_and_signed_kickoff_connector(
                Some(dbtx),
                deposit_id,
                self.signer.xonly_public_key,
            )
            .await?
            .ok_or(BridgeError::DatabaseError(sqlx::Error::RowNotFound))?;

        let current_round_index = self.db.get_current_round_index(Some(dbtx)).await?;
        tracing::info!(
            "Operator: Current round index: {}, round idx for kickoff: {}",
            current_round_index,
            round_idx
        );
        #[cfg(feature = "automation")]
        if current_round_index != round_idx {
            // we currently have no free kickoff connectors in the current round, so we need to end round first
            // if current_round_index should only be smaller than round_idx, and should not be smaller by more than 1
            // so sanity check:
            if current_round_index.next_round() != round_idx {
                return Err(eyre::eyre!(
                    "Internal error: Expected the current round ({:?}) to be equal to or 1 less than the round of the first available kickoff for deposit reimbursement ({:?}) for deposit {:?}. If the round is less than the current round, there is an issue with the logic of the fn that gets the first available kickoff. If the round is greater, that means the next round do not have any kickoff connectors available for reimbursement, which should not be possible.",
                    current_round_index, round_idx, deposit_outpoint
                ).into());
            }
            tracing::info!(
                "Operator: Starting next round to be able to get reimbursement for the payout"
            );
            // start the next round to be able to get reimbursement for the payout
            self.end_round(dbtx).await?;
        }

        // get signed txs,
        let kickoff_data = KickoffData {
            operator_xonly_pk: self.signer.xonly_public_key,
            round_idx,
            kickoff_idx,
        };

        let payout_tx_blockhash = payout_tx_blockhash.as_byte_array().last_20_bytes();

        #[cfg(test)]
        let payout_tx_blockhash = self
            .config
            .test_params
            .maybe_disrupt_payout_tx_block_hash_commit(payout_tx_blockhash);

        let context = ContractContext::new_context_for_kickoff(
            kickoff_data,
            deposit_data,
            self.config.protocol_paramset(),
        );

        let signed_txs = create_and_sign_txs(
            self.db.clone(),
            &self.signer,
            self.config.clone(),
            context,
            Some(payout_tx_blockhash),
            Some(dbtx),
        )
        .await?;
```
