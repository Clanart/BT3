### Title
`PayoutCheckerTask` atomically couples kickoff-connector assignment with external Citrea LCP fetch, permanently blocking operator reimbursement when the light-client prover is unavailable — (File: `core/src/task/payout_checker.rs`)

---

### Summary

`PayoutCheckerTask::run_once()` wraps kickoff-connector assignment, tx-sender queue insertion, LCP fetch from the external Citrea light-client prover, round advancement, and the final `mark_payout_handled` call inside a **single uncommitted database transaction**. If `fetch_validate_and_store_lcp` fails (prover unavailable, proof not yet generated for that L1 height, or network error), the `?` propagates, the entire `dbtx` is rolled back, and `is_payout_handled` is never set to `TRUE` / `kickoff_txid` is never written. The payout stays permanently unhandled. Because `get_reimbursement_txs` — the only production path to initiate a kickoff — gates on `kickoff_txid IS NOT NULL` via `validate_payer_is_operator`, and `InternalFinalizedPayout` is test-only, the operator has **no alternative path** to start the reimbursement process and recover the BTC they already fronted.

---

### Finding Description

**Exact code path:**

`PayoutCheckerTask::run_once()` (`core/src/task/payout_checker.rs`, lines 39–111):

```
dbtx = begin_transaction()
kickoff_txid = operator.handle_finalized_payout(&mut dbtx, ...)   // assigns kickoff connector,
                                                                    // queues Kickoff/Reimburse/etc.
                                                                    // txs into tx_sender via dbtx
(_, payout_block_height) = db.get_block_info_from_hash(...)
let _ = citrea_client.fetch_validate_and_store_lcp(               // ← EXTERNAL CALL
    payout_block_height, citrea_idx, &db, Some(&mut dbtx), ...
).await?;                                                          // ← if this fails, dbtx is
                                                                    //   dropped without commit
operator.end_round(&mut dbtx)
db.mark_payout_handled(&mut dbtx, citrea_idx, kickoff_txid)       // never reached
dbtx.commit()                                                      // never reached
```

`fetch_validate_and_store_lcp` (`core/src/citrea.rs`, lines 326–361) calls `get_light_client_proof_by_l1_height` on the external Citrea light-client prover. If the prover returns `None` for that L1 height it immediately returns:

```rust
None => return Err(eyre::eyre!(
    "Light client proof could not be fetched found for block height {}",
    payout_block_height
).into())
```

This error propagates through `?` in `run_once`, causing the entire function to return `Err`. The `dbtx` is dropped without `commit()`, rolling back every DB write including the kickoff-connector reservation and the tx-sender queue entries (both use the same `dbtx` via `insert_try_to_send(&mut dbtx, ...)`).

**Why the manual path is also blocked:**

`get_reimbursement_txs` (`core/src/operator.rs`, lines 2098–2149) calls `validate_payer_is_operator` (`core/src/operator.rs`, lines 1686–1740), which reads `kickoff_txid` from the `withdrawals` table:

```rust
"SELECT w.payout_payer_operator_xonly_pk, w.payout_tx_blockhash, w.kickoff_txid
 FROM withdrawals w ..."
```

and hard-fails if `kickoff_txid` is `None`:

```rust
_ => return Err(eyre::eyre!(
    "Payer info not found for deposit, payout blockhash: {:?}, kickoff txid: {:?}",
    ...
).into());
```

`kickoff_txid` is written only by `mark_payout_handled` (`core/src/database/verifier.rs`, lines 348–362):

```sql
UPDATE withdrawals SET is_payout_handled = TRUE, kickoff_txid = $2 WHERE idx = $1
```

which is only called from `PayoutCheckerTask::run_once()` — after the failing LCP fetch. `InternalFinalizedPayout` (`core/src/rpc/operator.rs`, lines 373–421) calls `handle_finalized_payout` without the LCP fetch and would set `kickoff_txid`, but it is guarded by `if !cfg!(test)` and returns `permission_denied` in production.

**The LCP is not needed to initiate the kickoff.** It is only needed later if the operator is challenged and must produce asserts. Coupling it with the kickoff assignment is the structural defect.

---

### Impact Explanation

An operator that has already broadcast a payout transaction (fronting `bridge_amount` BTC — 10 BTC in the default paramset) to a user cannot recover those funds if `PayoutCheckerTask` never succeeds:

1. The payout tx is on-chain; the operator has spent their BTC.
2. `PayoutCheckerTask` retries every 60 s but always fails at the LCP fetch.
3. `kickoff_txid` is never written; `is_payout_handled` stays `FALSE`.
4. `get_reimbursement_txs` (automation and manual paths) always returns an error.
5. The kickoff tx is never queued or broadcast; the reimbursement chain never starts.
6. The operator's fronted BTC is permanently locked with no on-chain or off-chain recovery path available in production code.

Additionally, `get_next_txs_to_send` (`core/src/operator.rs`, lines 1800–1835) also calls `fetch_validate_and_store_lcp` with `?` when the kickoff is not yet on-chain, meaning even if `kickoff_txid` were somehow set, the manual path would hit the same external-dependency block before returning the kickoff tx.

---

### Likelihood Explanation

The Citrea light-client prover is an external HTTP service. Failure modes include:

- **Temporary:** prover restart, network partition, rate-limiting — causes repeated task failures until the prover recovers; operator reimbursement is delayed but eventually succeeds.
- **Permanent for a specific L1 height:** prover bug that skips or permanently fails to generate a proof for the exact Bitcoin block that contained the payout tx. In this case the operator's BTC is permanently locked.

The temporary case is operationally likely (any deployment will experience prover downtime). The permanent case is less likely but has no mitigation in the current code. The `BufferedErrors` wrapper (`core/src/task/mod.rs`, lines 223–287) will eventually terminate the task thread after `error_overflow_limit` consecutive failures, stopping retries entirely.

---

### Recommendation

Separate the two logically independent operations into two committed transactions:

1. **Commit kickoff assignment first** — `handle_finalized_payout` + `mark_payout_handled` in one `dbtx.commit()`. This makes the payout handled and the kickoff tx queued regardless of LCP availability.
2. **Fetch and store the LCP independently** — in a separate task or a subsequent step that can be retried without blocking the kickoff. The LCP is only needed when the operator must respond to a challenge, which happens after the kickoff is already on-chain.

Alternatively, treat the LCP fetch as a best-effort operation: catch its error, log a warning, and proceed to `mark_payout_handled`. The LCP can be fetched lazily in `send_asserts` (which already calls `fetch_validate_and_store_lcp` itself at `core/src/operator.rs`, lines 1315–1324).

---

### Proof of Concept

1. Operator fronts a withdrawal; payout tx is confirmed in Bitcoin block `H`.
2. `PayoutCheckerTask` detects the unhandled payout (`is_payout_handled = FALSE`, `payout_txid IS NOT NULL`).
3. `handle_finalized_payout` succeeds: kickoff connector reserved, kickoff/reimburse txs signed and queued in `dbtx`.
4. `fetch_validate_and_store_lcp(H, ...)` calls the Citrea light-client prover for block `H`; prover returns `None` (proof not yet generated or permanently unavailable).
5. `run_once` returns `Err`; `dbtx` is dropped without commit; all DB writes are rolled back.
6. `mark_payout_handled` was never called; `kickoff_txid` remains `NULL`.
7. Task retries every 60 s; same failure repeats.
8. Operator calls `GetReimbursementTxs` RPC → `validate_payer_is_operator` → `kickoff_txid IS NULL` → error returned.
9. No kickoff tx is ever broadcast; no reimbursement chain starts; operator's fronted BTC is locked. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

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

**File:** core/src/citrea.rs (L326-361)
```rust
    async fn fetch_validate_and_store_lcp(
        &self,
        payout_block_height: u64,
        deposit_index: u32,
        db: &Database,
        mut dbtx: Option<DatabaseTransaction<'_>>,
        paramset: &'static ProtocolParamset,
    ) -> Result<Receipt, BridgeError> {
        let saved_data = db
            .get_lcp_for_assert(dbtx.as_deref_mut(), deposit_index)
            .await?;
        if let Some(lcp) = saved_data {
            // if already saved, do nothing
            return Ok(lcp);
        };

        let lcp_result = self
            .get_light_client_proof(payout_block_height, paramset)
            .await?;
        let (_lcp, lcp_receipt, _l2_height) = match lcp_result {
            Some(lcp) => lcp,
            None => {
                return Err(eyre::eyre!(
                    "Light client proof could not be fetched found for block height {}",
                    payout_block_height
                )
                .into())
            }
        };

        // save the LCP for assert
        db.insert_lcp_for_assert(dbtx, deposit_index, lcp_receipt.clone())
            .await?;

        Ok(lcp_receipt)
    }
```

**File:** core/src/operator.rs (L1703-1728)
```rust
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
```

**File:** core/src/operator.rs (L1826-1835)
```rust
                    let _ = self
                        .citrea_client
                        .fetch_validate_and_store_lcp(
                            payout_block_height as u64,
                            citrea_idx as u32,
                            &self.db,
                            dbtx.as_deref_mut(),
                            self.config.protocol_paramset(),
                        )
                        .await?;
```

**File:** core/src/operator.rs (L2116-2119)
```rust
        // validate payer is operator and get payer xonly pk, payout blockhash and kickoff txid
        let (payout_blockhash, kickoff_txid) = self
            .validate_payer_is_operator(Some(&mut dbtx), deposit_id)
            .await?;
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

**File:** core/src/rpc/operator.rs (L378-382)
```rust
        if !cfg!(test) {
            return Err(Status::permission_denied(
                "This method is only available in tests",
            ));
        }
```
