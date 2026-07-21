### Title
Consumed L1 Handler Transactions Re-enter `Pending` State After Timelock Deletion, Enabling Duplicate L2 Execution — (`crates/apollo_l1_provider/src/transaction_manager.rs`)

---

### Summary

After `l1_handler_consumption_timelock_seconds` expires, `clear_old_tx_from_consumed_queue` permanently removes a consumed L1 handler transaction's record from `records`. If the L1 scraper subsequently re-scrapes the original `LogMessageToL2` event (e.g., after a restart), `add_tx` re-creates the record as `Pending` with no memory of its prior consumed state. The transaction then passes `validate_tx`, is returned by `get_txs`, and is re-executed by the blockifier — producing a wrong L2 state, wrong state root, and wrong block hash.

---

### Finding Description

`TransactionManager` maintains `records: Records`, an `IndexMap<TransactionHash, TransactionRecord>`. When a consumed transaction's timelock expires, `clear_old_tx_from_consumed_queue` calls `self.records.remove(tx_hash)`, permanently erasing all lifecycle metadata: [1](#0-0) 

After removal, the hash is absent from `records`. When the scraper re-delivers the same `LogMessageToL2` event, `add_tx` calls `create_record_if_not_exist`: [2](#0-1) 

`Records::insert` only skips insertion if the hash is already present: [3](#0-2) 

Since the hash was deleted, a fresh `TransactionRecord` is created with default state `Pending` and `consumed_at: None`. The `HashOnly → Full` upgrade path in `add_tx` then fires: [4](#0-3) 

After the upgrade, `with_record` calls `maintain_indices`. Because `is_proposable()` returns `true` for `Pending` state, the tx is inserted into `proposable_index`: [5](#0-4) 

The scraper re-scrapes old events during restart/re-initialization. The `initialize` function in `l1_scraper.rs` fetches events from a configurable start block: [6](#0-5) 

If that start block predates the L1 block where the consumed tx was emitted, the `LogMessageToL2` event is re-delivered to `add_events`, triggering the re-addition path above.

**Contrast with rejected transactions:** rejected records are kept in `records` with `TransactionState::Rejected` indefinitely, so `Records::insert` returns `false` and `add_tx` silently ignores any re-scrape. The test `add_new_transaction_not_added_if_rejected` explicitly verifies this protection: [7](#0-6) 

Consumed transactions have no equivalent permanent tombstone after the timelock expires.

The `default()` implementation sets all timelocks to 0 seconds: [8](#0-7) 

This means any configuration that inherits the default deletes consumed records immediately, making any re-scrape a trigger.

---

### Impact Explanation

After re-addition, `validate_tx` returns `ValidationStatus::Validated` because `is_validatable()` returns `true` for `Pending` state (it only blocks `Committed`, `CancelledOnL2`, and `Consumed`): [9](#0-8) 

The blockifier's `execute_raw` for L1 handler transactions does not check whether the L1 message nonce was already consumed on L2: [10](#0-9) 

The transaction is re-executed, producing:
- Duplicate storage writes, balance changes, and events
- A wrong state root committed to storage
- A wrong block hash propagated through consensus
- Direct fund loss if the handler mints tokens or transfers assets

This matches **Critical: Wrong state, receipt, event, L1 message, storage value from blockifier/syscall/execution logic for accepted input**.

---

### Likelihood Explanation

The trigger is a scraper restart with a checkpoint older than `l1_handler_consumption_timelock_seconds`. This is a routine operational event: node crash, rolling restart, re-sync from an older snapshot. The consumption timelock is finite; any restart that re-scrapes events older than the timelock will trigger the bug. With the default timelock of 0 seconds, any re-scrape of any consumed event triggers it immediately.

---

### Recommendation

Maintain a permanent tombstone set of consumed transaction hashes that is never pruned. In `add_tx`, check this set before `create_record_if_not_exist` and return early if the hash is present. This mirrors the existing protection for rejected transactions.

Alternatively, do not delete consumed records from `records`; keep them with `TransactionState::Consumed` state permanently. `validate_tx` already returns `InvalidValidationStatus::ConsumedOnL1` for consumed records: [11](#0-10) 

This would prevent re-addition without any other changes, at the cost of unbounded memory growth for consumed records (which the timelock was designed to avoid). A bounded tombstone set (e.g., keyed by L1 block number with a hard cap) would balance both concerns.

---

### Proof of Concept

```
1. add_events(Event::L1HandlerTransaction { tx: T, tx_hash: H, ... })
   → records[H] = TransactionRecord { state: Pending, consumed_at: None }

2. commit_txs([H], [])
   → records[H].state = Committed

3. add_events(Event::TransactionConsumed { tx_hash: H, timestamp: T0 })
   → records[H].state = Consumed, consumed_queue[T0] = [H]

4. Advance clock past l1_handler_consumption_timelock_seconds.
   Next consume_tx call triggers clear_old_tx_from_consumed_queue:
   → records.remove(H)   // H is now absent

5. Scraper restarts; re-scrapes LogMessageToL2 for H.
   add_events(Event::L1HandlerTransaction { tx: T, tx_hash: H, ... })
   → create_record_if_not_exist(H): Records::insert succeeds (H absent)
   → add_tx: HashOnly branch fires, upgrades to Full, state = Pending
   → maintain_indices: H inserted into proposable_index

6. validate(H, block_N) → ValidationStatus::Validated   ✓
   get_txs(1, now)       → [T]                           ✓
   blockifier.execute(T) → duplicate state changes, wrong state root, wrong block hash
```

### Citations

**File:** crates/apollo_l1_provider/src/transaction_manager.rs (L126-135)
```rust
            if !record.is_validatable() {
                match record.state {
                    TransactionState::Committed => {
                        InvalidValidationStatus::AlreadyIncludedOnL2.into()
                    }
                    TransactionState::CancelledOnL2 => {
                        InvalidValidationStatus::CancelledOnL2.into()
                    }
                    TransactionState::Consumed => InvalidValidationStatus::ConsumedOnL1.into(),
                    _ => unreachable!(),
```

**File:** crates/apollo_l1_provider/src/transaction_manager.rs (L185-203)
```rust
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
```

**File:** crates/apollo_l1_provider/src/transaction_manager.rs (L269-279)
```rust
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

**File:** crates/apollo_l1_provider/src/transaction_manager.rs (L422-428)
```rust
impl Default for TransactionManager {
    // Note that new will init the epoch at 1, not 0, this is because a 0 epoch in the transaction
    // manager will make new transactions automatically staged by default in the first block.
    fn default() -> Self {
        Self::new(Duration::from_secs(0), Duration::from_secs(0), Duration::from_secs(0))
    }
}
```

**File:** crates/apollo_l1_provider/src/transaction_record.rs (L179-181)
```rust
    pub fn is_validatable(&self) -> bool {
        !self.is_committed() && !self.is_cancelled() && !self.is_consumed()
    }
```

**File:** crates/apollo_l1_provider/src/transaction_record.rs (L281-289)
```rust
    pub fn insert(&mut self, hash: TransactionHash, record: TransactionRecord) -> bool {
        match self.0.entry(hash) {
            Entry::Occupied(_) => false,
            Entry::Vacant(entry) => {
                entry.insert(record);
                true
            }
        }
    }
```

**File:** crates/apollo_l1_provider/src/l1_scraper.rs (L208-223)
```rust
    async fn initialize(
        &mut self,
        historic_l2_height: BlockNumber,
    ) -> L1ScraperResult<L1BlockReference, BaseLayerType> {
        let (latest_l1_block, events) = self.fetch_events().await?;

        debug!("Latest L1 block for initialize: {latest_l1_block:?}");
        debug!("All events scraped during initialize: {events:?}");

        // If this gets too high, send in batches.
        let initialize_result =
            self.l1_provider_client.initialize(historic_l2_height, events).await;
        handle_client_error(initialize_result)?;

        Ok(latest_l1_block)
    }
```

**File:** crates/apollo_l1_provider/src/l1_provider_tests.rs (L544-565)
```rust
#[test]
fn add_new_transaction_not_added_if_rejected() {
    // Setup.
    let rejected_tx_id: TransactionHash = tx_hash!(1);
    let mut l1_provider = setup_rejected_transactions();

    // Add a new transaction that is already in the rejected set.
    l1_provider.add_events(vec![l1_handler_event(rejected_tx_id)]).unwrap();

    // Ensure that the rejected transaction is not re-added to the provider.
    let expected_l1_provider = L1ProviderContentBuilder::new()
        .with_txs([l1_handler(3)])
        .with_rejected([l1_handler(1)])
        .with_committed([l1_handler(2)])
        .with_height(BlockNumber(1))
        .build();
    expected_l1_provider.assert_eq(&l1_provider);

    // Ensure that the rejected transaction is not re-added to the provider, even if it is staged.
    l1_provider.validate(rejected_tx_id, BlockNumber(1)).unwrap();
    l1_provider.add_events(vec![l1_handler_event(rejected_tx_id)]).unwrap();
    expected_l1_provider.assert_eq(&l1_provider);
```

**File:** crates/blockifier/src/transaction/l1_handler_transaction.rs (L55-115)
```rust
impl<U: UpdatableState> ExecutableTransaction<U> for L1HandlerTransaction {
    fn execute_raw(
        &self,
        state: &mut TransactionalState<'_, U>,
        block_context: &BlockContext,
        _concurrency_mode: bool,
    ) -> TransactionExecutionResult<TransactionExecutionInfo> {
        let tx_context = Arc::new(block_context.to_tx_context(self));
        let limit_steps_by_resources = false;
        let l1_handler_bounds =
            block_context.versioned_constants.os_constants.l1_handler_max_amount_bounds;

        let mut remaining_gas = l1_handler_bounds.l2_gas.0;
        let mut context = EntryPointExecutionContext::new_invoke(
            tx_context.clone(),
            limit_steps_by_resources,
            SierraGasRevertTracker::new(GasAmount(remaining_gas)),
        );
        let l1_handler_payload_size = self.payload_size();

        // Create a copy of the state for the execution. It will be rolled back if the execution is
        // reverted or committed upon success.
        let mut execution_state = TransactionalState::create_transactional(state);
        let execution_result =
            self.run_execute(&mut execution_state, &mut context, &mut remaining_gas);
        match execution_result {
            Ok(execute_call_info) => {
                let receipt = TransactionReceipt::from_l1_handler(
                    &tx_context,
                    l1_handler_payload_size,
                    CallInfo::summarize_many(
                        execute_call_info.iter(),
                        &block_context.versioned_constants,
                    ),
                    &execution_state.to_state_diff()?,
                );

                // Enforce resource bounds.
                let fee_check_report = FeeCheckReport::check_all_gas_amounts_within_bounds(
                    &l1_handler_bounds,
                    &receipt.gas,
                );
                match fee_check_report {
                    Ok(()) => {
                        // Post-execution check passed, commit the execution.
                        execution_state.commit();
                        // TODO(Arni): Consider removing this check. It is covered by the starknet
                        // core contract.
                        let paid_fee = self.paid_fee_on_l1;
                        // For now, assert only that any amount of fee was paid.
                        // The error message still indicates the required fee.
                        if paid_fee == Fee(0) {
                            return Err(TransactionExecutionError::TransactionFeeError(Box::new(
                                TransactionFeeError::InsufficientFee {
                                    paid_fee,
                                    actual_fee: receipt.fee,
                                },
                            )));
                        }

                        Ok(l1_handler_tx_execution_info(execute_call_info, receipt, None))
```
