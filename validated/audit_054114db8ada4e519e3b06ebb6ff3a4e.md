## Analysis

Let me trace the exact code path to determine if the race is real and reachable.

**Writer side** (`pending_sync.rs`):

At line 182, `pending_data` is written atomically with the new state diff (including class C in `declared_classes`): [1](#0-0) 

Only *after* this write returns `DownloadedNewPendingData` does the loop spawn `get_pending_class` tasks: [2](#0-1) 

`get_pending_class` performs a network fetch before writing to `pending_classes`: [3](#0-2) 

The two writes are to **separate locks** with an unbounded async gap (network I/O) between them.

**Reader side** (`api_impl.rs` `simulate_transactions` / `estimate_fee`):

```rust
let maybe_pending_data = if let BlockId::Tag(Tag::Pending) = block_id {
    Some(client_pending_data_to_execution_pending_data(
        read_pending_data(&self.pending_data, &storage_txn).await?,  // lock 1
        self.pending_classes.read().await.clone(),                    // lock 2
    ))
``` [4](#0-3) 

These are two **separate** lock acquisitions with no atomicity guarantee between them.

`client_pending_data_to_execution_pending_data` then combines them: [5](#0-4) 

The resulting `ExecutionPendingData` has `declared_classes` containing C (from lock 1) but `classes` missing C's body (from lock 2, taken before `get_pending_class` completes).

**The TODO comment in the writer loop itself acknowledges the structural issue:** [6](#0-5) 

---

### Title
Pending Data / Pending Classes TOCTOU Race Causes Wrong Execution Result for Newly Declared Classes on Pending Block — (`crates/apollo_central_sync/src/pending_sync.rs`)

### Summary
`pending_data` and `pending_classes` are updated via separate, independent `RwLock` writes with an unbounded async gap (network I/O) between them. Any RPC caller requesting `estimate_fee`, `simulate_transactions`, or `call` on the pending block during this window receives an `ExecutionPendingData` where `declared_classes` contains class hash C but `classes` does not contain C's body, causing execution to fail with `UndeclaredClassHash` or return a wrong result for any transaction that uses C.

### Finding Description
In `get_pending_data` (line 182), `*pending_data.write().await = new_pending_data` atomically publishes a new pending state diff that includes class C in `declared_classes`. The write lock is then released. Only after this does the `sync_pending_data` loop spawn `get_pending_class(C, ...)`, which performs a network fetch (`central_source.get_class(class_hash).await`) before writing C's body into `pending_classes` (line 196). During the entire duration of that network fetch — potentially seconds — `pending_data` advertises C as declared while `pending_classes` has no entry for C.

On the RPC side, `simulate_transactions` and `estimate_fee` acquire `pending_data` and `pending_classes` in two separate `.read().await` calls (lines 1081–1082 of `api_impl.rs`). There is no combined snapshot or atomic read. The resulting `ExecutionPendingData` passed to the blockifier has `declared_classes = [C, ...]` but `classes = {}` (or missing C), so any execution path that looks up C's class body fails.

### Impact Explanation
Any unprivileged RPC caller invoking `starknet_estimateFee`, `starknet_simulateTransactions`, or `starknet_call` on `"block_id": "pending"` during the window receives a wrong authoritative-looking result: either an `UndeclaredClassHash` error for a class that is legitimately declared in the pending block, or incorrect execution output for transactions that depend on C. This matches: **High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value.**

### Likelihood Explanation
The window is open for the full duration of the `get_class` network round-trip to the central source (feeder gateway), typically hundreds of milliseconds to seconds. Any pending block that declares a new Sierra class triggers the window. The RPC is publicly accessible; no privileges are required to hit the race.

### Recommendation
Acquire both locks atomically before constructing `ExecutionPendingData`. The simplest fix is to hold the `pending_data` write lock until `pending_classes` has been populated for all newly declared classes before releasing it, or alternatively to snapshot both locks together on the read side under a single combined lock/wrapper. The TODO comment at line 69–70 already hints at the preferred direction: pass the pending data through the task result rather than re-reading from the lock, and only publish `pending_data` after all class bodies are available in `pending_classes`.

### Proof of Concept
1. Pending source returns a `PendingData` with `declared_classes = [{class_hash: C, compiled_class_hash: H}]`.
2. `get_pending_data` writes this to `pending_data` at line 182 and returns `DownloadedNewPendingData`.
3. The loop spawns `get_pending_class(C, ...)` which begins a slow network fetch.
4. Before the fetch completes, an RPC client calls `starknet_estimateFee` with a transaction that deploys or calls a contract using class C on the pending block.
5. `api_impl.rs` reads `pending_data` (sees C in `declared_classes`) then reads `pending_classes` (C absent).
6. `client_pending_data_to_execution_pending_data` produces `ExecutionPendingData { declared_classes: [C], classes: {} }`.
7. The blockifier attempts to look up C's class body, fails, and returns `UndeclaredClassHash` — a wrong result for a legitimately declared pending class.

### Citations

**File:** crates/apollo_central_sync/src/pending_sync.rs (L69-71)
```rust
                    // TODO(shahak): Consider getting the pending data from the task result instead
                    // of reading from the lock.
                    let pending_state_diff = &pending_data.read().await.state_update.state_diff;
```

**File:** crates/apollo_central_sync/src/pending_sync.rs (L77-87)
```rust
                for DeclaredClassHashEntry { class_hash, compiled_class_hash } in declared_classes {
                    if processed_classes.insert(class_hash) {
                        tasks.push(
                            get_pending_class(
                                class_hash,
                                central_source.clone(),
                                pending_classes.clone(),
                            )
                            .boxed(),
                        );
                    }
```

**File:** crates/apollo_central_sync/src/pending_sync.rs (L182-183)
```rust
        *pending_data.write().await = new_pending_data;
        Ok(PendingSyncTaskResult::DownloadedNewPendingData)
```

**File:** crates/apollo_central_sync/src/pending_sync.rs (L194-197)
```rust
) -> Result<PendingSyncTaskResult, StateSyncError> {
    let class = central_source.get_class(class_hash).await?;
    pending_classes.write().await.add_class(class_hash, class);
    Ok(PendingSyncTaskResult::DownloadedClassOrCompiledClass)
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1079-1083)
```rust
        let maybe_pending_data = if let BlockId::Tag(Tag::Pending) = block_id {
            Some(client_pending_data_to_execution_pending_data(
                read_pending_data(&self.pending_data, &storage_txn).await?,
                self.pending_classes.read().await.clone(),
            ))
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
