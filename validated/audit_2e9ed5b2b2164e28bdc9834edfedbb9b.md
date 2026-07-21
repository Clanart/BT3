### Title
`processed_classes` HashSet Never Reset on Pending Parent Change Leaves `pending_classes` Incomplete, Causing Wrong Pending RPC Execution Results - (File: crates/apollo_central_sync/src/pending_sync.rs)

### Summary

In `sync_pending_data`, two deduplication sets — `processed_classes` and `processed_compiled_classes` — are initialized once and accumulate entries for the entire lifetime of the pending-sync loop. When a new finalized block causes the pending block's parent hash to change, `pending_classes` is correctly cleared, but the two deduplication sets are **never reset**. Any class hash that was already inserted into `processed_classes` will never be re-fetched, leaving `pending_classes` permanently empty for those hashes. Subsequent RPC calls that execute against the pending block (`starknet_call`, `starknet_estimateFee`, `starknet_simulateTransactions`) will fail or return wrong results because the class definition is absent.

### Finding Description

`sync_pending_data` in `crates/apollo_central_sync/src/pending_sync.rs` initializes two `HashSet`s at the top of the function:

```rust
let mut processed_classes = HashSet::new();
let mut processed_compiled_classes = HashSet::new();
``` [1](#0-0) 

Inside the loop, when `DownloadedNewPendingData` is received, each class hash from the new pending state diff is inserted into `processed_classes`. If `insert` returns `false` (already present), no fetch task is spawned:

```rust
if processed_classes.insert(class_hash) {
    tasks.push(get_pending_class(...).boxed());
}
if processed_compiled_classes.insert(compiled_class_hash) {
    tasks.push(get_pending_compiled_class(...).boxed());
}
``` [2](#0-1) 

When the pending block's parent hash changes (a new block was finalized), `get_pending_data` clears `pending_classes`:

```rust
if current_pending_parent_hash != new_pending_parent_hash {
    pending_classes.write().await.clear();
}
``` [3](#0-2) 

`pending_classes` is cleared, but `processed_classes` and `processed_compiled_classes` are **not cleared**. The invariant that `pending_classes` must contain every class declared in the current pending state diff is broken: any class hash that appeared in the old pending block and reappears in the new pending block will be silently skipped.

This is the direct analog of the `pendingWithdrawals` bug: `processed_classes` only grows (like `pendingWithdrawals` only incremented) and is never decremented/reset when the corresponding store (`pending_classes`) is wiped.

The incomplete `pending_classes` is consumed by `ExecutionStateReader` during pending-block execution: [4](#0-3) 

If a class is absent from `pending_classes` and also absent from finalized storage (because it was only declared in the discarded pending block), execution fails with a class-not-found error.

### Impact Explanation

Any RPC endpoint that executes against the pending block — `starknet_call`, `starknet_estimateFee`, `starknet_simulateTransactions` — will return an authoritative-looking error ("class not found") for transactions that use a class declared in the pending state diff. The caller cannot distinguish this from a genuine execution failure. This matches:

> **High. RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value.**

### Likelihood Explanation

The trigger is normal network operation:

1. User submits a `Declare` transaction for class `C1`; it lands in pending block `P1` (parent = block `A`).
2. `processed_classes` = `{C1}`, `pending_classes` = `{C1: <class_def>}`.
3. A new block `A+1` is finalized **without** including the `Declare` for `C1` (e.g., the block was full, or a different proposer built it). `P1` is discarded; `C1` is not in finalized storage.
4. The user's `Declare` transaction is still in the mempool and is included in the new pending block `P2` (parent = block `A+1`).
5. `get_pending_data` detects `current_pending_parent_hash != new_pending_parent_hash`, clears `pending_classes` → `{}`, returns `DownloadedNewPendingData`.
6. Loop processes `P2`'s state diff: `processed_classes.insert(C1)` → `false` (already present). No fetch task is spawned.
7. `pending_classes` remains `{}` for `C1`.
8. Any `starknet_call` / `starknet_estimateFee` on the pending block that touches `C1` fails.

This scenario requires no special privileges — only a normal declare transaction and ordinary block production.

### Recommendation

Clear `processed_classes` and `processed_compiled_classes` whenever `pending_classes` is cleared:

```rust
if current_pending_parent_hash != new_pending_parent_hash {
    pending_classes.write().await.clear();
    processed_classes.clear();           // add this
    processed_compiled_classes.clear();  // add this
}
``` [5](#0-4) 

### Proof of Concept

1. Run a node with central sync and pending sync enabled.
2. Submit a `Declare` transaction for class `C1`; confirm it appears in `pending_data.state_update.state_diff.declared_classes`.
3. Observe `processed_classes` = `{C1}` and `pending_classes.classes` = `{C1: <def>}`.
4. Allow a new block to be finalized that does **not** include the `Declare` for `C1`.
5. Confirm `pending_classes` is cleared (parent hash changed) but `processed_classes` still contains `C1`.
6. Confirm the `Declare` for `C1` reappears in the new pending block's state diff.
7. Observe that no `get_pending_class` task is spawned for `C1` (the `insert` returns `false`).
8. Call `starknet_estimateFee` with `block_id = "pending"` for a transaction that deploys or calls a contract of class `C1`.
9. The call returns a class-not-found error instead of a valid fee estimate, demonstrating the wrong authoritative value.

### Citations

**File:** crates/apollo_central_sync/src/pending_sync.rs (L62-63)
```rust
    let mut processed_classes = HashSet::new();
    let mut processed_compiled_classes = HashSet::new();
```

**File:** crates/apollo_central_sync/src/pending_sync.rs (L77-97)
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
                    if processed_compiled_classes.insert(compiled_class_hash) {
                        tasks.push(
                            get_pending_compiled_class(
                                class_hash,
                                central_source.clone(),
                                pending_classes.clone(),
                            )
                            .boxed(),
                        );
                    }
```

**File:** crates/apollo_central_sync/src/pending_sync.rs (L179-183)
```rust
        if current_pending_parent_hash != new_pending_parent_hash {
            pending_classes.write().await.clear();
        }
        *pending_data.write().await = new_pending_data;
        Ok(PendingSyncTaskResult::DownloadedNewPendingData)
```

**File:** crates/apollo_rpc_execution/src/state_reader_test.rs (L222-244)
```rust
    // Test pending state diff
    let mut pending_classes = PendingClasses::default();
    pending_classes.add_compiled_class(class_hash2, casm2);
    pending_classes.add_class(class_hash2, ApiContractClass::ContractClass(class2));
    pending_classes.add_class(class_hash3, ApiContractClass::ContractClass(class0));
    pending_classes
        .add_class(class_hash4, ApiContractClass::DeprecatedContractClass(class1.clone()));
    state_reader2.maybe_pending_data = Some(PendingData {
        storage_diffs: indexmap!(
            address0 => vec![StorageEntry{key: storage_key0, value: storage_value1}],
            address2 => vec![StorageEntry{key: storage_key0, value: storage_value2}],
        ),
        deployed_contracts: vec![DeployedContract { address: address2, class_hash: class_hash2 }],
        declared_classes: vec![DeclaredClassHashEntry {
            class_hash: class_hash2,
            compiled_class_hash: compiled_class_hash2,
        }],
        nonces: indexmap!(
            address2 => nonce1,
        ),
        classes: pending_classes,
        ..Default::default()
    });
```
