### Title
`ExecutionStateReader::get_compiled_class` Skips `state_number` Declaration Check for Cairo V0 Classes When Class Manager Is Set — (`File: crates/apollo_rpc_execution/src/state_reader.rs`)

### Summary

`ExecutionStateReader::get_compiled_class` applies a `state_number`-gated declaration check for Cairo V1 classes but unconditionally returns Cairo V0 (deprecated) classes from the class manager without any temporal guard. This is the direct sequencer analog of M-6: one code path enforces the "is this class declared at the queried block?" invariant, while the parallel path for the other class type silently bypasses it, causing the two paths to return inconsistent results for the same logical question.

### Finding Description

In `crates/apollo_rpc_execution/src/state_reader.rs`, `ExecutionStateReader::get_compiled_class` has two branches inside the `class_manager_handle` block:

```rust
// V1 path — correctly guarded
ContractClass::V1(casm_contract_class) => {
    let is_declared = is_contract_class_declared(
        &self.storage_reader.begin_ro_txn()...?,
        &class_hash,
        self.state_number,   // ← temporal filter applied
    )...?;
    if is_declared {
        Ok(RunnableCompiledClass::V1(casm_contract_class.try_into()?))
    } else {
        Err(StateError::UndeclaredClassHash(class_hash))
    }
}
// V0 path — NO temporal guard
// TODO(shahak): Verify cairo0 as well after get_class_definition_block_number is fixed.
ContractClass::V0(deprecated_contract_class) => {
    Ok(RunnableCompiledClass::V0(deprecated_contract_class.try_into()?))
}
``` [1](#0-0) 

For V1 classes, `is_contract_class_declared` is called with `self.state_number`, which enforces that the class must have been declared at or before the queried block. For V0 classes, no such check exists: if the class manager holds a deprecated class that was declared in block N+1, a query at block N will receive it as if it were already declared.

The `get_storage_at` and `get_nonce_at` and `get_class_hash_at` methods on the same struct all correctly thread `self.state_number` through `execution_utils` helpers: [2](#0-1) 

The V0 branch is the only method on `ExecutionStateReader` that ignores `state_number` when the class manager is active.

The `is_contract_class_declared` helper used for V1 reads the `declared_classes_block_table` and compares against `state_number`: [3](#0-2) 

### Impact Explanation

An RPC caller invoking `starknet_simulateTransactions`, `starknet_estimateFee`, `starknet_traceTransaction`, or `starknet_traceBlockTransactions` at a historical block number N can trigger execution that uses a Cairo V0 class declared only in block N+1 or later. The execution engine will treat the class as legitimately available, producing:

- Wrong fee estimates (the class may have different entry-point costs)
- Wrong simulation traces (call graph differs from what actually happened at block N)
- Wrong revert/success results (the class may not have existed at block N)

These are authoritative-looking wrong values returned by the RPC layer.

### Likelihood Explanation

The class manager is populated with all classes the node has seen, regardless of block number. Any deprecated (Cairo 0) class declared after the queried block is a trigger. The TODO comment in the source confirms the developers are aware the check is missing and have deferred it. An unprivileged user can craft a simulation request targeting any historical block where a V0 class was not yet declared.

### Recommendation

Apply the same `is_contract_class_declared` guard to the V0 branch:

```rust
ContractClass::V0(deprecated_contract_class) => {
    let is_declared = is_contract_class_declared(
        &self.storage_reader.begin_ro_txn().map_err(storage_err_to_state_err)?,
        &class_hash,
        self.state_number,
    )
    .map_err(|e| StateError::StateReadError(e.to_string()))?;

    if is_declared {
        Ok(RunnableCompiledClass::V0(deprecated_contract_class.try_into()?))
    } else {
        Err(StateError::UndeclaredClassHash(class_hash))
    }
}
```

The TODO comment references a `get_class_definition_block_number` fix as a prerequisite; that fix should be resolved and this guard added together.

### Proof of Concept

1. Deploy a node with a class manager configured (`class_manager_handle` is `Some`).
2. Let the chain advance to block 10. At block 5, no deprecated class `C` exists. At block 7, class `C` is declared.
3. The class manager now holds class `C`.
4. Call `starknet_simulateTransactions` with `block_id = 5` and a transaction that invokes class `C`.
5. `ExecutionStateReader::get_compiled_class` is called with `state_number = right_after(5)` and `class_hash = C`.
6. The V1 branch would return `UndeclaredClassHash` because `is_contract_class_declared` would find the declaration block (7) is after state_number (5).
7. The V0 branch returns `Ok(RunnableCompiledClass::V0(...))` unconditionally — the simulation proceeds with class `C` as if it were declared at block 5, producing a wrong result. [4](#0-3)

### Citations

**File:** crates/apollo_rpc_execution/src/state_reader.rs (L46-85)
```rust
    fn get_storage_at(
        &self,
        contract_address: ContractAddress,
        key: StorageKey,
    ) -> StateResult<Felt> {
        execution_utils::get_storage_at(
            &self.storage_reader.begin_ro_txn().map_err(storage_err_to_state_err)?,
            self.state_number,
            self.maybe_pending_data.as_ref().map(|pending_data| &pending_data.storage_diffs),
            contract_address,
            key,
        )
        .map_err(storage_err_to_state_err)
    }

    // Returns the default value if the contract address is not found.
    fn get_nonce_at(&self, contract_address: ContractAddress) -> StateResult<Nonce> {
        Ok(execution_utils::get_nonce_at(
            &self.storage_reader.begin_ro_txn().map_err(storage_err_to_state_err)?,
            self.state_number,
            self.maybe_pending_data.as_ref().map(|pending_data| &pending_data.nonces),
            contract_address,
        )
        .map_err(storage_err_to_state_err)?
        .unwrap_or_default())
    }

    // Returns the default value if the contract address is not found.
    fn get_class_hash_at(&self, contract_address: ContractAddress) -> StateResult<ClassHash> {
        Ok(execution_utils::get_class_hash_at(
            &self.storage_reader.begin_ro_txn().map_err(storage_err_to_state_err)?,
            self.state_number,
            self.maybe_pending_data.as_ref().map(|pending_data| {
                (&pending_data.deployed_contracts, &pending_data.replaced_classes)
            }),
            contract_address,
        )
        .map_err(storage_err_to_state_err)?
        .unwrap_or_default())
    }
```

**File:** crates/apollo_rpc_execution/src/state_reader.rs (L115-141)
```rust
        if let Some((class_manager_client, run_time_handle)) = &self.class_manager_handle {
            let contract_class = run_time_handle
                .block_on(class_manager_client.get_executable(class_hash))
                .map_err(|e| StateError::StateReadError(e.to_string()))?
                .ok_or(StateError::UndeclaredClassHash(class_hash))?;

            return match contract_class {
                ContractClass::V1(casm_contract_class) => {
                    let is_declared = is_contract_class_declared(
                        &self.storage_reader.begin_ro_txn().map_err(storage_err_to_state_err)?,
                        &class_hash,
                        self.state_number,
                    )
                    .map_err(|e| StateError::StateReadError(e.to_string()))?;

                    if is_declared {
                        Ok(RunnableCompiledClass::V1(casm_contract_class.try_into()?))
                    } else {
                        Err(StateError::UndeclaredClassHash(class_hash))
                    }
                }
                // TODO(shahak): Verify cairo0 as well after get_class_definition_block_number is
                // fixed.
                ContractClass::V0(deprecated_contract_class) => {
                    Ok(RunnableCompiledClass::V0(deprecated_contract_class.try_into()?))
                }
            };
```

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
