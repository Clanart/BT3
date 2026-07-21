### Title
Cairo0 Class Cache Bypass Allows Execution with Reverted-Block Undeclared Class - (`crates/blockifier/src/state/state_reader_and_contract_manager.rs`)

### Summary

`StateReaderAndContractManager::get_compiled_from_class_manager` validates Cairo1 cached classes against the declared state via `is_declared`, but performs **no equivalent check** for Cairo0 (`RunnableCompiledClass::V0`) classes. The shared `ContractClassManager` cache is never cleared on block revert. After `revert_state_diff` removes a Cairo0 class from storage, the cache retains the `CompiledClasses::V0` entry. Any subsequent transaction referencing that class hash receives the cached (now-undeclared) class without error, causing the gateway to accept an invalid transaction and the blockifier to execute it with wrong class code.

### Finding Description

In `get_compiled_from_class_manager`, the cache-hit branch explicitly guards Cairo1 classes but leaves the `V0` arm as a no-op:

```rust
match &runnable_class {
    RunnableCompiledClass::V0(_) => {}   // ← no declaration check
    _ => {
        // The Cairo1 class is cached; verify it is declared,
        // since existence in the cache does not guarantee that
        // (it might contain a declared class from a reverted block, for example).
        if !self.state_reader.is_declared(class_hash)? {
            return Err(StateError::UndeclaredClassHash(class_hash));
        }
    }
}
```

The comment explicitly names the revert-block risk, yet the `V0` arm is empty. The `FetchCompiledClasses::is_declared` trait is documented to return `false` for Cairo0 classes ("Cairo 0 classes always return `false`"), so no equivalent declaration guard exists for Cairo0. A parallel TODO in `apollo_rpc_execution/src/state_reader.rs` confirms the known gap:

```rust
// TODO(shahak): Verify cairo0 as well after get_class_definition_block_number is fixed.
ContractClass::V0(deprecated_contract_class) => {
    Ok(RunnableCompiledClass::V0(deprecated_contract_class.try_into()?))
}
```

The `ContractClassManager` cache (`RawClassCache`, a global `Arc<Mutex<LruCache>>`) is never cleared on block revert. `revert_state_diff` removes the class from the storage tables (`deprecated_declared_classes`, `deprecated_declared_classes_block`) but does not touch the in-process cache. After revert, `get_compiled_from_class_manager` still finds the `V0` entry on a cache hit and returns it unconditionally.

The `StatefulTransactionValidator` in the gateway wraps `StateReaderAndContractManager` in a `CachedState` and runs blockifier validation. Because the cache hit path for `V0` skips the declaration check, a transaction invoking a Cairo0 class that was declared in a reverted block passes gateway validation. The accepted transaction enters the mempool and is included in a subsequent block. If the blockifier execution path shares the same `ContractClassManager` instance, the transaction executes with the undeclared class, producing wrong state, receipt, or revert result.

The `cairo_0_cached_scenario` unit test in `state_reader_and_contract_manager_test.rs` confirms the current behavior: `is_declared_result: None` (never called) and `expected_result: Ok(...)` — the cached Cairo0 class is returned without any declaration verification.

### Impact Explanation

A transaction that should revert with `StateError::UndeclaredClassHash` instead succeeds because the gateway and blockifier both serve the class from the shared cache without checking the current declared state. This produces a wrong execution result (wrong revert/success outcome, wrong state diff, wrong receipt) for an accepted transaction. The corrupted execution output propagates into the block's `CommitmentStateDiff`, `ThinStateDiff`, and ultimately into the `state_diff_commitment` field of `BlockHeaderCommitments` used in `calculate_block_hash`, causing the sequencer to commit a block hash over incorrect state.

Matches: **Critical — Wrong state, receipt, or revert result from blockifier/execution logic for accepted input**, and **High — Mempool/gateway admission accepts invalid transactions**.

### Likelihood Explanation

Block reverts occur in normal sequencer operation (consensus round failures, reorgs). The `ContractClassManager` cache is not cleared on revert. An unprivileged attacker needs only to:
1. Submit a `declare` transaction for a Cairo0 class and have it land in a block.
2. Observe or trigger a revert of that block (e.g., by exploiting a consensus timeout or a known reorg window).
3. Submit a transaction that invokes the now-undeclared class.

No privileged sequencer access is required. The window between revert and LRU eviction of the cache entry can be arbitrarily extended by keeping the cache warm with other entries.

### Recommendation

Extend the `FetchCompiledClasses` trait with an `is_deprecated_declared` method that checks whether a Cairo0 class is declared in the current state (analogous to `is_declared` for Cairo1). Apply this check in the `V0` arm of `get_compiled_from_class_manager`:

```rust
RunnableCompiledClass::V0(_) => {
    if !self.state_reader.is_deprecated_declared(class_hash)? {
        return Err(StateError::UndeclaredClassHash(class_hash));
    }
}
```

The implementation of `is_deprecated_declared` should query `get_deprecated_class_definition_block_number` (already available in storage) and verify the block number is within the current state. This resolves the TODO in `apollo_rpc_execution/src/state_reader.rs` as well.

### Proof of Concept

1. Submit a `declare` transaction for Cairo0 class `C` with `class_hash = H`.
2. Transaction is included in block `N`; `ContractClassManager` cache now contains `(H, CompiledClasses::V0(...))`.
3. Block `N` is reverted via `revert_state_diff(BlockNumber(N))`; storage tables `deprecated_declared_classes` and `deprecated_declared_classes_block` no longer contain `H`; cache still holds `H`.
4. Submit transaction `T` that calls a contract whose class hash is `H`.
5. Gateway calls `get_compiled_from_class_manager(H)`: cache hit → `V0` arm → no check → returns class `C`.
6. `T` passes `StatefulTransactionValidator` and enters the mempool.
7. `T` is included in block `N+1`; blockifier executes `T` with undeclared class `C`.
8. Execution succeeds (or produces wrong revert) instead of returning `StateError::UndeclaredClassHash(H)`.
9. The wrong execution result is committed into the block's `CommitmentStateDiff` and `state_diff_commitment`.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** crates/blockifier/src/state/state_reader_and_contract_manager.rs (L16-22)
```rust
pub trait FetchCompiledClasses: StateReader {
    fn get_compiled_classes(&self, class_hash: ClassHash) -> StateResult<CompiledClasses>;

    /// Returns whether the given class hash corresponds to a declared Cairo 1 class.
    /// Cairo 0 classes always return `false`.
    fn is_declared(&self, class_hash: ClassHash) -> StateResult<bool>;
}
```

**File:** crates/blockifier/src/state/state_reader_and_contract_manager.rs (L66-87)
```rust
    fn get_compiled_from_class_manager(
        &self,
        class_hash: ClassHash,
    ) -> StateResult<RunnableCompiledClass> {
        if let Some(runnable_class) =
            self.contract_class_manager.get_runnable(&class_hash, &self.native_classes_whitelist)
        {
            match &runnable_class {
                RunnableCompiledClass::V0(_) => {}
                _ => {
                    // The Cairo1 class is cached; verify it is declared,
                    // since existence in the cache does not guarantee that
                    // (it might contain a declared class from a reverted block, for example).
                    if !self.state_reader.is_declared(class_hash)? {
                        return Err(StateError::UndeclaredClassHash(class_hash));
                    }
                }
            }
            self.increment_cache_hit_metric();
            self.update_native_metrics(&runnable_class);
            return Ok(runnable_class);
        }
```

**File:** crates/apollo_rpc_execution/src/state_reader.rs (L136-141)
```rust
                // TODO(shahak): Verify cairo0 as well after get_class_definition_block_number is
                // fixed.
                ContractClass::V0(deprecated_contract_class) => {
                    Ok(RunnableCompiledClass::V0(deprecated_contract_class.try_into()?))
                }
            };
```

**File:** crates/blockifier/src/state/state_reader_and_contract_manager_test.rs (L231-240)
```rust
#[cfg(not(feature = "cairo_native"))]
fn cairo_0_cached_scenario() -> GetCompiledClassTestScenario {
    GetCompiledClassTestScenario {
        expectations: GetCompiledClassTestExpectation {
            get_compiled_classes_result: None,
            is_declared_result: None,
        },
        expected_result: Ok(RunnableCompiledClass::test_deprecated_casm_contract_class()),
    }
}
```

**File:** crates/blockifier/src/test_utils/dict_state_reader.rs (L170-176)
```rust
    fn is_declared(&self, class_hash: ClassHash) -> StateResult<bool> {
        Ok(match self.class_hash_to_class.get(&class_hash) {
            // Cairo0 classes are not declared.
            Some(class) => !matches!(class, RunnableCompiledClass::V0(_)),
            None => false,
        })
    }
```

**File:** crates/blockifier/src/state/contract_class_manager.rs (L41-51)
```rust
        pub fn get_runnable(
            &self,
            class_hash: &ClassHash,
            _native_classes_whitelist: &NativeClassesWhitelist,
        ) -> Option<RunnableCompiledClass> {
            Some(self.class_cache.get(class_hash)?.to_runnable())
        }

        pub fn set_and_compile(&self, class_hash: ClassHash, compiled_class: CompiledClasses) {
            self.class_cache.set(class_hash, compiled_class);
        }
```

**File:** crates/apollo_storage/src/state/mod.rs (L592-627)
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
```
