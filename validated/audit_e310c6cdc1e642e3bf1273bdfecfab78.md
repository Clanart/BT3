### Title
`revert_state_diff` Unconditionally Deletes `stateless_compiled_class_hash_v2` for Migrated-Only Class Hashes, Corrupting State After Block Reversion - (File: crates/apollo_storage/src/state/mod.rs)

### Summary

`ThinStateDiff::from_state_diff` collapses two semantically distinct sources — `declared_classes` (V1/Poseidon compiled-class hash) and `migrated_compiled_classes` (V2/Blake compiled-class hash) — into a single flat `class_hash_to_compiled_class_hash: IndexMap<ClassHash, CompiledClassHash>` with no tag distinguishing origin. When `revert_state_diff` later processes that map it calls `delete_compiled_class_hashes_v2` over **all** keys, including class hashes that were only migrated (not declared) in the reverted block. This unconditionally removes the V2 hash from the `stateless_compiled_class_hash_v2` table for classes that are still live (declared in an earlier block), leaving the storage in an inconsistent state. Any subsequent transaction that executes one of those classes triggers `StateError::MissingCompiledClassHashV2`, causing execution to fail for otherwise-valid inputs.

---

### Finding Description

**Structural analog to the external report.** The token-bridge bug arises because a single bidirectional mapping (`nativeToBridgedToken` / `bridgedToNativeToken`) does not distinguish between a native token on L1 and a native token on L2 that share the same address. The sequencer analog is that `class_hash_to_compiled_class_hash` does not distinguish between a class hash whose compiled-class hash was set by a *declare* transaction (V1 Poseidon hash) and one whose compiled-class hash was updated by a *migration* (V2 Blake hash). Both are stored under the same key with no provenance tag, so downstream code that iterates the map cannot tell them apart.

**Step 1 — Flattening in `ThinStateDiff::from_state_diff`.**

`StateDiff` keeps the two sources separate:

```rust
pub declared_classes: IndexMap<ClassHash, (CompiledClassHash, SierraContractClass)>,
pub migrated_compiled_classes: IndexMap<ClassHash, CompiledClassHash>,
```

`ThinStateDiff::from_state_diff` chains them into one map:

```rust
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

The resulting `ThinStateDiff` carries no information about which entries came from declarations and which from migrations. [1](#0-0) 

**Step 2 — `stateless_compiled_class_hash_v2` is populated only at declaration time.**

When a Cairo 1 class is first declared, the V2 (Blake) hash is written to the `stateless_compiled_class_hash_v2` table:

```rust
append_txn = append_txn.append_casm(&class_hash, &casm_contract_class)?;
append_txn = append_txn.set_executable_class_hash_v2(&class_hash, compiled_class_hash_v2)?;
``` [2](#0-1) 

Migration does **not** call `set_executable_class_hash_v2`; it only updates `class_hash_to_compiled_class_hash` in the state diff. The `stateless_compiled_class_hash_v2` table therefore holds exactly one entry per declared class, written at declaration time and never updated on migration.

**Step 3 — `revert_state_diff` deletes without a block-number guard.**

`delete_declared_classes_block` correctly guards its deletion with a block-number check:

```rust
if class_block_entry == block_number {
    declared_classes_block_table.delete(txn, class_hash)?;
    ...
}
``` [3](#0-2) 

But `delete_compiled_class_hashes_v2` is called with the full key set of `class_hash_to_compiled_class_hash` — which includes migrated-only class hashes — and applies no such guard:

```rust
delete_compiled_class_hashes_v2(
    &self.txn,
    thin_state_diff.class_hash_to_compiled_class_hash.keys(),
    &compiled_class_hash_v2_table,
)?;
``` [4](#0-3) 

**Step 4 — Concrete corruption scenario.**

| Block | Event | `stateless_compiled_class_hash_v2[X]` | `compiled_class_hash[(X,·)]` |
|---|---|---|---|
| N | Class X declared (V1 hash) | V2_hash written | (X,N)→V1_hash |
| M>N | Class X migrated (V2 hash) | unchanged | (X,M)→V2_hash |
| revert M | `delete_compiled_class_hashes_v2` runs on keys of block-M diff, which includes X | **deleted** | (X,M) deleted; (X,N) still present |

After the revert, `get_compiled_class_hash(X)` returns V1_hash (non-zero), so `should_migrate` proceeds to call `get_compiled_class_hash_v2(X)`, which now returns `StateError::MissingCompiledClassHashV2(X)`. [5](#0-4) 

**Step 5 — Error propagation to execution.**

`should_migrate` is called inside `CasmHashMigrationData::from_state`, which is called by `get_tx_weights`, which is called by `Bouncer::try_update` during transaction execution. The `StateError` propagates as a `TransactionExecutorError`, causing every transaction that executes class X to fail. [6](#0-5) 

**Step 6 — Effect on block commitment.**

`BlockExecutionArtifacts::new` calls `calculate_block_commitments` with `ThinStateDiff::from(commitment_state_diff.clone())`. Because the execution of transactions using class X now fails, the resulting `CommitmentStateDiff` and the derived `state_diff_commitment` / `PartialBlockHashComponents` will differ from what they would have been had the V2 hash been present. The state-diff hash fed into the block hash is therefore wrong for any block produced after the revert. [7](#0-6) 

---

### Impact Explanation

After reverting a block that contains a migration entry for class X, the `stateless_compiled_class_hash_v2` table loses X's V2 hash even though X remains declared. Any transaction that executes X triggers `StateError::MissingCompiledClassHashV2`, causing the blockifier to return a wrong execution result (forced failure) for an otherwise-valid transaction. The derived `CommitmentStateDiff` and block-hash commitment are therefore incorrect.

This matches: **Critical — Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input.**

---

### Likelihood Explanation

Block reversion occurs during L1-driven reorgs and is a normal sequencer operation. Any network that has activated `enable_casm_hash_migration` and has experienced at least one migration block followed by a reorg will hit this path. The trigger is not user-controlled but is a routine network event, making the likelihood **Medium** once migration is live.

---

### Recommendation

In `revert_state_diff`, mirror the guard used by `delete_declared_classes_block`: before deleting a class hash from `stateless_compiled_class_hash_v2`, check `declared_classes_block_table` to confirm the class was **declared** (not merely migrated) in the block being reverted. Only delete the V2 hash if the class's declaration block equals the reverted block number.

Alternatively, separate the `class_hash_to_compiled_class_hash` field in `ThinStateDiff` into two distinct maps — one for declarations and one for migrations — so that revert logic can operate on each independently without ambiguity.

---

### Proof of Concept

1. Enable `enable_casm_hash_migration = true` in `VersionedConstants`.
2. Declare Cairo 1 class X with V1 (Poseidon) compiled-class hash in block N. Confirm `stateless_compiled_class_hash_v2[X]` is set.
3. Execute a transaction in block M > N that uses class X. Confirm `should_migrate` returns `Some(...)` and block M's `ThinStateDiff.class_hash_to_compiled_class_hash` contains X → V2_hash.
4. Call `revert_state_diff(BlockNumber(M))`. Observe that `delete_compiled_class_hashes_v2` is called with X as a key.
5. After revert, call `storage_reader.get_executable_class_hash_v2(&X)`. Observe it returns `None`.
6. Execute any transaction that uses class X in block M+1. Observe `StateError::MissingCompiledClassHashV2(X)` is returned, causing the transaction to fail despite being valid.
7. Confirm that `compiled_class_hash[(X, N)]` still exists (V1 hash), proving X is still declared and the failure is caused solely by the incorrect deletion of the V2 hash.

### Citations

**File:** crates/starknet_api/src/state.rs (L84-93)
```rust
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

**File:** crates/native_blockifier/src/storage.rs (L210-214)
```rust
        for (class_hash, casm_contract_class, compiled_class_hash_v2) in undeclared_casm_contracts {
            append_txn = append_txn.append_casm(&class_hash, &casm_contract_class)?;
            append_txn =
                append_txn.set_executable_class_hash_v2(&class_hash, compiled_class_hash_v2)?;
        }
```

**File:** crates/apollo_storage/src/state/mod.rs (L673-677)
```rust
        delete_compiled_class_hashes_v2(
            &self.txn,
            thin_state_diff.class_hash_to_compiled_class_hash.keys(),
            &compiled_class_hash_v2_table,
        )?;
```

**File:** crates/apollo_storage/src/state/mod.rs (L835-850)
```rust
    for class_hash in thin_state_diff.class_hash_to_compiled_class_hash.keys() {
        let class_block_entry =
            declared_classes_block_table.get(txn, class_hash)?.ok_or_else(|| {
                StorageError::DBInconsistency {
                    msg: format!(
                        "Attempting to revert declaration of class {class_hash} but it doesn't \
                         exist in the DB"
                    ),
                }
            })?;
        if class_block_entry == block_number {
            declared_classes_block_table.delete(txn, class_hash)?;
            deleted_data.push(*class_hash);
        }
    }
    Ok(deleted_data)
```

**File:** crates/blockifier/src/utils.rs (L122-142)
```rust
pub fn should_migrate(
    state_reader: &impl StateReader,
    class_hash: ClassHash,
) -> StateResult<Option<(ClassHash, CompiledClassHashV2ToV1)>> {
    let state_compiled_class_hash = state_reader.get_compiled_class_hash(class_hash)?;
    match state_compiled_class_hash {
        // Class hash does not exist in the state, or is a Cairo 0 class.
        CompiledClassHash(hash) if hash == StarkHash::ZERO => Ok(None),
        state_compiled_class_hash => {
            let compiled_class_hash_v2 = state_reader.get_compiled_class_hash_v2(
                class_hash,
                &state_reader.get_compiled_class(class_hash)?,
            )?;
            // If the state compiled class hash is compiled class hash v2, the class should not
            // migrate.
            if state_compiled_class_hash == compiled_class_hash_v2 {
                return Ok(None);
            }
            Ok(Some((class_hash, (compiled_class_hash_v2, state_compiled_class_hash))))
        }
    }
```

**File:** crates/blockifier/src/bouncer.rs (L346-369)
```rust
    fn from_state<S: StateReader>(
        state_reader: &S,
        executed_class_hashes: &HashSet<ClassHash>,
        versioned_constants: &VersionedConstants,
    ) -> TransactionExecutionResult<Self> {
        if !versioned_constants.enable_casm_hash_migration {
            return Ok(Self::empty());
        }

        executed_class_hashes.iter().try_fold(Self::empty(), |mut migration_data, &class_hash| {
            if let Some((class_hash, casm_hash_v2_to_v1)) =
                should_migrate(state_reader, class_hash)?
            {
                // Add class hash mapping to the migration data.
                migration_data.class_hashes_to_migrate.insert(class_hash, casm_hash_v2_to_v1);

                // Accumulate the class's migration resources.
                let class = state_reader.get_compiled_class(class_hash)?;
                migration_data.resources +=
                    &class.estimate_compiled_class_hash_migration_resources();
            }
            Ok(migration_data)
        })
    }
```

**File:** crates/apollo_batcher/src/block_builder.rs (L160-166)
```rust
        let (header_commitments, measurements) = calculate_block_commitments(
            &transactions_data,
            ThinStateDiff::from(commitment_state_diff.clone()),
            l1_da_mode,
            &block_info.starknet_version,
        )
        .await;
```
