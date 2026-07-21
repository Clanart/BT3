### Title
Stale Compiled Class Hash in `ExecutionStateReader` Causes Inflated Fee Estimates After V1→V2 Migration — (File: crates/apollo_rpc_execution/src/state_reader.rs)

---

### Summary

`ExecutionStateReader::get_compiled_class_hash` resolves a class's compiled hash by reading the `ThinStateDiff` stored at the class's **declaration block**. After the V1 (Poseidon) → V2 (Blake) compiled class hash migration is applied in a later block, the declaration block's state diff still holds the V1 hash. During RPC fee estimation and simulation, `CasmHashMigrationData::from_state` calls `should_migrate` using this stale V1 value and incorrectly concludes that already-migrated classes still need migration, inflating every fee estimate that touches those classes.

---

### Finding Description

**Root cause — stale hash source in `ExecutionStateReader`**

`ExecutionStateReader::get_compiled_class_hash` (lines 163–208) resolves the compiled class hash in two steps:

1. It calls `get_class_definition_block_number`, which reads from `declared_classes_block_table`. This table is written once at first declaration and is **never updated** when a migration block later re-writes the same class hash to V2.

2. It fetches the `ThinStateDiff` at that declaration block and returns `class_hash_to_compiled_class_hash[class_hash]` — the V1 Poseidon hash that was present at declaration time. [1](#0-0) 

The `declared_classes_block_table` insert is guarded by `if not_declared`, so migration blocks never update it: [2](#0-1) 

**Contrast with actual block execution**

`ApolloReader::get_compiled_class_hash` uses `get_compiled_class_hash_at`, which reads from `compiled_class_hash_table`. `write_compiled_class_hashes` updates that table for every block, including migration blocks, so it holds the current V2 hash after migration: [3](#0-2) [4](#0-3) 

**How the stale hash propagates into fee estimation**

During RPC execution, `CasmHashMigrationData::from_state` is called for every executed class hash. It calls `should_migrate(state_reader, class_hash)`, which internally calls `state_reader.get_compiled_class_hash(class_hash)`. For the `ExecutionStateReader`, this returns V1. It then calls `state_reader.get_compiled_class_hash_v2(...)`, which reads from the class manager or `executable_class_hash_v2` storage and returns V2. Because V1 ≠ V2, `should_migrate` returns `Some(...)` and migration gas is added to the fee estimate — even though the class was already migrated in a prior block. [5](#0-4) 

The `enable_casm_hash_migration` flag that gates this path was enabled starting in v0.14.1: [6](#0-5) 

**Analog to the external report**

The external report describes a component (`app-vault`) that hardcodes a bytecode prefix/suffix that no longer matches the current compiled artifact, causing it to operate on a stale version of the contract. Here, `ExecutionStateReader` hardcodes its lookup to the declaration-block state diff, which no longer reflects the current compiled class hash after migration — the same class-artifact mismatch pattern, applied to the sequencer's RPC execution path.

---

### Impact Explanation

Every call to `starknet_estimateFee`, `starknet_simulateTransactions`, or `starknet_traceTransaction` that touches a class migrated from V1 to V2 will:

- Include spurious migration gas in the returned fee, producing an authoritative-looking but incorrect value.
- Produce a simulation state diff that contains a `class_hash_to_compiled_class_hash` migration entry for a class that is already at V2 in the canonical state.

Actual block execution is unaffected because it uses `ApolloReader`, which reads from the up-to-date `compiled_class_hash_table`. The divergence between RPC estimates and on-chain execution is the concrete wrong value.

This matches: **High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value.**

---

### Likelihood Explanation

The conditions are:
1. `enable_casm_hash_migration = true` — active in v0.14.1 and later.
2. At least one Cairo 1 class was declared before migration and has since been migrated to V2.
3. A user calls fee estimation or simulation for a transaction that executes that class.

After v0.14.1 is deployed and the first migration block is produced, condition 2 is permanently satisfied for all previously declared Cairo 1 classes. Any subsequent fee estimation involving those classes triggers the bug without any special attacker action.

---

### Recommendation

Replace the declaration-block state diff lookup in `ExecutionStateReader::get_compiled_class_hash` with a call to `get_compiled_class_hash_at(state_number, &class_hash)`, which reads from `compiled_class_hash_table` and reflects the post-migration V2 hash. This is the same source used by `ApolloReader` and ensures RPC execution and actual block execution agree on the current compiled class hash. [1](#0-0) 

---

### Proof of Concept

1. Declare a Cairo 1 contract in block N (stored with V1 Poseidon hash in `declared_classes_block_table` and the block-N `ThinStateDiff`).
2. Upgrade to v0.14.1 (`enable_casm_hash_migration = true`).
3. Execute any transaction that touches the class; `finalize_block` calls `set_compiled_class_hash_migration`, writing V2 to `compiled_class_hash_table` and to the migration block's `ThinStateDiff`.
4. Call `starknet_estimateFee` for a transaction that invokes the same class.
5. Inside `CasmHashMigrationData::from_state`, `ExecutionStateReader::get_compiled_class_hash` reads block N's `ThinStateDiff` → returns V1. `get_compiled_class_hash_v2` reads `executable_class_hash_v2` storage → returns V2. `should_migrate` returns `Some(...)`. Migration gas is added to the estimate.
6. Submit the transaction; the sequencer's `ApolloReader` reads `compiled_class_hash_table` → returns V2; `should_migrate` returns `None`; no migration gas is charged.
7. The fee paid on-chain is lower than the RPC estimate, confirming the wrong authoritative value. [7](#0-6) [8](#0-7)

### Citations

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

**File:** crates/apollo_rpc_execution/src/state_reader.rs (L210-231)
```rust
    fn get_compiled_class_hash_v2(
        &self,
        class_hash: ClassHash,
        _compiled_class: &RunnableCompiledClass,
    ) -> StateResult<CompiledClassHash> {
        let maybe_hash =
            if let Some((class_manager_client, run_time_handle)) = &self.class_manager_handle {
                // First, try getting from class manager if available.
                run_time_handle
                    .block_on(class_manager_client.get_executable_class_hash_v2(class_hash))
                    .map_err(|e| StateError::StateReadError(e.to_string()))?
            } else {
                // Fall back to reading from storage.
                self.storage_reader
                    .begin_ro_txn()
                    .map_err(storage_err_to_state_err)?
                    .get_executable_class_hash_v2(&class_hash)
                    .map_err(storage_err_to_state_err)?
            };

        maybe_hash.ok_or(StateError::MissingCompiledClassHashV2(class_hash))
    }
```

**File:** crates/apollo_storage/src/state/mod.rs (L549-554)
```rust
        for (class_hash, _) in &thin_state_diff.class_hash_to_compiled_class_hash {
            let not_declared = declared_classes_block_table.get(&self.txn, class_hash)?.is_none();
            if not_declared {
                declared_classes_block_table.insert(&self.txn, class_hash, &block_number)?;
            }
        }
```

**File:** crates/apollo_storage/src/state/mod.rs (L556-561)
```rust
        write_compiled_class_hashes(
            &thin_state_diff.class_hash_to_compiled_class_hash,
            &self.txn,
            block_number,
            &compiled_class_hash_table,
        )?;
```

**File:** crates/apollo_state_reader/src/apollo_state.rs (L243-254)
```rust
    fn get_compiled_class_hash(&self, class_hash: ClassHash) -> StateResult<CompiledClassHash> {
        let state_number = StateNumber(self.latest_block);
        match self
            .reader()?
            .get_state_reader()
            .and_then(|sr| sr.get_compiled_class_hash_at(state_number, &class_hash))
        {
            Ok(Some(compiled_class_hash)) => Ok(compiled_class_hash),
            Ok(None) => Ok(CompiledClassHash::default()),
            Err(err) => Err(StateError::StateReadError(err.to_string())),
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

**File:** crates/blockifier/resources/versioned_constants_diff_regression/0.14.0_0.14.1.txt (L1-2)
```text
~ /block_casm_hash_v1_declares: true
~ /enable_casm_hash_migration: true
```

**File:** crates/blockifier/src/state/compiled_class_hash_migration.rs (L16-36)
```rust
impl<S: StateReader> CompiledClassHashMigrationUpdater for CachedState<S> {
    // Sets the new compiled class hashes for the class hashes that need to be migrated.
    fn set_compiled_class_hash_migration(
        &mut self,
        class_hashes_to_migrate: &HashMap<ClassHash, CompiledClassHashV2ToV1>,
    ) -> StateResult<()> {
        for (class_hash, (compiled_class_hash_v2, compiled_class_hash_v1)) in
            class_hashes_to_migrate
        {
            // Sanity check: the compiled class hashes should not be equal.
            assert_ne!(
                compiled_class_hash_v1, compiled_class_hash_v2,
                "Classes for migration should hold v1 (Poseidon) hash in the state."
            );

            // TODO(Meshi): Consider panic here instead of returning an error.
            self.set_compiled_class_hash(*class_hash, *compiled_class_hash_v2)?;
        }

        Ok(())
    }
```
