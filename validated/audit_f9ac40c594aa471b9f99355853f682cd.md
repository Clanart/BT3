### Title
Cairo0 Class Cache Not Invalidated After Block Revert Enables Execution of Reverted Contract Code — (`crates/blockifier/src/state/state_reader_and_contract_manager.rs`)

---

### Summary

`get_compiled_from_class_manager` applies an `is_declared` guard to cached Cairo1 classes to detect post-revert stale entries, but explicitly skips that guard for Cairo0 (`V0`) classes. Because the `GlobalContractCache` inside `ContractClassManager` is never cleared on `revert_state_diff`, a Cairo0 class declared in a reverted block remains in the in-memory cache indefinitely. A subsequent `library_call` with the reverted class hash hits the cache, bypasses the `UndeclaredClassHash` error, and executes the reverted bytecode, producing wrong execution results, state diffs, receipts, and events.

---

### Finding Description

**Root cause — asymmetric cache-hit guard in `get_compiled_from_class_manager`:** [1](#0-0) 

```rust
fn get_compiled_from_class_manager(...) {
    if let Some(runnable_class) =
        self.contract_class_manager.get_runnable(&class_hash, ...)
    {
        match &runnable_class {
            RunnableCompiledClass::V0(_) => {}          // ← NO check
            _ => {
                // might contain a declared class from a reverted block
                if !self.state_reader.is_declared(class_hash)? {
                    return Err(StateError::UndeclaredClassHash(class_hash));
                }
            }
        }
        return Ok(runnable_class);   // ← V0 returned unconditionally
    }
```

The comment on line 78 explicitly acknowledges the revert risk, but the guard is only applied to Cairo1 variants. The `FetchCompiledClasses::is_declared` contract documents why: [2](#0-1) 

> "Returns whether the given class hash corresponds to a declared Cairo 1 class. Cairo 0 classes always return `false`."

Because `is_declared` always returns `false` for Cairo0, applying it would always reject V0 cache hits. The developers therefore left the V0 arm empty — but provided no alternative guard.

**Cache persistence across reverts:**

`ContractClassManager` wraps a `GlobalContractCache` (`RawClassCache`) that is an `Arc<Mutex<SizedCache<ClassHash, CompiledClasses>>>`: [3](#0-2) 

This cache is created once at node startup and shared across all block executions. `revert_state_diff` removes the class from MDBX storage tables: [4](#0-3) 

But there is no corresponding `class_cache.remove(class_hash)` call anywhere in the revert path. The `clear()` method on `ContractClassManager` exists but is only called in test contexts: [5](#0-4) 

**Test confirms the missing guard:**

The test suite explicitly encodes the absence of the check for Cairo0 cache hits: [6](#0-5) 

```rust
fn cairo_0_cached_scenario() -> GetCompiledClassTestScenario {
    GetCompiledClassTestScenario {
        expectations: GetCompiledClassTestExpectation {
            get_compiled_classes_result: None,
            is_declared_result: None,   // ← no is_declared call expected
        },
        expected_result: Ok(RunnableCompiledClass::test_deprecated_casm_contract_class()),
    }
}
```

Compare with the Cairo1 reorg scenario which correctly expects `is_declared_result: Some(Ok(false))` and `Err(UndeclaredClassHash)`: [7](#0-6) 

**RPC execution path has the same gap (acknowledged by a TODO):** [8](#0-7) 

```rust
// TODO(shahak): Verify cairo0 as well after get_class_definition_block_number is fixed.
ContractClass::V0(deprecated_contract_class) => {
    Ok(RunnableCompiledClass::V0(deprecated_contract_class.try_into()?))
}
```

---

### Impact Explanation

An unprivileged attacker who can submit transactions can execute a `library_call` with a reverted Cairo0 class hash. The blockifier accepts the call, runs the reverted bytecode, and produces:

- A wrong execution result (call succeeds instead of `UndeclaredClassHash`)
- Wrong storage writes, events, and L2→L1 messages from the reverted code
- A wrong state diff committed to `apollo_storage`
- A wrong state root propagated to consensus and eventually to L1

This matches the **Critical** impact: *"Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input."*

---

### Likelihood Explanation

Requires a block revert (reorg), which is a normal operational event in Starknet. The attacker needs only:

1. A Cairo0 class declared in a block that is subsequently reverted — achievable by any transaction sender.
2. A follow-up transaction using `library_call` with the reverted class hash — a standard Starknet operation.

No privileged keys or special roles are required.

---

### Recommendation

Add a declaration check for Cairo0 classes on cache hit. Since `is_declared` does not support Cairo0, the check should call `self.state_reader.get_compiled_classes(class_hash)` (which returns `UndeclaredClassHash` from storage if the class was reverted) when a `V0` cache hit occurs, or alternatively:

- Extend `FetchCompiledClasses::is_declared` to support Cairo0 by querying `get_deprecated_class_definition_block_number`.
- Or, in `revert_state_diff`, explicitly evict reverted Cairo0 class hashes from the `GlobalContractCache`.

---

### Proof of Concept

1. Declare Cairo0 class `H` in block N → `GlobalContractCache` stores `(H → CompiledClasses::V0(...))`.
2. Revert block N → MDBX storage removes `H` from `deprecated_declared_classes_block` table; `GlobalContractCache` is **not** updated.
3. Submit transaction in block N+1 that executes `library_call(class_hash=H, selector, calldata)`.
4. Blockifier calls `get_compiled_class(H)` → `get_compiled_from_class_manager(H)`.
5. Cache hit → `CompiledClasses::V0` → `RunnableCompiledClass::V0(_) => {}` → no guard → `return Ok(runnable_class)`.
6. `library_call` executes with reverted bytecode → wrong state diff, receipts, and events are committed.

The `cairo_0_cached_scenario` test at line 232–239 of `state_reader_and_contract_manager_test.rs` is a deterministic reproduction: it shows that a cached V0 class is returned without any `is_declared` call, even when the underlying state reader would return `UndeclaredClassHash`.

### Citations

**File:** crates/blockifier/src/state/state_reader_and_contract_manager.rs (L19-21)
```rust
    /// Returns whether the given class hash corresponds to a declared Cairo 1 class.
    /// Cairo 0 classes always return `false`.
    fn is_declared(&self, class_hash: ClassHash) -> StateResult<bool>;
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

**File:** crates/starknet_api/src/class_cache.rs (L13-37)
```rust
#[derive(Clone, Debug)]
pub struct GlobalContractCache<T: Clone>(pub Arc<Mutex<ContractLRUCache<T>>>);

impl<T: Clone> GlobalContractCache<T> {
    /// Locks the cache for atomic access. Although conceptually shared, writing to this cache is
    /// only possible for one writer at a time.
    pub fn lock(&self) -> LockedClassCache<'_, T> {
        self.0.lock().expect("Global contract cache is poisoned.")
    }

    pub fn get(&self, class_hash: &ClassHash) -> Option<T> {
        self.lock().cache_get(class_hash).cloned()
    }

    pub fn set(&self, class_hash: ClassHash, contract_class: T) {
        self.lock().cache_set(class_hash, contract_class);
    }

    pub fn clear(&mut self) {
        self.lock().cache_clear();
    }

    pub fn new(cache_size: usize) -> Self {
        Self(Arc::new(Mutex::new(ContractLRUCache::<T>::with_size(cache_size))))
    }
```

**File:** crates/apollo_storage/src/state/mod.rs (L908-945)
```rust
fn delete_deprecated_declared_classes<'env>(
    txn: &'env DbTransaction<'env, RW>,
    block_number: BlockNumber,
    thin_state_diff: &ThinStateDiff,
    deprecated_declared_classes_table: &'env DeprecatedDeclaredClassesTable<'env>,
    file_handlers: &FileHandlers<RW>,
) -> StorageResult<IndexMap<ClassHash, DeprecatedContractClass>> {
    // Class hashes of the contracts that were deployed in this block.
    let deployed_contracts_class_hashes = thin_state_diff.deployed_contracts.values();

    // Merge the class hashes from the state diff and from the deployed contracts into a single
    // unique set.
    let class_hashes: HashSet<&ClassHash> = thin_state_diff
        .deprecated_declared_classes
        .iter()
        .chain(deployed_contracts_class_hashes)
        .collect();

    let mut deleted_data = IndexMap::new();
    for class_hash in class_hashes {
        // If the class is not in the deprecated classes table, it means that either we didn't
        // download it yet or the hash is of a deployed contract of a new class type. We've decided
        // to avoid deleting these classes because they're from at most 0.11.
        if let Some(IndexedDeprecatedContractClass {
            block_number: declared_block_number,
            location_in_file,
        }) = deprecated_declared_classes_table.get(txn, class_hash)?
        {
            // If the class was declared in a different block then we should'nt delete it.
            if block_number == declared_block_number {
                deleted_data.insert(
                    *class_hash,
                    file_handlers.get_deprecated_contract_class_unchecked(location_in_file)?,
                );
                deprecated_declared_classes_table.delete(txn, class_hash)?;
            }
        }
    }
```

**File:** crates/blockifier/src/state/native_class_manager.rs (L229-231)
```rust
    pub fn clear(&mut self) {
        self.class_cache.clear();
    }
```

**File:** crates/blockifier/src/state/state_reader_and_contract_manager_test.rs (L207-216)
```rust
#[cfg(not(feature = "cairo_native"))]
fn cached_but_verification_failed_after_reorg_scenario() -> GetCompiledClassTestScenario {
    GetCompiledClassTestScenario {
        expectations: GetCompiledClassTestExpectation {
            get_compiled_classes_result: None,
            is_declared_result: Some(Ok(false)), // Verification fails after reorg.
        },
        expected_result: Err(StateError::UndeclaredClassHash(*DUMMY_CLASS_HASH)),
    }
}
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

**File:** crates/apollo_rpc_execution/src/state_reader.rs (L136-141)
```rust
                // TODO(shahak): Verify cairo0 as well after get_class_definition_block_number is
                // fixed.
                ContractClass::V0(deprecated_contract_class) => {
                    Ok(RunnableCompiledClass::V0(deprecated_contract_class.try_into()?))
                }
            };
```
