### Title
`delete_declared_classes` in `revert_state_diff` Unconditionally Erases Sierra Class Definitions for Migrated Classes, Corrupting Storage After Block Revert — (`File: crates/apollo_storage/src/state/mod.rs`)

---

### Summary

When `revert_state_diff` is called, the helper `delete_declared_classes` deletes Sierra class definitions from `declared_classes_table` for **every** class hash in `ThinStateDiff.class_hash_to_compiled_class_hash` — including classes that were declared in a **previous** block but whose compiled class hash is being migrated in the block being reverted. The sibling helper `delete_declared_classes_block` correctly guards against this with a block-number check; `delete_declared_classes` does not. The result is a storage state where `declared_classes_block_table` still records the class as declared in block N, but `declared_classes_table` has no Sierra definition for it — an inconsistency that causes wrong class-hash lookups, wrong RPC results, and wrong execution state after any revert that touches a migrated class.

---

### Finding Description

**Root cause — asymmetric block-number guard between two sibling deletion helpers.**

`ThinStateDiff.class_hash_to_compiled_class_hash` is built from **two** sources:

```rust
// crates/starknet_api/src/state.rs  lines 82-93
class_hash_to_compiled_class_hash: diff
    .declared_classes          // classes first declared in THIS block
    .iter()
    .map(|(ch, (cch, _))| (*ch, *cch))
    .chain(
        diff.migrated_compiled_classes   // classes declared in EARLIER blocks
            .iter()
            .map(|(ch, cch)| (*ch, *cch)),
    )
    .collect(),
``` [1](#0-0) 

The same merge is performed explicitly in the native-blockifier path:

```rust
// crates/native_blockifier/src/storage.rs  lines 223-228
for (class_hash, compiled_class_hash) in migrated_class_hash_to_compiled_class_hash {
    thin_state_diff
        .class_hash_to_compiled_class_hash
        .insert(class_hash, compiled_class_hash);
}
``` [2](#0-1) 

During `revert_state_diff`, two helpers are called for every class hash in `class_hash_to_compiled_class_hash`:

**`delete_declared_classes_block`** — correctly guards with a block-number check:

```rust
// crates/apollo_storage/src/state/mod.rs  lines 845-848
if class_block_entry == block_number {
    declared_classes_block_table.delete(txn, class_hash)?;
    deleted_data.push(*class_hash);
}
``` [3](#0-2) 

**`delete_declared_classes`** — **no block-number check at all**; deletes unconditionally:

```rust
// crates/apollo_storage/src/state/mod.rs  lines 860-868
for class_hash in thin_state_diff.class_hash_to_compiled_class_hash.keys() {
    let Some(contract_class_location) = declared_classes_table.get(txn, class_hash)? else {
        continue;
    };
    deleted_data.insert(*class_hash, ...);
    declared_classes_table.delete(txn, class_hash)?;   // ← no block guard
}
``` [4](#0-3) 

`declared_classes_table` maps `ClassHash → LocationInFile` (the Sierra definition). It is written once per class, when the class is first declared via `append_classes`:

```rust
// crates/apollo_storage/src/class.rs  lines 262-265
for (class_hash, contract_class) in classes {
    let location = file_handlers.append_contract_class(contract_class);
    declared_classes_table.insert(txn, class_hash, &location)?;
``` [5](#0-4) 

It is **not** re-written when a compiled class hash migration occurs (the Sierra source does not change). Therefore, when block N+1 is reverted and it contains a migration of class C (declared in block N):

| Table | Expected after revert | Actual after revert |
|---|---|---|
| `declared_classes_block_table[C]` | `N` (preserved — guard fires correctly) | `N` ✓ |
| `declared_classes_table[C]` | Sierra definition present | **deleted** ✗ |

The revert path is production-reachable: `revert_block` in `apollo_reverts` calls `revert_state_diff`, and the consensus manager calls `revert_block` on the batcher whenever a block must be rolled back:

```rust
// crates/apollo_reverts/src/lib.rs  lines 133-134
.revert_state_diff(target_block_marker)
``` [6](#0-5) 

```rust
// crates/apollo_consensus_manager/src/consensus_manager.rs  lines 351-356
self.batcher_client
    .revert_block(RevertBlockInput { height })
    .await
    .expect("Failed to revert block at height {height} in the batcher");
``` [7](#0-6) 

---

### Impact Explanation

After the revert, `get_class(&C)` returns `None` even though `declared_classes_block_table` records C as declared in block N. Any subsequent:

- **Execution** that calls `state.get_compiled_class(class_hash)` for C will fail or receive wrong data — matching the "Wrong compiled class / contract code selected for execution" critical scope.
- **RPC** `starknet_getClass` call for C returns an authoritative-looking "class not found" — matching the "RPC returns an authoritative-looking wrong value" high scope.
- **State sync** that reads `declared_classes_block_table` to decide whether to re-download C will see a block-number entry but find no Sierra body, producing an inconsistent sync state.

---

### Likelihood Explanation

Two independent conditions must hold simultaneously:

1. A block containing `migrated_compiled_classes` entries is committed. This is a production feature used by the native-blockifier path and the central-sync path (confirmed by `native_blockifier/src/storage.rs` and `apollo_central_sync`).
2. That block is subsequently reverted. Block reverts occur during normal consensus operation whenever a validator rejects a proposal or a reorg is detected — no adversarial action is required.

Both conditions are part of normal sequencer operation, making this a realistic scenario.

---

### Recommendation

`delete_declared_classes` must consult `declared_classes_block_table` before deleting, mirroring the guard already present in `delete_declared_classes_block`:

```rust
fn delete_declared_classes<'env>(
    txn: &'env DbTransaction<'env, RW>,
    thin_state_diff: &ThinStateDiff,
    declared_classes_table: &'env DeclaredClassesTable<'env>,
    declared_classes_block_table: &'env DeclaredClassesBlockTable<'env>,
    file_handlers: &FileHandlers<RW>,
    block_number: BlockNumber,
) -> StorageResult<IndexMap<ClassHash, SierraContractClass>> {
    let mut deleted_data = IndexMap::new();
    for class_hash in thin_state_diff.class_hash_to_compiled_class_hash.keys() {
        // Only delete the Sierra definition if the class was FIRST declared in this block.
        let Some(declared_at) = declared_classes_block_table.get(txn, class_hash)? else {
            continue;
        };
        if declared_at != block_number {
            continue;   // class was declared in an earlier block; preserve its definition
        }
        let Some(location) = declared_classes_table.get(txn, class_hash)? else {
            continue;
        };
        deleted_data.insert(*class_hash,
            file_handlers.get_contract_class_unchecked(location)?);
        declared_classes_table.delete(txn, class_hash)?;
    }
    Ok(deleted_data)
}
```

A corresponding test should be added alongside the existing `revert_doesnt_delete_previously_declared_classes` test, covering the Sierra (`class_hash_to_compiled_class_hash`) path with a migrated class.

---

### Proof of Concept

```
1. append_state_diff(BlockNumber(0), diff_with_class_C_declared)
   append_classes(BlockNumber(0), [(C, sierra_def)], [])
   → declared_classes_block_table[C] = 0
   → declared_classes_table[C]       = <location of sierra_def>

2. append_state_diff(BlockNumber(1), diff_with_C_in_migrated_compiled_classes)
   // C appears in class_hash_to_compiled_class_hash of block 1's ThinStateDiff
   // append_classes is NOT called for C (Sierra definition unchanged)
   → declared_classes_block_table[C] still = 0  (guard in append_state_diff fires)
   → declared_classes_table[C]       still = <location of sierra_def>

3. revert_state_diff(BlockNumber(1))
   delete_declared_classes_block: class_block_entry(C)=0 ≠ 1 → SKIP  ✓
   delete_declared_classes:       no guard → DELETE declared_classes_table[C]  ✗

4. get_class(&C)  →  None
   declared_classes_block_table[C] = 0  (says class exists)
   declared_classes_table[C]       = missing  (definition gone)
   // Inconsistent state; execution / RPC / sync all see wrong value
```

### Citations

**File:** crates/starknet_api/src/state.rs (L82-93)
```rust
                deployed_contracts: diff.deployed_contracts,
                storage_diffs: diff.storage_diffs,
                class_hash_to_compiled_class_hash: diff
                    .declared_classes
                    .iter()
                    .map(|(class_hash, (compiled_hash, _class))| (*class_hash, *compiled_hash))
                    .chain(
                        diff.migrated_compiled_classes
                            .iter()
                            .map(|(class_hash, compiled_hash)| (*class_hash, *compiled_hash)),
                    )
                    .collect(),
```

**File:** crates/native_blockifier/src/storage.rs (L223-228)
```rust
        // Add the migrated class hash to the state diff.
        for (class_hash, compiled_class_hash) in migrated_class_hash_to_compiled_class_hash {
            thin_state_diff
                .class_hash_to_compiled_class_hash
                .insert(class_hash, compiled_class_hash);
        }
```

**File:** crates/apollo_storage/src/state/mod.rs (L845-848)
```rust
        if class_block_entry == block_number {
            declared_classes_block_table.delete(txn, class_hash)?;
            deleted_data.push(*class_hash);
        }
```

**File:** crates/apollo_storage/src/state/mod.rs (L860-868)
```rust
    for class_hash in thin_state_diff.class_hash_to_compiled_class_hash.keys() {
        let Some(contract_class_location) = declared_classes_table.get(txn, class_hash)? else {
            continue;
        };
        deleted_data.insert(
            *class_hash,
            file_handlers.get_contract_class_unchecked(contract_class_location)?,
        );
        declared_classes_table.delete(txn, class_hash)?;
```

**File:** crates/apollo_storage/src/class.rs (L262-265)
```rust
    for (class_hash, contract_class) in classes {
        let location = file_handlers.append_contract_class(contract_class);
        declared_classes_table.insert(txn, class_hash, &location)?;
        file_offset_table.upsert(txn, &OffsetKind::ContractClass, &location.next_offset())?;
```

**File:** crates/apollo_reverts/src/lib.rs (L133-134)
```rust
        .revert_state_diff(target_block_marker)
        .unwrap()
```

**File:** crates/apollo_consensus_manager/src/consensus_manager.rs (L351-356)
```rust
        let revert_blocks_fn = move |height| async move {
            self.batcher_client
                .revert_block(RevertBlockInput { height })
                .await
                .expect("Failed to revert block at height {height} in the batcher");
        };
```
