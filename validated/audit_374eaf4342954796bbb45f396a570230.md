Looking at the actual implementation of `get_compiled_class_hash` in `ExecutionStateReader` and the existing test that confirms the behavior, I can give a definitive answer.

**The bug is real and confirmed by the existing test suite.**

Here is the exact defect:

In `crates/apollo_rpc_execution/src/state_reader.rs` at lines 174–181, `get_compiled_class_hash` calls `get_class_definition_block_number(&class_hash)` **without passing `self.state_number`**: [1](#0-0) 

`get_class_definition_block_number` returns the globally-stored declaration block for the class, with no awareness of the caller's `state_number`. After receiving `Some(block_number)`, the code performs **no bound check** — it never verifies that `block_number < self.state_number.block_after()`. It unconditionally fetches the state diff at that block and returns the compiled class hash: [2](#0-1) 

Compare this with the correct pattern used by `StateReader::get_compiled_class_hash_at` in storage, which explicitly uses `state_number.block_after()` as the upper bound: [3](#0-2) 

**The existing test at line 186 of `state_reader_test.rs` explicitly asserts the broken behavior:**

`state_reader0` is constructed with `state_number = right_after_block(BlockNumber(0))`, meaning `block_after() = 1`. `class_hash0` is declared in the state diff at `BlockNumber(1)`. The test then asserts: [4](#0-3) 

This asserts that `get_compiled_class_hash(class_hash0)` returns `compiled_class_hash0` even though the state number is *before* block 1 — the exact invariant violation described in the question. The correct return value should be `CompiledClassHash::default()`.

---

### Title
`ExecutionStateReader::get_compiled_class_hash` ignores `state_number`, returning future-block compiled class hash — (`crates/apollo_rpc_execution/src/state_reader.rs`)

### Summary
`ExecutionStateReader::get_compiled_class_hash` calls `get_class_definition_block_number` without the reader's `state_number` and performs no bound check on the returned block number. A class declared at block N is visible to any `ExecutionStateReader` whose `state_number` is before block N, returning the wrong `CompiledClassHash` instead of `CompiledClassHash::default()`.

### Finding Description
The call path is:

```
get_compiled_class_hash(C)
  → get_class_definition_block_number(&C)   // returns Some(N), ignores state_number
  → get_state_diff(N)                        // fetches future block's diff
  → class_hash_to_compiled_class_hash.get(&C) // returns block-N value
```

No guard exists between receiving `Some(block_number)` and returning the hash. The missing check is:

```rust
if block_number >= self.state_number.block_after() {
    return Ok(CompiledClassHash::default());
}
```

All other state-aware reads (`get_class_hash_at`, `get_nonce_at`, `get_storage_at`, `get_compiled_class`) correctly pass `self.state_number` to the underlying storage reader and respect the bound. `get_compiled_class_hash` is the sole exception.

### Impact Explanation
`ExecutionStateReader` is the state backend for blockifier during RPC simulation, fee estimation, tracing, and pending execution. Blockifier calls `get_compiled_class_hash` to determine whether a Sierra class is already declared. Returning a non-default value for a class that is not yet declared at the queried state number causes blockifier to treat the class as already declared, which:

- Causes a valid `DeclareV2`/`DeclareV3` transaction to be incorrectly rejected during simulation or fee estimation (wrong authoritative RPC result).
- Returns the wrong `CompiledClassHash` in any state read that feeds execution logic, matching the Critical impact: *wrong compiled class, CASM/native artifact, class hash, or contract code selected for execution*.

### Likelihood Explanation
Triggered whenever an RPC caller queries at a historical `state_number` (e.g., `starknet_simulateTransactions` at block 5) for a class declared at a later block already present in storage. No attacker privileges are required; any user can craft such a query. The existing test at line 186 of `state_reader_test.rs` already exercises and confirms the broken path.

### Recommendation
Add a state-number bound check immediately after receiving `Some(block_number)`:

```rust
let Some(block_number) = maybe_block_number else {
    return Ok(CompiledClassHash::default());
};

// Enforce state-number invariant: class must have been declared before state_number.
if block_number >= self.state_number.block_after() {
    return Ok(CompiledClassHash::default());
}
```

### Proof of Concept
The existing test at `crates/apollo_rpc_execution/src/state_reader_test.rs:186` already demonstrates the violation:

```rust
// state_reader0.state_number = right_after_block(0)  →  block_after() = 1
// class_hash0 declared at BlockNumber(1)
// Expected (correct): CompiledClassHash::default()
// Actual (buggy):     compiled_class_hash0  ← wrong future-block value
assert_eq!(state_reader0.get_compiled_class_hash(class_hash0).unwrap(), compiled_class_hash0);
```

A targeted regression test would:
1. Append a state diff at `BlockNumber(10)` declaring class `C` with compiled class hash `H`.
2. Construct `ExecutionStateReader` with `state_number = right_after_block(BlockNumber(5))`.
3. Call `get_compiled_class_hash(C)`.
4. Assert the result equals `CompiledClassHash::default()` — currently it returns `H`. [5](#0-4) [6](#0-5)

### Citations

**File:** crates/apollo_rpc_execution/src/state_reader.rs (L163-207)
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
```

**File:** crates/apollo_storage/src/state/mod.rs (L340-353)
```rust
    pub fn get_compiled_class_hash_at(
        &self,
        state_number: StateNumber,
        class_hash: &ClassHash,
    ) -> StorageResult<Option<CompiledClassHash>> {
        // State diff updates are indexed by the block_number at which they occurred.
        let block_number: BlockNumber = state_number.block_after();
        get_compiled_class_hash_at(
            block_number,
            class_hash,
            self.txn,
            &self.compiled_class_hash_table,
        )
    }
```

**File:** crates/apollo_rpc_execution/src/state_reader_test.rs (L167-186)
```rust
    let state_number0 = StateNumber::unchecked_right_after_block(BlockNumber(0));
    let state_reader0 = ExecutionStateReader {
        storage_reader: storage_reader.clone(),
        state_number: state_number0,
        maybe_pending_data: None,
        missing_compiled_class: Cell::new(None),
        class_manager_handle: None,
    };
    let storage_after_block_0 = state_reader0.get_storage_at(address0, storage_key0).unwrap();
    assert_eq!(storage_after_block_0, Felt::default());
    let nonce_after_block_0 = state_reader0.get_nonce_at(address0).unwrap();
    assert_eq!(nonce_after_block_0, Nonce::default());
    let class_hash_after_block_0 = state_reader0.get_class_hash_at(address0).unwrap();
    assert_eq!(class_hash_after_block_0, ClassHash::default());
    let compiled_contract_class_after_block_0 = state_reader0.get_compiled_class(class_hash0);
    assert_matches!(
        compiled_contract_class_after_block_0, Err(StateError::UndeclaredClassHash(class_hash))
        if class_hash == class_hash0
    );
    assert_eq!(state_reader0.get_compiled_class_hash(class_hash0).unwrap(), compiled_class_hash0);
```
