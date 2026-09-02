### Title
Head-of-line blocking in `get_first_unhandled_payout_by_operator_xonly_pk` permanently starves an honest operator's reimbursements once one earlier idx is unprocessable - (File: `core/src/database/verifier.rs`, `core/src/task/payout_checker.rs`)

### Summary
`PayoutCheckerTask::run_once` always fetches the single smallest unhandled `idx` for operator O via `ORDER BY w.idx ASC LIMIT 1` and processes strictly in that order. If any earlier `idx = j` is permanently unprocessable (its `payout_payer_operator_xonly_pk` matches O but the corresponding deposit/kickoff data O actually signed does not exist), `run_once` errors out on every invocation and never advances past `j`, `git permanently blocking O from ever reaching a legitimately-funded, later `idx = i`.

### Finding Description
The binding that should hold is: for every row returned by `get_first_unhandled_payout_by_operator_xonly_pk(O)`, `payout_payer_operator_xonly_pk == O` implies O actually funded that payout with a kickoff/deposit O created. The code only checks `payout_txid IS NOT NULL AND is_payout_handled = FALSE AND payout_payer_operator_xonly_pk = $1` [1](#0-0) ; it never re-validates that O's own signed kickoff data actually corresponds to that `move_to_vault_txid`.

`PayoutCheckerTask::run_once` takes whatever row is returned, looks up `deposit_data_with_move_tx`, and if that lookup or the subsequent `handle_finalized_payout` fails, propagates an `Err` without committing `dbtx` [2](#0-1) . Because the transaction is never committed, `is_payout_handled` for `j` is never set to `TRUE`, so the very next call to `run_once` re-runs the identical `ORDER BY idx ASC LIMIT 1` query and gets `j` again — forever. `handle_finalized_payout` itself will fail at `get_unused_and_signed_kickoff_connector`/`get_deposit_data` if the kickoff data doesn't correspond to a round/kickoff O genuinely created for that deposit [3](#0-2) . There is no skip-list, retry-limit, or fallback in the query or `run_once` that lets the task move on to `idx = i` once `idx = j`'s payout_payer attribution is wrong, so idx=i for O is unreachable as long as idx=j remains stuck at the head of the ASC-ordered queue.

This finding presupposes the precondition stated in the referenced scenario — that `payout_payer_operator_xonly_pk` for `idx = j` was set to O via a stale/incorrect write (from the separate RBF/attribution race). I was not able to independently re-verify the write path of `update_payout_txs_and_payer_operator_xonly_pk` (i.e., confirm that an attacker-triggered RBF replacement can cause this specific misattribution) within this trace; that mechanism belongs to a different, previously-referenced audit question. Given that precondition, however, the downstream head-of-line-blocking behavior in `PayoutCheckerTask`/`get_first_unhandled_payout_by_operator_xonly_pk` is confirmed directly in the code as traced above.

### Impact Explanation
If the precondition holds, operator O — who genuinely funded payout `idx = i` — can never have its `PayoutCheckerTask` reach `idx = i`, because the task always re-selects the smaller, permanently-broken `idx = j` and errors out before committing anything. O is therefore permanently unable to trigger `handle_finalized_payout`/`mark_payout_handled` for `idx = i`, and never obtains a `kickoff_txid` to walk the Reimburse path. This matches the "honest operator permanently unable to be reimbursed" Critical impact category, and blocks the operator's entire subsequent queue (every `idx > j`), not just one payout.

### Likelihood Explanation
Reachability depends entirely on whether the stated precondition (a stale/misattributed `payout_payer_operator_xonly_pk = O` for an idx O never actually paid) can occur through the referenced RBF/reorg attribution scenario. That mechanism was not independently reverified here. Assuming that precondition is achievable, the downstream lock-up requires no further attacker action — it is a deterministic consequence of the ASC/LIMIT-1 query design and the non-committing error path in `run_once`.

### Recommendation
Decouple queue liveness from a single stuck entry: (1) when `handle_finalized_payout`/`get_deposit_data_with_move_tx` fails for a given `idx`, mark that row with an explicit error/quarantine state distinct from "unhandled" so subsequent polls skip it (e.g., add an `error`/`is_payout_disputed` column checked by the `WHERE` clause), rather than relying on `is_payout_handled` alone; (2) have `get_first_unhandled_payout_by_operator_xonly_pk` return/attempt multiple candidate rows (or the task loop continue past a caught, logged error) instead of hard-failing `run_once` on one row; (3) ensure `payout_payer_operator_xonly_pk` writes in `update_payout_txs_and_payer_operator_xonly_pk` can only ever be set for a txid/operator pair verified against that operator's actual signed kickoff/round data, closing the root misattribution.

### Proof of Concept
```rust
// core/src/task/payout_checker.rs tests (conceptual, using existing test harness for Database + Operator)
#[tokio::test]
async fn stuck_idx_blocks_later_legit_payout() {
    // 1. Insert withdrawals row idx=j: payout_txid = Some(<some unrelated txid>),
    //    payout_payer_operator_xonly_pk = Some(O.xonly_pk), is_payout_handled = false,
    //    but with NO matching deposit_data/kickoff signed by O for that move_to_vault_txid.
    // 2. Insert withdrawals row idx=i (i > j): properly fronted by O with valid deposit_data
    //    and kickoff connector O actually created/signed.
    let mut task = PayoutCheckerTask::new(db.clone(), operator.clone());

    // 3. Run run_once() repeatedly (simulating the background loop).
    for _ in 0..5 {
        let result = task.run_once().await;
        assert!(result.is_err(), "run_once should error on unprocessable idx=j");
    }

    // 4. Assert idx=i was NEVER marked handled, proving O is permanently blocked
    //    despite legitimately funding idx=i.
    let handled = db.is_payout_handled(None, i).await.unwrap();
    assert!(!handled, "idx=i should be reachable but is perpetually blocked by stuck idx=j");
}
```
Note: step 1's setup (constructing a `payout_payer_operator_xonly_pk = O` row without a corresponding valid deposit/kickoff) relies on the separate, previously-referenced RBF/attribution issue to be reachable from attacker input in production; this PoC directly seeds the DB state to isolate and demonstrate the downstream head-of-line-blocking defect in `PayoutCheckerTask`/`get_first_unhandled_payout_by_operator_xonly_pk`.

### Citations

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

**File:** core/src/task/payout_checker.rs (L39-79)
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
```

**File:** core/src/operator.rs (L839-860)
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
```
