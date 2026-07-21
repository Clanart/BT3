Looking at the FeiPool analog: a running total incremented on deposit but not decremented on withdrawal. I need to find the same pattern in the sequencer — a counter incremented when items enter a pool but not decremented when they leave via the commit/withdrawal path.

The closest structural match is in `TransactionPool` in `crates/apollo_mempool/src/transaction_pool.rs`.

---

### Title
`PoolSize::size_in_bytes` Not Decremented on Committed-Transaction Removal in `TransactionPool::remove_up_to_nonce_when_committed` — (`crates/apollo_mempool/src/transaction_pool.rs`)

### Summary

`TransactionPool` maintains a `PoolSize` counter (`size_in_bytes`) that is incremented on every `insert` and decremented in the single-transaction `remove` path. However, the batch-removal path used during block commit — `remove_up_to_nonce_when_committed` — removes transactions from every internal data structure **except** `PoolSize`. The counter therefore grows monotonically with each committed block. Because `Mempool::exceeds_capacity` gates all incoming transactions against this counter, the mempool will eventually report itself as full and reject every new transaction, even when the actual pool is empty.

### Finding Description

**Increment path** — `TransactionPool::insert`:

```rust
self.size.add(tx_size);   // PoolSize incremented
``` [1](#0-0) 

**Single-removal path** — `TransactionPool::remove` (eviction / rejection):

```rust
self.remove_from_account_mapping(&removed_tx);
self.remove_from_timed_mapping(&removed_tx);
self.size.remove(tx.total_bytes());   // PoolSize decremented ✓
``` [2](#0-1) 

**Commit-removal path** — `TransactionPool::remove_up_to_nonce_when_committed`:

```rust
let removed_txs = self.txs_by_account.remove_up_to_nonce(address, nonce);
// ... latency metrics ...
self.remove_from_main_mapping(&removed_txs);
self.remove_from_timed_mapping(&removed_txs);
removed_txs.len()
// ← self.size.remove(...) is ABSENT
``` [3](#0-2) 

`remove_from_main_mapping` accepts `&[TransactionReference]`, which carries only `tx_hash / address / nonce / tip / max_l2_gas_price` — no byte-size information — so it cannot update `PoolSize` internally. The `PoolSize` struct itself has no side-channel update mechanism. [4](#0-3) 

`remove_up_to_nonce_when_committed` is the sole code path called during `Mempool::commit_block` to retire committed transactions:

```rust
let n_removed_txs = self.tx_pool.remove_up_to_nonce_when_committed(address, next_nonce);
``` [5](#0-4) 

**Capacity check** — `Mempool::exceeds_capacity`:

```rust
fn exceeds_capacity(&self, tx: &InternalRpcTransaction) -> bool {
    self.size_in_bytes() + tx.total_bytes() > self.config.static_config.capacity_in_bytes
}
``` [6](#0-5) 

where `size_in_bytes()` reads directly from the stale `tx_pool.size_in_bytes()`: [7](#0-6) 

### Impact Explanation

After each committed block, `size_in_bytes` retains the byte-weight of every committed transaction. Over time the counter exceeds `capacity_in_bytes`. From that point forward every call to `Mempool::add_tx` triggers `try_make_space`, which finds no evictable accounts (the pool may be empty) and returns `false`, causing `MempoolError::MempoolFull` for all incoming transactions. The gateway propagates this as a rejection to the submitter. No privileged access is required; normal block production is sufficient to trigger the condition.

**Matching impact:** *High — Mempool/gateway/RPC admission rejects valid transactions before sequencing.*

### Likelihood Explanation

The condition is triggered by ordinary block commits. In a live network processing thousands of transactions per block, `size_in_bytes` will exceed any finite `capacity_in_bytes` within hours to days of operation, depending on the configured capacity and transaction throughput.

### Recommendation

In `remove_up_to_nonce_when_committed`, after `remove_from_main_mapping`, iterate over the removed transactions and subtract each one's byte size from `self.size`, mirroring the pattern in `remove`:

```rust
// After remove_from_main_mapping / remove_from_timed_mapping:
for tx_ref in &removed_txs {
    if let Some(tx) = self.tx_pool.get(&tx_ref.tx_hash) {
        self.size.remove(tx.total_bytes());
    }
}
```

Alternatively, restructure `remove_from_main_mapping` to return the removed full transactions so their sizes can be subtracted in one pass.

### Proof of Concept

1. Configure the mempool with `capacity_in_bytes = N`.
2. Submit transactions totalling `N` bytes; all are accepted.
3. Call `commit_block` with those transactions' addresses/nonces; they are removed from `tx_pool` and `txs_by_account`, but `size_in_bytes` remains `N`.
4. Submit any new transaction (even 1 byte): `exceeds_capacity` returns `true` (`N + 1 > N`), `try_make_space` finds no evictable accounts, and the transaction is rejected with `MempoolError::MempoolFull`.
5. The mempool is now permanently closed to new transactions despite being logically empty.

### Citations

**File:** crates/apollo_mempool/src/transaction_pool.rs (L60-94)
```rust
    pub fn insert(&mut self, tx: InternalRpcTransaction) -> MempoolResult<()> {
        let tx_reference = TransactionReference::new(&tx);
        let tx_hash = tx_reference.tx_hash;
        let tx_size = tx.total_bytes();

        // Insert to pool.
        if let hash_map::Entry::Vacant(entry) = self.tx_pool.entry(tx_hash) {
            entry.insert(tx);
        } else {
            return Err(MempoolError::DuplicateTransaction { tx_hash });
        }

        // Insert to account mapping.
        let unexpected_existing_tx = self.txs_by_account.insert(tx_reference);
        if unexpected_existing_tx.is_some() {
            panic!(
                "Transaction pool consistency error: transaction with hash {tx_hash} does not
                appear in main mapping, but transaction with same nonce appears in the account
                mapping",
            )
        };

        // Insert to timed mapping.
        let unexpected_existing_tx = self.txs_by_submission_time.insert(tx_reference);
        if unexpected_existing_tx.is_some() {
            panic!(
                "Transaction pool consistency error: transaction with hash {tx_hash} does not
                appear in main mapping, but transaction with same hash appears in the timed
                mapping",
            )
        };

        self.size.add(tx_size);

        Ok(())
```

**File:** crates/apollo_mempool/src/transaction_pool.rs (L97-110)
```rust
    pub fn remove(&mut self, tx_hash: TransactionHash) -> MempoolResult<InternalRpcTransaction> {
        // Remove from pool.
        let tx =
            self.tx_pool.remove(&tx_hash).ok_or(MempoolError::TransactionNotFound { tx_hash })?;

        // Remove reference from other mappings.
        let removed_tx = vec![TransactionReference::new(&tx)];
        self.remove_from_account_mapping(&removed_tx);
        self.remove_from_timed_mapping(&removed_tx);

        self.size.remove(tx.total_bytes());

        Ok(tx)
    }
```

**File:** crates/apollo_mempool/src/transaction_pool.rs (L114-150)
```rust
    pub fn remove_up_to_nonce_when_committed(
        &mut self,
        address: ContractAddress,
        nonce: Nonce,
    ) -> usize {
        fn update_metric(metric: &MetricHistogram, start: DateTime, end: DateTime) {
            let time_spent = (end - start).to_std().unwrap().as_secs_f64();
            metric.record(time_spent);
        }

        let removed_txs = self.txs_by_account.remove_up_to_nonce(address, nonce);

        let now = self.txs_by_submission_time.clock.now();
        for tx_ref in &removed_txs {
            let submission_id = self
                .get_submission_id(tx_ref.tx_hash)
                .expect("Transaction must still be in Mempool when recording commit latency");
            update_metric(
                &TRANSACTION_TIME_SPENT_UNTIL_COMMITTED,
                submission_id.submission_time,
                now,
            );

            if let Some(batching_time) = submission_id.batching_time {
                update_metric(
                    &TRANSACTION_TIME_SPENT_UNTIL_BATCHED,
                    submission_id.submission_time,
                    batching_time,
                );
            }
        }

        self.remove_from_main_mapping(&removed_txs);
        self.remove_from_timed_mapping(&removed_txs);

        removed_txs.len()
    }
```

**File:** crates/apollo_mempool/src/transaction_pool.rs (L327-351)
```rust
#[derive(Debug, Default, Eq, PartialEq, Clone)]
pub struct PoolSize {
    // Keeps track of the total size of the transactions in the pool.
    size_in_bytes: u64,
}

impl PoolSize {
    fn add(&mut self, tx_size_in_bytes: u64) {
        self.size_in_bytes = self
            .size_in_bytes
            .checked_add(tx_size_in_bytes)
            .expect("Overflow when adding to PoolCapacity size_in_bytes.");
    }

    fn remove(&mut self, tx_size_in_bytes: u64) {
        self.size_in_bytes = self
            .size_in_bytes
            .checked_sub(tx_size_in_bytes)
            .expect("Underflow when subtracting from PoolCapacity size_in_bytes.");
    }

    fn size_in_bytes(&self) -> u64 {
        self.size_in_bytes
    }
}
```

**File:** crates/apollo_mempool/src/mempool.rs (L625-627)
```rust
            // Remove from pool.
            let n_removed_txs = self.tx_pool.remove_up_to_nonce_when_committed(address, next_nonce);
            metric_count_committed_txs(n_removed_txs);
```

**File:** crates/apollo_mempool/src/mempool.rs (L876-878)
```rust
    fn size_in_bytes(&self) -> u64 {
        self.tx_pool.size_in_bytes() + self.delayed_declares.size_in_bytes()
    }
```

**File:** crates/apollo_mempool/src/mempool.rs (L880-883)
```rust
    // Returns true if the mempool will exceeds its capacity by adding the given transaction.
    fn exceeds_capacity(&self, tx: &InternalRpcTransaction) -> bool {
        self.size_in_bytes() + tx.total_bytes() > self.config.static_config.capacity_in_bytes
    }
```
