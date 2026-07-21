### Title
Non-Atomic `pending_classes` Clear / `pending_data` Write Creates Intermediate-State Window Readable by RPC Execution Paths - (File: crates/apollo_central_sync/src/pending_sync.rs)

### Summary

When the pending-sync task detects a new parent block hash, it clears `pending_classes` and then writes `pending_data` in two separate, non-atomic async lock acquisitions. RPC handlers for `starknet_estimateFee`, `starknet_simulateTransactions`, and `starknet_call` with `block_id = Tag::Pending` read those same two locks in two separate acquisitions. A concurrent RPC request can observe the intermediate state — cleared `pending_classes` paired with the old (or new) `pending_data` that still references class hashes — causing execution to fail to resolve a declared class and return an authoritative-looking wrong fee estimate or simulation result.

### Finding Description

**Write side — non-atomic two-step update in `get_pending_data`:** [1](#0-0) 

```rust
if current_pending_parent_hash != new_pending_parent_hash {
    pending_classes.write().await.clear();   // Step 1 — lock acquired, cleared, RELEASED
}
*pending_data.write().await = new_pending_data;  // Step 2 — separate lock acquired, written, RELEASED
```

Each `.await` releases the write guard before the next line runs. Between Step 1 and Step 2 there is a scheduler yield point where `pending_classes` is empty but `pending_data` still holds the old value (which contains `state_diff.declared_classes` referencing class hashes that were just evicted).

**Read side — non-atomic two-step read in `estimate_fee`:** [2](#0-1) 

```rust
let maybe_pending_data = if let BlockId::Tag(Tag::Pending) = block_id {
    Some(client_pending_data_to_execution_pending_data(
        read_pending_data(&self.pending_data, &storage_txn).await?,  // read #1 — lock released
        self.pending_classes.read().await.clone(),                   // read #2 — separate lock
    ))
```

The identical pattern appears in `simulate_transactions`: [3](#0-2) 

`client_pending_data_to_execution_pending_data` merges the two snapshots into a single `ExecutionPendingData`, placing `declared_classes` from the state diff alongside `classes` from `PendingClasses`: [4](#0-3) 

**Inconsistent snapshot that can be observed:**

| Timing | `pending_data` read | `pending_classes` read | Result |
|--------|--------------------|-----------------------|--------|
| After Step 1, before Step 2 | old data (has `declared_classes` for old block) | empty (cleared) | class hashes in state diff have no corresponding compiled class |
| After Step 2, before new classes are downloaded | new data (has `declared_classes` for new block) | empty (cleared) | same — new class hashes also absent |

In both cases `ExecutionPendingData.declared_classes` is non-empty while `ExecutionPendingData.classes` is empty. The blockifier's `ExecutionStateReader::get_compiled_class` will fail to find the compiled class, causing execution to error or produce a wrong result. [5](#0-4) 

### Impact Explanation

`starknet_estimateFee`, `starknet_simulateTransactions`, and `starknet_call` with `block_id = Tag::Pending` return an authoritative-looking wrong value (execution error or incorrect fee) to any caller that races the pending-sync transition. The response is indistinguishable from a legitimate execution failure, so callers (wallets, dApps, relayers) may incorrectly conclude a transaction is invalid or mis-price gas. This matches: **High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value.**

### Likelihood Explanation

The window exists on every block transition while the node is syncing pending data. The gap between `pending_classes.write().await.clear()` and `*pending_data.write().await = new_pending_data` is a real async yield point that any concurrent Tokio task can be scheduled into. Any client polling `starknet_estimateFee` or `starknet_simulateTransactions` at high frequency (common for wallets and bots) will eventually hit this window. No special privileges are required — any unprivileged RPC caller can trigger the inconsistent read.

### Recommendation

Acquire both write locks simultaneously before performing either mutation, so no intermediate state is ever visible:

```rust
if current_pending_parent_hash != new_pending_parent_hash {
    // Hold both guards at the same time to prevent any reader from
    // observing cleared classes with stale pending_data.
    let mut classes_guard = pending_classes.write().await;
    let mut data_guard   = pending_data.write().await;
    classes_guard.clear();
    *data_guard = new_pending_data;
} else {
    *pending_data.write().await = new_pending_data;
}
```

Alternatively, wrap both fields in a single `RwLock<(PendingData, PendingClasses)>` so every read and write is always atomic over the pair.

### Proof of Concept

1. Node is syncing; `pending_data` contains a pending block on top of block N with one declared Sierra class (`class_hash = 0xABC`); `pending_classes` contains the compiled class for `0xABC`.
2. A new block N is finalized. `get_pending_data` detects `current_pending_parent_hash != new_pending_parent_hash`.
3. `pending_classes.write().await.clear()` executes — `pending_classes` is now empty; lock released; Tokio yields.
4. Concurrently, an RPC request for `starknet_estimateFee` with `block_id = Tag::Pending` is scheduled.
5. `read_pending_data` acquires and releases the `pending_data` read lock — returns old `PendingData` with `declared_classes = [{class_hash: 0xABC, ...}]`.
6. `self.pending_classes.read().await.clone()` acquires and releases the `pending_classes` read lock — returns empty `PendingClasses`.
7. `client_pending_data_to_execution_pending_data` merges them: `declared_classes` references `0xABC` but `classes` is empty.
8. Blockifier calls `get_compiled_class(0xABC)` → not found in pending classes, not found in committed storage (not yet finalized) → execution errors.
9. `starknet_estimateFee` returns `TRANSACTION_EXECUTION_ERROR` for a transaction that would succeed under a consistent view, misleading the caller.

### Citations

**File:** crates/apollo_central_sync/src/pending_sync.rs (L163-166)
```rust
        if current_pending_parent_hash != new_pending_parent_hash {
            pending_classes.write().await.clear();
        }
        *pending_data.write().await = new_pending_data;
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1009-1016)
```rust
        let maybe_pending_data = if let BlockId::Tag(Tag::Pending) = block_id {
            Some(client_pending_data_to_execution_pending_data(
                read_pending_data(&self.pending_data, &storage_txn).await?,
                self.pending_classes.read().await.clone(),
            ))
        } else {
            None
        };
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1079-1086)
```rust
        let maybe_pending_data = if let BlockId::Tag(Tag::Pending) = block_id {
            Some(client_pending_data_to_execution_pending_data(
                read_pending_data(&self.pending_data, &storage_txn).await?,
                self.pending_classes.read().await.clone(),
            ))
        } else {
            None
        };
```

**File:** crates/apollo_rpc/src/pending.rs (L5-23)
```rust
pub(crate) fn client_pending_data_to_execution_pending_data(
    client_pending_data: ClientPendingData,
    pending_classes: PendingClasses,
) -> ExecutionPendingData {
    ExecutionPendingData {
        storage_diffs: client_pending_data.state_update.state_diff.storage_diffs,
        deployed_contracts: client_pending_data.state_update.state_diff.deployed_contracts,
        declared_classes: client_pending_data.state_update.state_diff.declared_classes,
        old_declared_contracts: client_pending_data.state_update.state_diff.old_declared_contracts,
        nonces: client_pending_data.state_update.state_diff.nonces,
        replaced_classes: client_pending_data.state_update.state_diff.replaced_classes,
        classes: pending_classes,
        timestamp: client_pending_data.block.timestamp(),
        l1_gas_price: client_pending_data.block.l1_gas_price(),
        l1_data_gas_price: client_pending_data.block.l1_data_gas_price(),
        l2_gas_price: client_pending_data.block.l2_gas_price(),
        l1_da_mode: client_pending_data.block.l1_da_mode(),
        sequencer: client_pending_data.block.sequencer_address(),
    }
```

**File:** crates/apollo_rpc_execution/src/state_reader.rs (L163-172)
```rust
    fn get_compiled_class_hash(&self, class_hash: ClassHash) -> StateResult<CompiledClassHash> {
        if let Some(pending_data) = &self.maybe_pending_data {
            for DeclaredClassHashEntry { class_hash: other_class_hash, compiled_class_hash } in
                &pending_data.declared_classes
            {
                if class_hash == *other_class_hash {
                    return Ok(*compiled_class_hash);
                }
            }
        }
```
