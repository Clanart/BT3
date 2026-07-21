Now I have all the information needed. Let me trace the exact code path.

### Title
`ExecutionStateReader::get_compiled_class_hash` Returns Future-Block Compiled Class Hash Due to Missing State-Number Guard — (`crates/apollo_rpc_execution/src/state_reader.rs`)

---

### Summary

`ExecutionStateReader::get_compiled_class_hash` calls `get_class_definition_block_number`, which takes **no `state_number` parameter** and returns the block at which a class was first declared regardless of the caller's temporal context. The function then unconditionally fetches the state diff at that block and returns the compiled class hash from it — with no check that the declaration block is ≤ the current `state_number`. A class declared at block N+1 is therefore visible to a caller executing at `state_number = right_after_block(N)`, violating temporal isolation.

---

### Finding Description

The full call chain in `ExecutionStateReader::get_compiled_class_hash`:

```
get_class_definition_block_number(&class_hash)   // no state_number arg
  → declared_classes_block_table.get(txn, class_hash)  // raw DB lookup
→ get_state_diff(block_number)                   // block_number may be > state_number
→ state_diff.class_hash_to_compiled_class_hash.get(&class_hash)
→ return compiled_class_hash                     // from a future block
``` [1](#0-0) 

`get_class_definition_block_number` is a raw key-value lookup with no temporal filter: [2](#0-1) 

Contrast with `get_class_definition_at`, which **does** enforce the boundary: [3](#0-2) 

The missing guard in `get_compiled_class_hash` is the exact line that should read:
```rust
if state_number.is_before(block_number) {
    return Ok(CompiledClassHash::default());
}
```
but does not exist.

The existing test **confirms the buggy behavior as an assertion** — at `state_number = right_after_block(0)`, with `class_hash0` declared at block 1, `get_compiled_class` correctly returns `UndeclaredClassHash`, but `get_compiled_class_hash` returns the block-1 compiled class hash: [4](#0-3) 

This is a direct contradiction: the two methods on the same `ExecutionStateReader` instance disagree on whether the class exists at state N.

---

### Impact Explanation

`get_compiled_class_hash` is called by blockifier during transaction execution to validate declare transactions and to bind the compiled class hash to a class hash. When RPC fee estimation, `starknet_call`, `starknet_simulateTransactions`, or `starknet_traceTransaction` execute at a historical block N, any class declared at block N+1 (already committed to storage) will have its compiled class hash leaked into the execution context. Blockifier receives a non-default `CompiledClassHash` for a class that `get_compiled_class` simultaneously reports as undeclared, producing an inconsistent state view. This causes fee estimation and simulation to return authoritative-looking wrong values.

Impact: **High** — RPC execution, fee estimation, tracing, and simulation return wrong values.

---

### Likelihood Explanation

Any user can trigger this via a standard RPC call (`starknet_estimateFee`, `starknet_call`, `starknet_simulateTransactions`) specifying a `block_id` that is one or more blocks behind the chain tip. No special privileges are required. The class at the future block is already committed to storage by the time the query is made.

---

### Recommendation

Insert a state-number guard immediately after `get_class_definition_block_number` returns, mirroring the pattern in `get_class_definition_at`:

```rust
let Some(block_number) = maybe_block_number else {
    return Ok(CompiledClassHash::default());
};

// ADD THIS GUARD:
if self.state_number.is_before(block_number) {
    return Ok(CompiledClassHash::default());
}
``` [5](#0-4) 

Additionally, the existing test assertion at line 186 of `state_reader_test.rs` should be updated to assert `CompiledClassHash::default()` after the fix, consistent with the `UndeclaredClassHash` result from `get_compiled_class` on the same reader.

---

### Proof of Concept

The existing test already demonstrates the violation without any additional setup:

- `state_number0 = right_after_block(BlockNumber(0))` [6](#0-5) 
- `class_hash0` is declared in the state diff at `BlockNumber(1)` [7](#0-6) 
- `get_compiled_class(class_hash0)` → `Err(UndeclaredClassHash)` ✓ (correct)
- `get_compiled_class_hash(class_hash0)` → `compiled_class_hash0` ✗ (should be `CompiledClassHash::default()`) [4](#0-3)

### Citations

**File:** crates/apollo_rpc_execution/src/state_reader.rs (L174-207)
```rust
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
```

**File:** crates/apollo_storage/src/state/mod.rs (L437-439)
```rust
        if state_number.is_before(block_number) {
            return Ok(None);
        }
```

**File:** crates/apollo_storage/src/state/mod.rs (L464-469)
```rust
    pub fn get_class_definition_block_number(
        &self,
        class_hash: &ClassHash,
    ) -> StorageResult<Option<BlockNumber>> {
        Ok(self.declared_classes_block_table.get(self.txn, class_hash)?)
    }
```

**File:** crates/apollo_rpc_execution/src/state_reader_test.rs (L114-130)
```rust
        .append_state_diff(
            BlockNumber(1),
            ThinStateDiff {
                deployed_contracts: indexmap!(
                    address0 => class_hash0,
                    address1 => class_hash1,
                ),
                storage_diffs: indexmap!(
                    address0 => indexmap!(
                        storage_key0 => storage_value0,
                    ),
                ),
                class_hash_to_compiled_class_hash: indexmap!(
                    class_hash0 => compiled_class_hash0,
                    class_hash5 => compiled_class_hash0,
                ),
                deprecated_declared_classes: vec![class_hash1],
```

**File:** crates/apollo_rpc_execution/src/state_reader_test.rs (L167-174)
```rust
    let state_number0 = StateNumber::unchecked_right_after_block(BlockNumber(0));
    let state_reader0 = ExecutionStateReader {
        storage_reader: storage_reader.clone(),
        state_number: state_number0,
        maybe_pending_data: None,
        missing_compiled_class: Cell::new(None),
        class_manager_handle: None,
    };
```

**File:** crates/apollo_rpc_execution/src/state_reader_test.rs (L181-186)
```rust
    let compiled_contract_class_after_block_0 = state_reader0.get_compiled_class(class_hash0);
    assert_matches!(
        compiled_contract_class_after_block_0, Err(StateError::UndeclaredClassHash(class_hash))
        if class_hash == class_hash0
    );
    assert_eq!(state_reader0.get_compiled_class_hash(class_hash0).unwrap(), compiled_class_hash0);
```
