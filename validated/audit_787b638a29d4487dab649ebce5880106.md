### Title
Consumed L1 Handler Transaction Re-Added as Pending After Timelock Cleanup Enables Double Execution — (`crates/apollo_l1_provider/src/transaction_manager.rs`)

### Summary

After the `l1_handler_consumption_timelock_seconds` elapses, `clear_old_tx_from_consumed_queue()` permanently deletes a consumed L1 handler transaction's record from `records`. If the L1 scraper subsequently re-delivers the original `LogMessageToL2` event for that same transaction (e.g., after an L1 reorg or a scraper restart that replays from an earlier L1 block), `add_tx()` finds no existing record and creates a brand-new `Pending` record — bypassing every "already consumed / already committed" guard. The transaction re-enters the `proposable_index` and can be included in a new L2 block, producing a wrong state root, wrong receipts, and wrong storage values.

### Finding Description

**Root cause — `clear_old_tx_from_consumed_queue` erases the only consumed-guard:** [1](#0-0) 

The function removes every `tx_hash` whose `BlockTimestamp` is older than `unix_now − l1_handler_consumption_timelock_seconds` from both `consumed_queue` and `records`. After this call, no in-memory trace of the transaction's consumed/committed history remains.

**`add_tx` has no persistent guard against re-addition:** [2](#0-1) 

`create_record_if_not_exist` only checks whether the hash is currently present in `records`: [3](#0-2) 

Because the record was deleted, `Records::insert` succeeds and a fresh `TransactionRecord` is created with default state `Pending` and `committed = false`: [4](#0-3) 

`maintain_indices` then adds the transaction back to `proposable_index`: [5](#0-4) 

**The `mark_committed` double-commit assertion is silently bypassed:** [6](#0-5) 

This assertion fires only if the *same* record object is committed twice. Because the old record was deleted and a new one was created with `committed = false`, the assertion does not fire when the transaction is committed a second time.

**Trigger path (L1 reorg or scraper restart):**

The L1 scraper continuously polls for events: [7](#0-6) 

After an L1 reorg at a block before the original `LogMessageToL2` event, or after a scraper restart that replays from an earlier checkpoint, the scraper re-delivers the `LogMessageToL2` event. If the `ConsumedMessageToL2` event has not yet been re-delivered in the same scraping batch, the transaction is `Pending` and proposable.

### Impact Explanation

The re-proposed L1 handler transaction is executed a second time by the blockifier. The resulting L2 state diff — including storage writes, emitted events, and fee transfers — is committed to `apollo_storage` under a wrong state root. Any RPC query against that state (balance reads, storage reads, event logs) returns an authoritative-looking wrong value. The L1 verifier will ultimately reject the proof for the block containing the double-execution, but the sequencer's local committed state is already corrupted, requiring a revert and re-sync.

This matches: **Critical — Wrong state, receipt, event, L1 message, storage value from blockifier/syscall/execution logic for accepted input**, and **High — RPC execution returns an authoritative-looking wrong value**.

### Likelihood Explanation

Short L1 reorgs (1–3 blocks) are routine on Ethereum. If a `LogMessageToL2` event falls in a reorged block and the corresponding `ConsumedMessageToL2` event (emitted after L2 proof verification) falls in a later block that is also reorged, the scraper will re-deliver the `LogMessageToL2` without the `ConsumedMessageToL2`. If the consumption timelock has already expired and the record was deleted, the transaction re-enters the proposable index. The proposal cooldown (`l1_handler_proposal_cooldown_seconds`) provides a narrow window of protection, but with a zero or short cooldown the transaction can be re-proposed immediately.

### Recommendation

Maintain a separate, persistent set of transaction hashes that were ever committed or consumed, and check this set in `add_tx()` before creating a new record. Alternatively, never delete records for transactions that reached the `Committed` state — only records that were consumed without ever being committed on L2 should be eligible for cleanup. A minimal fix is:

```rust
// In add_tx(), before create_record_if_not_exist:
if self.previously_committed_or_consumed.contains(&tx_hash) {
    warn!("Ignoring re-scraped tx {tx_hash}: previously committed/consumed.");
    return;
}
```

The persistent set can be bounded by the same timelock used for `consumed_queue`, but must not be cleared until *after* the scraper's replay window (i.e., the maximum L1 reorg depth) has passed.

### Proof of Concept

```
// Sequence that triggers the bug:
// 1. tx H scraped → add_tx() → records[H] = Pending
// 2. get_txs() → H proposed → commit_block([H]) → records[H] = Committed
// 3. ConsumedMessageToL2 scraped → consume_tx(H) → records[H] = Consumed
//    consumed_queue[T] = [H]
// 4. clock advances past l1_handler_consumption_timelock_seconds
// 5. Next consume_tx() call triggers clear_old_tx_from_consumed_queue()
//    → records.remove(H)   ← H is now gone; all history erased
// 6. L1 reorg: scraper re-delivers LogMessageToL2 for H
//    → add_tx(H) → create_record_if_not_exist(H) succeeds (H absent)
//    → new record: state=Pending, committed=false
//    → maintain_indices: H added to proposable_index
// 7. get_txs() returns H again → H included in new L2 block N+K
//    → blockifier executes H a second time → wrong state root stored
//    → mark_committed() assertion does NOT fire (committed=false on new record)
``` [8](#0-7) [6](#0-5) [2](#0-1)

### Citations

**File:** crates/apollo_l1_provider/src/transaction_manager.rs (L171-204)
```rust
    pub fn add_tx(
        &mut self,
        tx: L1HandlerTransaction,
        block_timestamp: BlockTimestamp,
        scrape_timestamp: UnixTimestamp,
    ) {
        let tx_hash = tx.tx_hash;
        // If exists, return false and do nothing. If not, create the record as a HashOnly payload.
        let is_new_record = self.create_record_if_not_exist(tx_hash);
        // Replace a HashOnly payload with a Full payload. Do not update a Full payload.
        // A hash only payload can come from catching up from state sync, and then updated by
        // add_events from the scraper. However, if we get the same full tx twice (from the scraper)
        // it could indicate a double-scrape, and may cause the tx to be re-added to the proposable
        // index.
        self.with_record(tx_hash, move |record| match &record.tx {
            TransactionPayload::HashOnly(_) => {
                if !is_new_record {
                    info!(
                        "Transaction {tx_hash} already exists as a HashOnly payload. It was \
                         probably gotten via state sync component, and is now updated with a Full \
                         payload."
                    );
                }
                record.tx.set(tx, block_timestamp, scrape_timestamp);
            }
            TransactionPayload::Full { tx: _, created_at_block_timestamp: _, scrape_timestamp } => {
                warn!(
                    "Transaction {tx_hash} already exists as a Full payload, scraped at \
                     {scrape_timestamp}. This could indicate a double scrape. Ignoring the new \
                     transaction."
                );
            }
        });
    }
```

**File:** crates/apollo_l1_provider/src/transaction_manager.rs (L242-279)
```rust
    pub fn consume_tx(
        &mut self,
        tx_hash: TransactionHash,
        consumed_at: BlockTimestamp,
        unix_now: u64,
    ) -> Result<(), BlockTimestamp> {
        self.clear_old_tx_from_consumed_queue(unix_now);

        let Some(record) = self.records.get(&tx_hash) else {
            debug!(
                "Attempted to consume an unknown transaction: {tx_hash}. This can happen if the \
                 transaction was too old to be scraped (e.g. it was created before we started \
                 scraping)."
            );
            return Ok(());
        };

        // Double consumption is a bug.
        if let Some(previously_consumed_at) = record.get_consumed_at_timestamp() {
            return Err(previously_consumed_at);
        }

        // Mark the transaction as consumed.
        self.with_record(tx_hash, |record| record.mark_consumed(consumed_at));
        Ok(())
    }

    pub fn clear_old_tx_from_consumed_queue(&mut self, unix_now: u64) {
        let cutoff =
            unix_now.saturating_sub(self.config.l1_handler_consumption_timelock_seconds.as_secs());
        let still_timelocked = self.consumed_queue.split_off(&BlockTimestamp(cutoff));
        let passed_timelock = std::mem::replace(&mut self.consumed_queue, still_timelocked);
        for tx_hashes in passed_timelock.values() {
            for tx_hash in tx_hashes {
                self.records.remove(tx_hash);
            }
        }
    }
```

**File:** crates/apollo_l1_provider/src/transaction_manager.rs (L346-348)
```rust
    fn create_record_if_not_exist(&mut self, hash: TransactionHash) -> bool {
        self.records.insert(hash, TransactionRecord::new(hash.into()))
    }
```

**File:** crates/apollo_l1_provider/src/transaction_manager.rs (L370-376)
```rust
            if record.is_proposable() {
                // Assumption: txs will only be added to the index once, on arrival, so this
                // preserves arrival order.
                let tx_hashes = self.proposable_index.entry(scrape_timestamp).or_default();
                if !tx_hashes.contains(&tx_hash) {
                    tx_hashes.push(tx_hash);
                }
```

**File:** crates/apollo_l1_provider/src/transaction_record.rs (L34-39)
```rust
impl TransactionRecord {
    /// Create a new transaction record from a transaction payload, epoch is 0 by default, allowing
    /// the transaction to always be stageable, since the transaction manager's epoch starts at one.
    pub fn new(payload: TransactionPayload) -> Self {
        Self::from(payload)
    }
```

**File:** crates/apollo_l1_provider/src/transaction_record.rs (L50-60)
```rust
    pub fn mark_committed(&mut self) {
        // Can't return error because committing only part of a block leaves the provider in an
        // undetermined state.
        assert!(
            !self.committed,
            "L1 handler transaction {} committed twice, this may lead to l2 reorgs,",
            self.tx.tx_hash()
        );
        self.state = TransactionState::Committed;
        self.committed = true;
    }
```

**File:** crates/apollo_l1_provider/src/l1_provider.rs (L139-147)
```rust
        for event in events {
            match event {
                Event::L1HandlerTransaction {
                    l1_handler_tx,
                    block_timestamp,
                    scrape_timestamp,
                } => {
                    self.tx_manager.add_tx(l1_handler_tx, block_timestamp, scrape_timestamp);
                }
```
