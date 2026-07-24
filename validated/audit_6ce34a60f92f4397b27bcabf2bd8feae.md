### Title
`bump_fees_of_unconfirmed_fee_payer_txs` Error Propagation Blocks All Pending Bridge Transactions — (`crates/clementine-tx-sender/src/cpfp.rs`, `crates/clementine-tx-sender/src/lib.rs`)

---

### Summary

In `try_to_send_unconfirmed_txs`, the call to `bump_fees_of_unconfirmed_fee_payer_txs` is placed **before** the main per-transaction sending loop and its error is propagated with `?`. Any unexpected RPC error for **any single** fee-payer UTXO causes the entire function to return early, silently skipping the dispatch of every pending bridge transaction — including time-critical ones such as `Disprove`, `ChallengeTimeout`, and `DisproveTimeout`.

---

### Finding Description

`TxSenderTaskInternal::run_once` calls `try_to_send_unconfirmed_txs`, which first calls `bump_fees_of_unconfirmed_fee_payer_txs` and propagates its error with `?` before entering the per-transaction loop:

```rust
// crates/clementine-tx-sender/src/lib.rs  ~L341-342
self.bump_fees_of_unconfirmed_fee_payer_txs(new_fee_rate)
    .await?;          // ← early return on any error

for id in txs {       // ← never reached if the call above fails
    ...
}
``` [1](#0-0) 

Inside `bump_fees_of_unconfirmed_fee_payer_txs`, the loop over **all** unconfirmed fee-payer UTXOs contains a hard `return Err(...)` when `get_mempool_entry` returns any error whose string representation does not contain the literal `"Transaction not in mempool"`:

```rust
// crates/clementine-tx-sender/src/cpfp.rs  ~L468-474
Err(e) => {
    if !e.to_string().contains("Transaction not in mempool") {
        return Err(
            eyre!("Failed to get mempool entry for {fee_payer_txid}: {e}").into(),
        );
    }
    ...
}
``` [2](#0-1) 

The function iterates over **all** unconfirmed fee-payer UTXOs across **all** queued transactions: [3](#0-2) 

A single fee-payer UTXO belonging to any low-priority transaction (e.g., a stuck CPFP parent) can therefore abort the entire sending pass.

The error propagates up through `run_once`: [4](#0-3) 

`BufferedErrors` absorbs up to 10 consecutive errors before terminating the task: [5](#0-4) 

`BackgroundTaskManager` logs the termination but does **not** restart the task: [6](#0-5) 

The `TxSender` is configured with `error_overflow_limit = 10` and a 30-second poll delay: [7](#0-6) 

---

### Impact Explanation

The tx-sender queue holds time-critical bridge transactions: `Disprove`, `ChallengeTimeout`, `DisproveTimeout`, and `Reimburse`. These transactions have on-chain timelocks enforced by Bitcoin script. If the TxSender task is blocked or stopped, these transactions are never broadcast.

- **Disprove not sent** → a fraudulent operator assertion goes unchallenged → the operator can claim the payout, draining bridged BTC from the bridge.
- **ChallengeTimeout / DisproveTimeout not sent** → the challenge-response protocol stalls, leaving operator collateral locked or allowing an invalid state to finalize.
- **Reimburse not sent** → reimbursement outputs remain unspent, locking bridge-controlled UTXOs.

The `Disprove` transaction is explicitly queued with `FeePayingType::NoFunding` and is dispatched inside the loop that is skipped: [8](#0-7) 

---

### Likelihood Explanation

The trigger condition is an RPC error from `get_mempool_entry` whose message does not match the hardcoded substring `"Transaction not in mempool"`. This can occur:

1. **Fragile string matching** — Bitcoin Core's JSON-RPC error for a missing mempool entry is `-5: Transaction not in mempool`. Any variation (e.g., a different locale, a proxy, or a future Bitcoin Core version changing the message) would not match the substring check and would trigger the hard return.
2. **Node restart / transient RPC failure** — a brief Bitcoin node restart returns a connection-refused or timeout error, which does not contain the expected substring.
3. **Fee-payer UTXO in inconsistent DB state** — if a fee-payer UTXO is recorded as unconfirmed in the DB but the corresponding transaction was replaced by an external wallet operation, `get_mempool_entry` may return an unexpected error on every iteration, causing 10 consecutive failures and permanent task termination.

---

### Recommendation

Replace the hard `return Err(...)` in `bump_fees_of_unconfirmed_fee_payer_txs` with a `tracing::warn!` + `continue`, matching the pattern already used for `bump_fee_with_fee_rate` errors in the same function:

```rust
Err(e) => {
    if !e.to_string().contains("Transaction not in mempool") {
        tracing::warn!(
            "Unexpected get_mempool_entry error for fee payer {fee_payer_txid}: {e}; skipping"
        );
        continue;   // ← do not abort the entire sending pass
    }
    ...
}
```

Additionally, the `mark_fee_payer_utxo_as_evicted` DB call at the end of the function also uses `?` and should similarly be demoted to a logged error with `continue` to prevent a DB hiccup from blocking all transactions: [9](#0-8) 

---

### Proof of Concept

1. The TxSender queue contains a pending `Disprove` transaction (queued by the verifier after detecting a fraudulent operator assertion).
2. A separate, unrelated CPFP transaction has an unconfirmed fee-payer UTXO in the DB.
3. The Bitcoin node is briefly restarted (or returns a connection error), causing `get_mempool_entry` for the fee-payer UTXO to return a connection-refused error — a string that does not contain `"Transaction not in mempool"`.
4. `bump_fees_of_unconfirmed_fee_payer_txs` hits the `return Err(...)` branch at line 472–474 of `cpfp.rs`.
5. `try_to_send_unconfirmed_txs` returns the error via `?` at line 342 of `lib.rs`, skipping the `for id in txs` loop entirely.
6. `run_once` returns the error; `BufferedErrors` absorbs it and returns `Ok(false)`.
7. Steps 3–6 repeat on every 30-second poll. After 10 consecutive failures, the `TxSender` task terminates permanently.
8. The `Disprove` transaction is never broadcast. The challenge window expires. The fraudulent operator assertion is accepted, and the payout is executed against the bridge's BTC.

### Citations

**File:** crates/clementine-tx-sender/src/lib.rs (L340-354)
```rust
        // bump fees of fee payer transactions that are unconfirmed
        self.bump_fees_of_unconfirmed_fee_payer_txs(new_fee_rate)
            .await?;

        if !txs.is_empty() {
            tracing::debug!("Trying to send {} sendable txs ", txs.len());
        }

        if std::env::var("TXSENDER_DBG_INACTIVE_TXS").is_ok() {
            self.db
                .debug_inactive_txs(get_sendable_txs_fee_rate, current_tip_height)
                .await;
        }

        for id in txs {
```

**File:** crates/clementine-tx-sender/src/cpfp.rs (L434-443)
```rust
    pub async fn bump_fees_of_unconfirmed_fee_payer_txs(&self, fee_rate: FeeRateKvb) -> Result<()> {
        let bumpable_txs = self
            .db
            .get_all_unconfirmed_fee_payer_txs(None)
            .await
            .map_err(|e: BridgeError| SendTxError::Other(e.into()))?;
        let mut not_evicted_ids = HashSet::new();
        let mut all_parent_ids = HashSet::new();

        for (id, try_to_send_id, fee_payer_txid, vout, amount, replacement_of_id) in bumpable_txs {
```

**File:** crates/clementine-tx-sender/src/cpfp.rs (L468-475)
```rust
                Err(e) => {
                    // If not in mempool we should ignore, it was either evicted or replaced by a bumped feepayer tx
                    // give an error if the error is not "Transaction not in mempool"
                    if !e.to_string().contains("Transaction not in mempool") {
                        return Err(
                            eyre!("Failed to get mempool entry for {fee_payer_txid}: {e}").into(),
                        );
                    }
```

**File:** crates/clementine-tx-sender/src/cpfp.rs (L540-547)
```rust
        for parent_id in all_parent_ids {
            if !not_evicted_ids.contains(&parent_id) {
                self.db
                    .mark_fee_payer_utxo_as_evicted(None, parent_id)
                    .await
                    .map_err(|e: BridgeError| SendTxError::Other(e.into()))?;
            }
        }
```

**File:** crates/clementine-tx-sender/src/task.rs (L43-49)
```rust
        self.inner
            .try_to_send_unconfirmed_txs(
                fee_rate,
                self.current_tip_height,
                self.last_processed_tip_height != self.current_tip_height,
            )
            .await?;
```

**File:** core/src/task/mod.rs (L268-281)
```rust
                if self.buffer.len() >= self.error_overflow_limit {
                    let mut base_error: eyre::Report =
                        self.buffer.pop().expect("just inserted above").into();

                    for error in std::mem::take(&mut self.buffer) {
                        base_error = base_error.wrap_err(error);
                    }

                    base_error = base_error.wrap_err(format!(
                        "Exiting due to {} consecutive errors, the following chain is the list of errors.",
                        self.error_overflow_limit
                    ));

                    Err(base_error.into())
```

**File:** core/src/task/manager.rs (L58-62)
```rust
                Ok(Err(e)) => {
                    // Task returned an error
                    tracing::error!("Task {task_variant:?} failed with error: {e:?}");
                    format!("Failed due to error: {e:?}")
                }
```

**File:** core/src/task/tx_sender.rs (L48-54)
```rust
impl crate::task::IntoTask for TxSender {
    type Task = WithDelay<BufferedErrors<TxSenderTask>>;
    fn into_task(self) -> Self::Task {
        TxSenderTask::new(self)
            .into_buffered_errors(10, 1, Duration::from_secs(10))
            .with_delay(POLL_DELAY)
    }
```

**File:** core/src/tx_sender_queue.rs (L165-176)
```rust
            TransactionType::Disprove => {
                self.insert_try_to_send(
                    dbtx,
                    tx_metadata,
                    signed_tx,
                    FeePayingType::NoFunding,
                    rbf_info,
                    &[],
                    &[],
                    &[],
                    &[],
                )
```
