### Title
`revert_state_diff` deletes `declared_classes_table` entries for migrated classes that were originally declared in an earlier block, leaving `declared_classes_block_table` pointing to a now-missing class body - (`File: crates/apollo_storage/src/state/mod.rs`)

### Summary

`revert_state_diff` calls `delete_declared_classes`, which unconditionally deletes every entry in `declared_classes_table` whose class hash appears in the reverted block's `class_hash_to_compiled_class_hash` map. When that map contains a **migrated** class (one declared in an earlier block N and whose compiled-class-hash was updated in a later block M), reverting block M deletes the Sierra class body that was written in block N. The companion function `delete_declared_classes_block` correctly guards against this with a block-number equality check, but `delete_declared_classes` has no such guard. After the revert, `declared_classes_block_table[C] = N` still exists while `declared_classes_table[C]` is gone, producing a permanent `DBInconsistency` for any subsequent read of class C.

### Finding Description

**Stale-flag analog.** In the Solidity report, `_isLiquidation[collection][tokenId]` is set in block N and never cleared when the token is reserved; a later fill reads the stale flag and skips the tax refund. Here the analog is:

| Solidity concept | Sequencer concept |
|---|---|
| `_isLiquidation[collection][tokenId] = true` | `declared_classes_block_table[C] = N` written in `append_state_diff` at block N |
| `reserve()` does not delete the flag | `revert_state_diff(M)` does not guard `delete_declared_classes` with a block-number check |
| Future `_fillListing` reads stale flag → wrong tax | Future `get_class_definition_at` finds `declared_classes_block_table[C] = N` but `declared_classes_table[C]` is gone → `DBInconsistency` |

**Write path (block N – declaration).**
`append_state_diff` inserts into `declared_classes_block_table` only when the class is not yet present:

```rust
// crates/apollo_storage/src/state/mod.rs  lines 549-553
for (class_hash, _) in &thin_state_diff.class_hash_to_compiled_class_hash {
    let not_declared = declared_classes_block_table.get(&self.txn, class_hash)?.is_none();
    if not_declared {
        declared_classes_block_table.insert(&self.txn, class_hash, &block_number)?;
    }
}
```

`append_classes` → `write_classes` writes the Sierra body into `declared_classes_table` via `insert` (fails if already present):

```rust
// crates/apollo_storage/src/class.rs  lines 262-265
for (class_hash, contract_class) in classes {
    let location = file_handlers.append_contract_class(contract_class);
    declared_classes_table.insert(txn, class_hash, &location)?;
```

**Write path (block M – migration).** Migrated classes are appended to `thin_state_diff.class_hash_to_compiled_class_hash` but are **not** passed to `append_classes`. Therefore `declared_classes_table[C]` is unchanged (still points to the body written in block N), and `declared_classes_block_table[C]` is also unchanged (still `N`).

**Revert path (block M reverted).**
`delete_declared_classes_block` correctly skips migrated classes because `class_block_entry (= N) ≠ block_number (= M)`:

```rust
// crates/apollo_storage/src/state/mod.rs  lines 828-851
if class_block_entry == block_number {
    declared_classes_block_table.delete(txn, class_hash)?;
    deleted_data.push(*class_hash);
}
```

But `delete_declared_classes` has **no such guard**:

```rust
// crates/apollo_storage/src/state/mod.rs  lines 853-872
for class_hash in thin_state_diff.class_hash_to_compiled_class_hash.keys() {
    let Some(contract_class_location) = declared_classes_table.get(txn, class_hash)? else {
        continue;
    };
    // ← no block-number check; deletes the body written in block N
    declared_classes_table.delete(txn, class_hash)?;
}
```

After reverting block M the storage is in an inconsistent state:

| Table | Key | Value |
|---|---|---|
| `declared_classes_block_table` | `C` | `N` (still present) |
| `declared_classes_table` | `C` | **DELETED** |
| `compiled_class_hash_table` | `(C, N)` | `v1_hash` (still present) |
| `stateless_compiled_class_hash_v2` | `C` | deleted (correct) |

**Read path after revert.** `get_class_definition_at` finds `declared_classes_block_table[C] = N`, proceeds past the `is_before` guard, then fails to find `declared_classes_table[C]`:

```rust
// crates/apollo_storage/src/state/mod.rs  lines 428-453
let Some(block_number) = self.declared_classes_block_table.get(self.txn, class_hash)?
else { return Ok(None); };
if state_number.is_before(block_number) { return Ok(None); }
let Some(contract_class_location) =
    self.declared_classes_table.get(self.txn, class_hash)?
else {
    if state_number.is_after(class_marker) { return Ok(None); }
    return Err(StorageError::DBInconsistency { ... });  // ← hit here
};
```

The result is either a hard `DBInconsistency` error or a silent `None` for a class that is legitimately declared, depending on the class marker position.

### Impact Explanation

- Any transaction that calls a contract whose class was migrated and whose migration block was later reverted will fail to load the class body, producing a wrong execution result or a hard storage error.
- The RPC `starknet_getClass` / `starknet_getClassAt` endpoints will return an authoritative-looking wrong value (`None` or an internal error) for the affected class hash.
- The state root computed over subsequent blocks will be wrong because the class is treated as undeclared.
- The proof inputs fed to SNOS will reference a class hash that the storage layer reports as absent, breaking proof generation.

This matches **Critical – Wrong state / class hash / storage value from blockifier/syscall/execution logic for accepted input** and **High – RPC execution returns an authoritative-looking wrong value**.

### Likelihood Explanation

The trigger requires three sequential events that are all part of normal sequencer operation:

1. A Cairo 1 class is declared (any user can do this).
2. The sequencer migrates the class's compiled-class hash from v1 (Poseidon) to v2 (Blake) in a later block — this is an automatic protocol-level operation already implemented and enabled via `enable_casm_hash_migration`.
3. The migration block is reverted — this happens during normal BFT consensus whenever a proposed block is not finalized (e.g., a competing proposal wins).

No privileged access is required; the user's only action is step 1.

### Recommendation

Add the same block-number guard to `delete_declared_classes` that already exists in `delete_declared_classes_block`. Pass `declared_classes_block_table` and `block_number` into `delete_declared_classes` and skip deletion when `class_block_entry != block_number`:

```rust
fn delete_declared_classes<'env>(
    txn: &'env DbTransaction<'env, RW>,
    thin_state_diff: &ThinStateDiff,
    declared_classes_table: &'env DeclaredClassesTable<'env>,
    declared_classes_block_table: &'env DeclaredClassesBlockTable<'env>, // add
    file_handlers: &FileHandlers<RW>,
    block_number: BlockNumber,                                            // add
) -> StorageResult<IndexMap<ClassHash, SierraContractClass>> {
    let mut deleted_data = IndexMap::new();
    for class_hash in thin_state_diff.class_hash_to_compiled_class_hash.keys() {
        // Only delete the body if the class was first declared in this block.
        let Some(class_block_entry) =
            declared_classes_block_table.get(txn, class_hash)?
        else { continue; };
        if class_block_entry != block_number { continue; }   // ← guard
        let Some(contract_class_location) =
            declared_classes_table.get(txn, class_hash)?
        else { continue; };
        deleted_data.insert(*class_hash,
            file_handlers.get_contract_class_unchecked(contract_class_location)?);
        declared_classes_table.delete(txn, class_hash)?;
    }
    Ok(deleted_data)
}
```

### Proof of Concept

```
Block N:
  append_state_diff(N, { class_hash_to_compiled_class_hash: {C: v1_hash} })
  append_classes(N, [(C, sierra_body)], [])
  → declared_classes_block_table[C] = N
  → declared_classes_table[C]       = <location of sierra_body>

Block M (M > N, migration):
  append_state_diff(M, { class_hash_to_compiled_class_hash: {C: v2_hash} })
  // append_classes NOT called for C (it is a migration, not a new declaration)
  → declared_classes_block_table[C] = N  (unchanged, skip-if-exists)
  → declared_classes_table[C]       = <location of sierra_body>  (unchanged)
  → compiled_class_hash_table[(C,M)] = v2_hash
  → stateless_compiled_class_hash_v2[C] = v2_hash

Revert block M:
  revert_state_diff(M)
    delete_declared_classes_block: class_block_entry(N) ≠ M → SKIP  ✓
    delete_declared_classes:       declared_classes_table[C] found → DELETE  ✗
    delete_compiled_class_hashes_v2: stateless_compiled_class_hash_v2[C] → DELETE  ✓

Post-revert state:
  declared_classes_block_table[C] = N          ← still present
  declared_classes_table[C]       = MISSING    ← incorrectly deleted

get_class_definition_at(state_after_N, C):
  declared_classes_block_table[C] = N  → found, not before state_after_N
  declared_classes_table[C]       = None
  class_marker > N                → DBInconsistency error  ← wrong authoritative value
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** crates/apollo_storage/src/state/mod.rs (L428-453)
```rust
    pub fn get_class_definition_at(
        &self,
        state_number: StateNumber,
        class_hash: &ClassHash,
    ) -> StorageResult<Option<SierraContractClass>> {
        let Some(block_number) = self.declared_classes_block_table.get(self.txn, class_hash)?
        else {
            return Ok(None);
        };
        if state_number.is_before(block_number) {
            return Ok(None);
        }
        // TODO(shahak): Fix code duplication with ClassStorageReader.
        let Some(contract_class_location) =
            self.declared_classes_table.get(self.txn, class_hash)?
        else {
            if state_number
                .is_after(self.markers_table.get(self.txn, &MarkerKind::Class)?.unwrap_or_default())
            {
                return Ok(None);
            }
            return Err(StorageError::DBInconsistency {
                msg: "Couldn't find class for a block that is before the class marker.".to_string(),
            });
        };
        Ok(Some(self.file_handlers.get_contract_class_unchecked(contract_class_location)?))
```

**File:** crates/apollo_storage/src/state/mod.rs (L549-553)
```rust
        for (class_hash, _) in &thin_state_diff.class_hash_to_compiled_class_hash {
            let not_declared = declared_classes_block_table.get(&self.txn, class_hash)?.is_none();
            if not_declared {
                declared_classes_block_table.insert(&self.txn, class_hash, &block_number)?;
            }
```

**File:** crates/apollo_storage/src/state/mod.rs (L592-693)
```rust
    #[latency_histogram("storage_revert_state_diff_latency_seconds", false)]
    fn revert_state_diff(
        self,
        block_number: BlockNumber,
    ) -> StorageResult<(Self, Option<RevertedStateDiff>)> {
        let markers_table = self.open_table(&self.tables.markers)?;
        let declared_classes_table = self.open_table(&self.tables.declared_classes)?;
        let declared_classes_block_table = self.open_table(&self.tables.declared_classes_block)?;
        let deprecated_declared_classes_table =
            self.open_table(&self.tables.deprecated_declared_classes)?;
        let deprecated_declared_classes_block_table =
            self.open_table(&self.tables.deprecated_declared_classes_block)?;
        // TODO(yair): Consider reverting the compiled classes in their own module.
        let compiled_classes_table = self.open_table(&self.tables.casms)?;
        let compiled_class_hash_v2_table =
            self.open_table(&self.tables.stateless_compiled_class_hash_v2)?;
        let deployed_contracts_table = self.open_table(&self.tables.deployed_contracts)?;
        let nonces_table = self.open_table(&self.tables.nonces)?;
        let storage_table = self.open_table(&self.tables.contract_storage)?;
        let state_diffs_table = self.open_table(&self.tables.state_diffs)?;
        let compiled_class_hash_table = self.open_table(&self.tables.compiled_class_hash)?;

        let current_state_marker = self.get_state_marker()?;

        // Reverts only the last state diff.
        let Some(next_block_number) = block_number
            .next()
            .filter(|next_block_number| *next_block_number == current_state_marker)
        else {
            debug!(
                "Attempt to revert a non-existing / old state diff of block {}. Returning without \
                 an action.",
                block_number
            );
            return Ok((self, None));
        };

        let thin_state_diff = self
            .get_state_diff(block_number)?
            .unwrap_or_else(|| panic!("Missing state diff for block {block_number}."));
        markers_table.upsert(&self.txn, &MarkerKind::State, &block_number)?;
        let classes_marker = markers_table.get(&self.txn, &MarkerKind::Class)?.unwrap_or_default();
        if classes_marker == next_block_number {
            markers_table.upsert(&self.txn, &MarkerKind::Class, &block_number)?;
        }
        let compiled_classes_marker =
            markers_table.get(&self.txn, &MarkerKind::CompiledClass)?.unwrap_or_default();
        if compiled_classes_marker == next_block_number {
            markers_table.upsert(&self.txn, &MarkerKind::CompiledClass, &block_number)?;
        }
        let deleted_class_hashes = delete_declared_classes_block(
            &self.txn,
            &thin_state_diff,
            &declared_classes_block_table,
            block_number,
        )?;
        let deleted_classes = delete_declared_classes(
            &self.txn,
            &thin_state_diff,
            &declared_classes_table,
            &self.file_handlers,
        )?;
        let deleted_deprecated_class_hashes = delete_deprecated_declared_classes_block(
            &self.txn,
            block_number,
            &thin_state_diff,
            &deprecated_declared_classes_block_table,
        )?;
        let deleted_deprecated_classes = delete_deprecated_declared_classes(
            &self.txn,
            block_number,
            &thin_state_diff,
            &deprecated_declared_classes_table,
            &self.file_handlers,
        )?;
        let deleted_compiled_classes = delete_compiled_classes(
            &self.txn,
            thin_state_diff.class_hash_to_compiled_class_hash.keys(),
            &compiled_classes_table,
            &self.file_handlers,
        )?;
        delete_compiled_class_hashes_v2(
            &self.txn,
            thin_state_diff.class_hash_to_compiled_class_hash.keys(),
            &compiled_class_hash_v2_table,
        )?;
        delete_deployed_contracts(
            &self.txn,
            block_number,
            &thin_state_diff,
            &deployed_contracts_table,
            &nonces_table,
        )?;
        delete_storage_diffs(&self.txn, block_number, &thin_state_diff, &storage_table)?;
        delete_nonces(&self.txn, block_number, &thin_state_diff, &nonces_table)?;
        delete_compiled_class_hashes(
            &self.txn,
            block_number,
            &thin_state_diff,
            &compiled_class_hash_table,
        )?;
        state_diffs_table.delete(&self.txn, &block_number)?;
```

**File:** crates/apollo_storage/src/state/mod.rs (L828-851)
```rust
fn delete_declared_classes_block<'env>(
    txn: &'env DbTransaction<'env, RW>,
    thin_state_diff: &ThinStateDiff,
    declared_classes_block_table: &'env DeclaredClassesBlockTable<'env>,
    block_number: BlockNumber,
) -> StorageResult<Vec<ClassHash>> {
    let mut deleted_data = Vec::new();
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
}
```

**File:** crates/apollo_storage/src/state/mod.rs (L853-872)
```rust
fn delete_declared_classes<'env>(
    txn: &'env DbTransaction<'env, RW>,
    thin_state_diff: &ThinStateDiff,
    declared_classes_table: &'env DeclaredClassesTable<'env>,
    file_handlers: &FileHandlers<RW>,
) -> StorageResult<IndexMap<ClassHash, SierraContractClass>> {
    let mut deleted_data = IndexMap::new();
    for class_hash in thin_state_diff.class_hash_to_compiled_class_hash.keys() {
        let Some(contract_class_location) = declared_classes_table.get(txn, class_hash)? else {
            continue;
        };
        deleted_data.insert(
            *class_hash,
            file_handlers.get_contract_class_unchecked(contract_class_location)?,
        );
        declared_classes_table.delete(txn, class_hash)?;
    }

    Ok(deleted_data)
}
```

**File:** crates/apollo_storage/src/class.rs (L262-265)
```rust
    for (class_hash, contract_class) in classes {
        let location = file_handlers.append_contract_class(contract_class);
        declared_classes_table.insert(txn, class_hash, &location)?;
        file_offset_table.upsert(txn, &OffsetKind::ContractClass, &location.next_offset())?;
```
