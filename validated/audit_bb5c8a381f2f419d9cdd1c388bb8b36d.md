### Title
Stale `compiled_class_hash` Returned by RPC Execution State Reader After Class Hash Migration — (`File: crates/apollo_rpc_execution/src/state_reader.rs`)

### Summary

`ExecutionStateReader::get_compiled_class_hash` in `apollo_rpc_execution` resolves the compiled class hash by looking up the **first-ever declaration block** from `declared_classes_block_table` and reading the state diff at that block. When a class's compiled class hash is subsequently migrated via `migrated_compiled_classes`, the `declared_classes_block_table` entry is never updated, so the function permanently returns the stale pre-migration value. The correct, versioned lookup (`get_compiled_class_hash_at`) is used by `ApolloReader` for sequencing but is absent from the RPC execution path, creating a divergence that causes fee estimation, simulation, and tracing to return an authoritative-looking wrong compiled class hash.

---

### Finding Description

**Accumulator that is never updated on re-use**

`append_state_diff` writes the first declaration block for a Cairo 1 class into `declared_classes_block_table` with an explicit skip-if-already-present guard:

```rust
for (class_hash, _) in &thin_state_diff.class_hash_to_compiled_class_hash {
    let not_declared = declared_classes_block_table.get(&self.txn, class_hash)?.is_none();
    if not_declared {
        declared_classes_block_table.insert(&self.txn, class_hash, &block_number)?;
    }
}
``` [1](#0-0) 

At the same time, `write_compiled_class_hashes` unconditionally appends a new `(class_hash, block_number) → compiled_class_hash` row to the versioned `compiled_class_hash_table` for every block that touches the class (including migrations):

```rust
for (class_hash, compiled_class_hash) in compiled_class_hashes {
    compiled_class_hash_table.insert(txn, &(*class_hash, block_number), compiled_class_hash)?;
}
``` [2](#0-1) 

After a migration at block M, the `compiled_class_hash_table` therefore contains two rows — `(C, N) → H1` (original) and `(C, M) → H2` (migrated) — while `declared_classes_block_table[C]` still holds `N`.

**The broken read path in RPC execution**

`ExecutionStateReader::get_compiled_class_hash` resolves the compiled class hash by:
1. Calling `get_class_definition_block_number` → returns the stale first-declaration block `N`.
2. Reading the full state diff at block `N`.
3. Extracting `class_hash_to_compiled_class_hash[C]` from that diff → returns `H1`.

```rust
fn get_compiled_class_hash(&self, class_hash: ClassHash) -> StateResult<CompiledClassHash> {
    // ...
    let maybe_block_number = self
        .storage_reader.begin_ro_txn()...
        .get_class_definition_block_number(&class_hash)...;   // always returns N

    let state_diff = self
        .storage_reader.begin_ro_txn()...
        .get_state_diff(block_number)...;                     // reads diff at N

    let compiled_class_hash = state_diff
        .class_hash_to_compiled_class_hash
        .get(&class_hash)...;                                 // returns H1 (stale)
    Ok(*compiled_class_hash)
}
``` [3](#0-2) 

**The correct read path used by the sequencer**

`ApolloReader::get_compiled_class_hash` (used for actual block execution) calls `get_compiled_class_hash_at`, which performs a cursor-based lower-bound scan on the versioned `compiled_class_hash_table` and returns the most recent entry before the requested state number — correctly returning `H2` after the migration: [4](#0-3) 

The two implementations are inconsistent. The RPC path is permanently frozen at the first-declaration value.

**Migration path that triggers the divergence**

`ThinStateDiff::from_state_diff` chains `declared_classes` and `migrated_compiled_classes` into the same `class_hash_to_compiled_class_hash` map:

```rust
class_hash_to_compiled_class_hash: diff.declared_classes.iter()
    .map(|(ch, (cch, _))| (*ch, *cch))
    .chain(diff.migrated_compiled_classes.iter()
        .map(|(ch, cch)| (*ch, *cch)))
    .collect(),
``` [5](#0-4) 

When central sync ingests a block containing a `migrated_compiled_classes` entry for class `C`, `append_state_diff` writes the new compiled class hash into `compiled_class_hash_table` but leaves `declared_classes_block_table[C]` pointing at the original block. Every subsequent RPC execution call for class `C` returns `H1` instead of `H2`.

---

### Impact Explanation

After a compiled class hash migration, every call to `starknet_estimateFee`, `starknet_simulateTransactions`, `starknet_traceTransaction`, or any pending-block execution that touches class `C` will receive `H1` (the pre-migration compiled class hash) as the authoritative initial state value. The blockifier's `CachedState` caches this wrong initial read and uses it to compute the state diff:

```rust
fn get_compiled_class_hash(&self, class_hash: ClassHash) -> StateResult<CompiledClassHash> {
    if cache.get_compiled_class_hash(class_hash).is_none() {
        let compiled_class_hash = self.state.get_compiled_class_hash(class_hash)?; // H1 (wrong)
        cache.set_compiled_class_hash_initial_value(class_hash, compiled_class_hash);
    }
    ...
}
``` [6](#0-5) 

The resulting state diff will contain a spurious `class_hash → compiled_class_hash` update (from `H1` to `H2`) even for transactions that never touch the class declaration, inflating the reported state diff length and producing wrong simulation/trace outputs. Any client relying on these RPC results for decision-making (e.g., wallets, dApps, block explorers) receives an authoritative-looking wrong value.

**Matching impact**: *High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value.*

---

### Likelihood Explanation

Triggering the bug requires a `migrated_compiled_classes` entry to be included in a synced block's state diff. This is a governance-level operation (compiler upgrade migration) that has occurred historically on Starknet mainnet. The field is still present and active in the codebase (marked with a TODO to remove after 0.14.1). Once a migration block is synced, the bug is permanent and affects all subsequent RPC execution calls for the migrated class — no further attacker action is needed.

**Likelihood: Low** (requires a governance migration event), but the effect is persistent and automatic once triggered.

---

### Recommendation

Replace the `get_class_definition_block_number` + state-diff lookup in `ExecutionStateReader::get_compiled_class_hash` with a direct call to the versioned `get_compiled_class_hash_at` (or equivalently `get_compiled_class_hash_at` via the storage reader), mirroring the correct implementation in `ApolloReader`:

```rust
fn get_compiled_class_hash(&self, class_hash: ClassHash) -> StateResult<CompiledClassHash> {
    // pending data check unchanged ...

    let state_number = self.state_number;
    match self.storage_reader
        .begin_ro_txn().map_err(storage_err_to_state_err)?
        .get_state_reader().map_err(storage_err_to_state_err)?
        .get_compiled_class_hash_at(state_number, &class_hash)
        .map_err(storage_err_to_state_err)?
    {
        Some(h) => Ok(h),
        None => Ok(CompiledClassHash::default()),
    }
}
```

This aligns the RPC execution path with the sequencer execution path and ensures that post-migration compiled class hashes are always returned correctly.

---

### Proof of Concept

1. Sync two blocks:
   - **Block 0**: Declare class `C` with `compiled_class_hash = H1`. After `append_state_diff`, `declared_classes_block_table[C] = 0`, `compiled_class_hash_table[(C,0)] = H1`.
   - **Block 1**: Include `migrated_compiled_classes = [(C, H2)]`. After `append_state_diff`, `declared_classes_block_table[C]` remains `0` (skip-if-present guard fires), `compiled_class_hash_table[(C,1)] = H2`.

2. Call `starknet_estimateFee` for any transaction at block 1 that reads class `C`'s compiled class hash.

3. `ExecutionStateReader::get_compiled_class_hash(C)`:
   - `get_class_definition_block_number(C)` → `BlockNumber(0)` ✗ (stale)
   - `get_state_diff(0).class_hash_to_compiled_class_hash[C]` → `H1` ✗ (stale)

4. `ApolloReader::get_compiled_class_hash(C)` at the same state:
   - `get_compiled_class_hash_at(StateNumber(1), C)` → cursor finds `(C,1) → H2` ✓

5. The RPC returns `H1`; the sequencer uses `H2`. The simulation result contains a spurious `C → H2` compiled class hash update in the state diff, and any fee or trace output depending on this value is wrong. [1](#0-0) [7](#0-6) [4](#0-3)

### Citations

**File:** crates/apollo_storage/src/state/mod.rs (L549-554)
```rust
        for (class_hash, _) in &thin_state_diff.class_hash_to_compiled_class_hash {
            let not_declared = declared_classes_block_table.get(&self.txn, class_hash)?.is_none();
            if not_declared {
                declared_classes_block_table.insert(&self.txn, class_hash, &block_number)?;
            }
        }
```

**File:** crates/apollo_storage/src/state/mod.rs (L801-810)
```rust
fn write_compiled_class_hashes<'env>(
    compiled_class_hashes: &IndexMap<ClassHash, CompiledClassHash>,
    txn: &DbTransaction<'env, RW>,
    block_number: BlockNumber,
    compiled_class_hash_table: &'env CompiledClassHashTable<'env>,
) -> StorageResult<()> {
    for (class_hash, compiled_class_hash) in compiled_class_hashes {
        compiled_class_hash_table.insert(txn, &(*class_hash, block_number), compiled_class_hash)?;
    }
    Ok(())
```

**File:** crates/apollo_storage/src/state/mod.rs (L1066-1089)
```rust
fn get_compiled_class_hash_at<'env, Mode: TransactionKind>(
    first_irrelevant_block: BlockNumber,
    class_hash: &ClassHash,
    txn: &'env DbTransaction<'env, Mode>,
    compiled_class_hash_table: &'env CompiledClassHashTable<'env>,
) -> StorageResult<Option<CompiledClassHash>> {
    let db_key = (*class_hash, first_irrelevant_block);
    // Find the previous db item.
    let mut cursor = compiled_class_hash_table.cursor(txn)?;
    cursor.lower_bound(&db_key)?;
    let res = cursor.prev()?;
    match res {
        None => Ok(None),
        Some(((got_class_hash, _got_block_number), value)) => {
            if got_class_hash != *class_hash {
                // The previous item belongs to different class hash, which means there is no
                // previous state diff for this item.
                return Ok(None);
            };
            // The previous db item indeed belongs to this address and key.
            Ok(Some(value))
        }
    }
}
```

**File:** crates/apollo_rpc_execution/src/state_reader.rs (L163-208)
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

        let maybe_block_number = self
            .storage_reader
            .begin_ro_txn()
            .map_err(storage_err_to_state_err)?
            .get_state_reader()
            .map_err(storage_err_to_state_err)?
            .get_class_definition_block_number(&class_hash)
            .map_err(storage_err_to_state_err)?;

        // Cairo 0 classes (and undeclared classes) do not have a compiled class hash.
        // According to the trait, return the default value.
        let Some(block_number) = maybe_block_number else {
            return Ok(CompiledClassHash::default());
        };

        let state_diff = self
            .storage_reader
            .begin_ro_txn()
            .map_err(storage_err_to_state_err)?
            .get_state_diff(block_number)
            .map_err(storage_err_to_state_err)?
            .ok_or(StateError::StateReadError(format!(
                "Inner storage error. Missing state diff at block {block_number}."
            )))?;

        let compiled_class_hash = state_diff
            .class_hash_to_compiled_class_hash
            .get(&class_hash)
            .ok_or(StateError::StateReadError(format!(
                "Inner storage error. Missing class declaration at block {block_number}, class \
                 {class_hash}."
            )))?;

        Ok(*compiled_class_hash)
    }
```

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

**File:** crates/blockifier/src/state/cached_state.rs (L203-215)
```rust
    fn get_compiled_class_hash(&self, class_hash: ClassHash) -> StateResult<CompiledClassHash> {
        let mut cache = self.cache.borrow_mut();

        if cache.get_compiled_class_hash(class_hash).is_none() {
            let compiled_class_hash = self.state.get_compiled_class_hash(class_hash)?;
            cache.set_compiled_class_hash_initial_value(class_hash, compiled_class_hash);
        }

        let compiled_class_hash = cache
            .get_compiled_class_hash(class_hash)
            .unwrap_or_else(|| panic!("Cannot retrieve '{class_hash:?}' from the cache."));
        Ok(*compiled_class_hash)
    }
```
