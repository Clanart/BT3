### Title
`push_ids_into_queue` can `expect()`-panic when held (not-yet-requeued) transactions push the map length past capacity - (File: core/src/banking_stage/transaction_scheduler/transaction_state_container.rs)

### Summary
The reported Rubicon bug is a class of "insert-without-registering-in-the-structure-that-removal/accounting relies on" defect: `SimpleMarket::offer` inserts an order into the order map but never links it into the sorted `_rank`/`_near` lists that `cancel()` depends on, so a later operation that assumes consistency between the two structures fails hard (revert). The Agave analog is in `TransactionStateContainer`, which keeps two structures that are supposed to stay in sync: the slab map `id_to_transaction_state` (all buffered transactions) and the `priority_queue` (`BTreeSet<TransactionPriorityId>`, only currently-schedulable transactions). `insert_map_only` and `hold_transaction` insert a transaction into the map/`held_transactions` vec **without** putting it into `priority_queue`, exactly like `SimpleMarket::offer` skipping `_rank`.<cite repo="Annirich/agave--025" path="core/src/banking_stage/transaction_scheduler/transaction_state_container.rs" start="266="273" end="273"/> [1](#0-0) 

### Finding Description
`push_ids_into_queue` inserts the given ids into `priority_queue`, then computes how many entries are over capacity using the size of the **map**, not the size of the **queue**:

```rust
let num_dropped = self
    .id_to_transaction_state
    .len()
    .saturating_sub(self.capacity);

for _ in 0..num_dropped {
    let priority_id = self.priority_queue.pop_first().expect("queue is not empty");
    self.remove_state(priority_id.id);
}
``` [2](#0-1) 

This is only safe if `priority_queue.len() >= id_to_transaction_state.len() - capacity` at the moment `push_ids_into_queue` runs. That invariant is broken by the "held" path: `retry_transaction` can leave a transaction in the map while stashing its id in `held_transactions` instead of the queue when `immediately_retryable` is `false`:

```rust
if immediately_retryable {
    self.push_ids_into_queue(std::iter::once(priority_id));
} else {
    self.hold_transaction(priority_id);
}
``` [3](#0-2) 

```rust
fn hold_transaction(&mut self, priority_id: TransactionPriorityId) {
    self.held_transactions.push(priority_id);
}
``` [1](#0-0) 

Held transactions remain counted in `id_to_transaction_state.len()` (they still occupy a slab slot) but are absent from `priority_queue` until `flush_held_transactions` runs:

```rust
fn flush_held_transactions(&mut self) {
    let mut held_transactions = core::mem::take(&mut self.held_transactions);
    self.push_ids_into_queue(held_transactions.drain(..));
    core::mem::swap(&mut self.held_transactions, &mut held_transactions);
}
``` [4](#0-3) 

Similarly, `insert_map_only` (used directly by the packet receive path) inserts into the slab but not the queue; the queue insertion happens in a later, separate step (`push_ids_into_queue`), leaving a window where the map count exceeds the queue count:

```rust
let transaction_id = container.insert_map_only(state);
let priority_id = TransactionPriorityId::new(priority, transaction_id);
...
receiving_stats.num_dropped_on_capacity +=
    container.push_ids_into_queue(std::iter::once(priority_id));
``` [5](#0-4) 

The invariant `map.len() - capacity <= queue.len()` that `push_ids_into_queue` silently assumes is therefore not guaranteed whenever "held" (not-yet-requeued) transactions exist alongside normal capacity pressure. If, at the moment `push_ids_into_queue` is called (e.g. for a newly-received packet or for `flush_held_transactions` itself), `id_to_transaction_state.len() - capacity` exceeds `priority_queue.len()`, the loop calls `pop_first()` more times than there are queue entries, hitting `.expect("queue is not empty")` and panicking the banking-stage thread — i.e. crashing the validator process.

### Impact Explanation
A panic inside `push_ids_into_queue`, executed on the banking-stage scheduling path, aborts the validator process (Rust panics in a non-catch-unwind context terminate the thread/process for this kind of core hot-path code). Because this path is driven purely by ordinary transaction/packet traffic reaching the node (no privileged access, no malicious peer needed — any client sending transactions that get "held" via `retry_transaction(..., immediately_retryable=false)`, e.g. account-lock-contended transactions being requeued for a later pass, can populate `held_transactions`), this qualifies as a non-RPC remote crash/exhaustion vector under the disclosed impact criteria.

### Likelihood Explanation
Likelihood depends on how easily an attacker can arrange: (1) enough transactions held via `hold_transaction`/`retry_transaction(false)` so that `id_to_transaction_state.len()` stays near/at `capacity` while several ids sit only in `held_transactions` (not in `priority_queue`), and (2) a subsequent `push_ids_into_queue` call (e.g. from receiving one more packet via `insert_map_only` + `push_ids_into_queue`, or from `flush_held_transactions` itself) whose newly computed `num_dropped` exceeds the current `priority_queue.len()`. This requires precise timing/volume control over container occupancy relative to `capacity`, which is plausible for a high-throughput unprivileged sender flooding a validator with transactions that repeatedly hit the "hold" branch (e.g. write-lock contention causing repeated non-immediate retries), but I could not fully trace every caller of `retry_transaction`/`hold_transaction` in the scheduler pipeline within the available search budget, so the exact traffic pattern needed to reliably trigger `held_transactions.len() > 0` while simultaneously forcing `id_to_transaction_state.len() - capacity > priority_queue.len()` is not fully confirmed from local code alone.

### Recommendation
Make `push_ids_into_queue`'s eviction count depend on `priority_queue.len()` (or on `min(priority_queue.len(), id_to_transaction_state.len() - capacity)`) instead of purely on `id_to_transaction_state.len() - capacity`, and/or account for `held_transactions.len()` when computing capacity pressure, so the eviction loop can never attempt to pop more entries than the queue actually holds. Replace the `.expect(...)` with a bounded loop (`while num_dropped > 0 && let Some(priority_id) = self.priority_queue.pop_first() { ... }`) as a defensive measure regardless of the invariant fix.

### Proof of Concept
Conceptual sequence (exact reproduction requires driving the real scheduler/consumer pipeline, which was not fully traced):
1. Fill the container to `capacity` transactions total in `id_to_transaction_state`, with several of them moved into `held_transactions` via `retry_transaction(id, tx, immediately_retryable=false)` (e.g. from repeated conflicting-lock retries), so `priority_queue.len() < id_to_transaction_state.len()`.
2. Submit one more transaction through the receive path, causing `insert_map_only` then `push_ids_into_queue` to run:
   - `id_to_transaction_state.len()` is now `capacity + 1`.
   - `num_dropped = 1`.
   - But if `priority_queue.len()` is already `0` (all remaining entries are held, not queued) at this instant, `priority_queue.pop_first()` returns `None`, and `.expect("queue is not empty")` panics.
3. The panic unwinds through the banking-stage scheduling code, aborting the validator process. [2](#0-1)

### Citations

**File:** core/src/banking_stage/transaction_scheduler/transaction_state_container.rs (L85-91)
```rust
        transaction_state.retry_transaction(transaction);

        if immediately_retryable {
            self.push_ids_into_queue(std::iter::once(priority_id));
        } else {
            self.hold_transaction(priority_id);
        }
```

**File:** core/src/banking_stage/transaction_scheduler/transaction_state_container.rs (L178-201)
```rust
    fn push_ids_into_queue(
        &mut self,
        priority_ids: impl Iterator<Item = TransactionPriorityId>,
    ) -> usize {
        for id in priority_ids {
            self.priority_queue.insert(id);
        }

        // The number of items in the `id_to_transaction_state` map is
        // greater than or equal to the number of elements in the queue.
        // To avoid the map going over capacity, we use the length of the
        // map here instead of the queue.
        let num_dropped = self
            .id_to_transaction_state
            .len()
            .saturating_sub(self.capacity);

        for _ in 0..num_dropped {
            let priority_id = self.priority_queue.pop_first().expect("queue is not empty");
            self.remove_state(priority_id.id);
        }

        num_dropped
    }
```

**File:** core/src/banking_stage/transaction_scheduler/transaction_state_container.rs (L203-205)
```rust
    fn hold_transaction(&mut self, priority_id: TransactionPriorityId) {
        self.held_transactions.push(priority_id);
    }
```

**File:** core/src/banking_stage/transaction_scheduler/transaction_state_container.rs (L214-218)
```rust
    fn flush_held_transactions(&mut self) {
        let mut held_transactions = core::mem::take(&mut self.held_transactions);
        self.push_ids_into_queue(held_transactions.drain(..));
        core::mem::swap(&mut self.held_transactions, &mut held_transactions);
    }
```

**File:** core/src/banking_stage/transaction_scheduler/receive_and_buffer.rs (L342-360)
```rust
            let transaction_id = container.insert_map_only(state);
            let priority_id = TransactionPriorityId::new(priority, transaction_id);

            // Now, if this is a nonce transaction, we know it is validated and higher-priority than any
            // which may exist in the priority queue. If one is queued, evict it. Regardless, record the
            // incoming nonce transaction's nonce as in-use.
            if let Some(nonce_address) = validated_nonce_address {
                if let Some(existing_nonce_priority_id) =
                    container.get_nonce_transaction_priority_id(&nonce_address)
                {
                    receiving_stats.num_evicted_on_nonce_dedup += 1;
                    container.remove_by_id(existing_nonce_priority_id.id);
                }
                container.set_nonce_transaction_priority_id(&nonce_address, priority_id);
            }

            // Transaction is already fully validated and can be inserted into priority queue.
            receiving_stats.num_dropped_on_capacity +=
                container.push_ids_into_queue(std::iter::once(priority_id));
```
