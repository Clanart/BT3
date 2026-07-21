### Title
Missing Declaration Existence Check for Cairo 0 Classes in `ExecutionStateReader::get_compiled_class` Allows Undeclared Class Execution in RPC Simulation/Estimation - (`File: crates/apollo_rpc_execution/src/state_reader.rs`)

---

### Summary

`ExecutionStateReader::get_compiled_class` verifies that a Cairo 1 class is declared at the queried `state_number` before returning it, but **skips this check entirely for Cairo 0 (deprecated) classes** when the class manager handle is present. A Cairo 0 class that was declared in a subsequently-reverted block remains in the class manager cache; any RPC call that triggers execution at a state before that declaration will silently succeed using the undeclared class, producing an authoritative-looking wrong execution result, wrong fee estimate, or wrong trace.

---

### Finding Description

`ExecutionStateReader::get_compiled_class` has two code paths depending on whether `self.class_manager_handle` is `Some`:

**Cairo 1 path (guarded):**
```rust
ContractClass::V1(casm_contract_class) => {
    let is_declared = is_contract_class_declared(
        &self.storage_reader.begin_ro_txn()...,
        &class_hash,
        self.state_number,   // ← checked against the queried block
    )?;
    if is_declared {
        Ok(RunnableCompiledClass::V1(casm_contract_class.try_into()?))
    } else {
        Err(StateError::UndeclaredClassHash(class_hash))
    }
}
```

**Cairo 0 path (unguarded — acknowledged with a TODO):**
```rust
// TODO(shahak): Verify cairo0 as well after get_class_definition_block_number is
// fixed.
ContractClass::V0(deprecated_contract_class) => {
    Ok(RunnableCompiledClass::V0(deprecated_contract_class.try_into()?))
}
``` [1](#0-0) 

The class manager client (`get_executable`) returns a class whenever it exists in the manager's store, regardless of the `state_number` the caller is querying. For Cairo 1 classes the subsequent `is_contract_class_declared` call gates the result against the correct block. For Cairo 0 classes that gate is absent.

The same asymmetry exists one layer down in `StateReaderAndContractManager::get_compiled_from_class_manager` (used by the batcher): cached Cairo 1 classes call `self.state_reader.is_declared(class_hash)?` before being returned; cached Cairo 0 classes fall through the `RunnableCompiledClass::V0(_) => {}` arm with no check. [2](#0-1) 

The comment on the Cairo 1 guard explicitly states the risk: *"it might contain a declared class from a reverted block"*. That risk is identical for Cairo 0 classes. [3](#0-2) 

---

### Impact Explanation

`ExecutionStateReader` is the state reader supplied to `exec_simulate`, `exec_estimate_fee`, and `exec_transaction_trace` inside the RPC server. [4](#0-3) 

When a caller invokes `starknet_simulateTransactions`, `starknet_estimateFee`, or `starknet_traceTransaction` at a block where a Cairo 0 class is **not** declared, but the class manager still holds that class (e.g., from a reverted block), the execution engine will:

1. Resolve the class hash to a `RunnableCompiledClass::V0` without error.
2. Execute the transaction against that class.
3. Return a simulation result, fee estimate, or trace that is **wrong** — the transaction would actually revert (or behave differently) on-chain because the class is not declared at that state.

This matches the impact category: **High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value.**

---

### Likelihood Explanation

The trigger requires a chain reorg that reverts a block containing a `declare` transaction for a Cairo 0 class, while the class manager cache is not flushed. Reorgs are a normal operational event on Starknet (especially during sequencer restarts or consensus failures). The class manager cache is a long-lived in-memory store; there is no eviction on reorg for Cairo 0 entries. An unprivileged user who knows the class hash (observable from the reverted block) can immediately reproduce the wrong result by querying any RPC simulation endpoint at a pre-declaration block number.

---

### Recommendation

Apply the same `is_contract_class_declared` guard to the Cairo 0 branch in `ExecutionStateReader::get_compiled_class`:

```rust
ContractClass::V0(deprecated_contract_class) => {
    let is_declared = is_deprecated_class_declared(
        &self.storage_reader.begin_ro_txn()...,
        &class_hash,
        self.state_number,
    )?;
    if is_declared {
        Ok(RunnableCompiledClass::V0(deprecated_contract_class.try_into()?))
    } else {
        Err(StateError::UndeclaredClassHash(class_hash))
    }
}
```

The TODO comment references `get_class_definition_block_number` being broken for Cairo 0; the correct function to use is `get_deprecated_class_definition_block_number`, which is already implemented in `StateReader` and used by `is_class_declared_at` in the state sync module. [5](#0-4) 

Similarly, `StateReaderAndContractManager::get_compiled_from_class_manager` should call `is_declared` (or an equivalent Cairo 0 check) for cached `RunnableCompiledClass::V0` entries, mirroring the existing Cairo 1 guard. [6](#0-5) 

---

### Proof of Concept

1. Declare a Cairo 0 class in block N (class hash `H`). The class manager stores it.
2. Reorg: block N is reverted. The class manager cache is **not** cleared for `H`.
3. Call `starknet_estimateFee` (or `starknet_simulateTransactions`) at `block_id = N-1` with a transaction that invokes a contract whose class hash is `H`.
4. `ExecutionStateReader::get_compiled_class(H)` is called with `state_number = right_after_block(N-1)`.
5. `class_manager_client.get_executable(H)` returns `Some(ContractClass::V0(...))`.
6. The Cairo 0 branch returns `Ok(RunnableCompiledClass::V0(...))` — **no declaration check performed**.
7. The transaction executes successfully against the undeclared class.
8. The RPC returns a fee estimate / simulation result as if the class were declared, which is incorrect — the same transaction submitted on-chain would revert with `UndeclaredClassHash`. [1](#0-0)

### Citations

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

**File:** crates/blockifier/src/state/state_reader_and_contract_manager.rs (L70-87)
```rust
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

**File:** crates/apollo_rpc_execution/src/lib.rs (L1-30)
```rust
#![warn(missing_docs)]
//! Functionality for executing Starknet transactions and contract entry points.
//!
//! In this module, we use the term "state_number" to refer to the state of the storage at the
//! execution, and "block_context_block_number" to refer to the block in which the transactions
//! should run. For example, if you want to simulate transactions at the beginning of block 10, you
//! should use state_number = 10 and block_context_block_number = 10. If you want to simulate
//! transactions at the end of block 10, you should use state_number = 11 and
//! block_context_block_number = 10.
//! See documentation of [StateNumber] for more details.
#[cfg(test)]
mod execution_test;
pub mod execution_utils;
mod state_reader;
#[cfg(test)]
mod test_utils;
#[cfg(any(feature = "testing", test))]
pub mod testing_instances;

pub mod objects;
use std::cell::Cell;
use std::collections::BTreeMap;
use std::sync::{Arc, LazyLock};

use apollo_class_manager_types::SharedClassManagerClient;
use apollo_config::dumping::{ser_param, SerializeConfig};
use apollo_config::{ParamPath, ParamPrivacyInput, SerializedParam};
use apollo_storage::header::HeaderStorageReader;
use apollo_storage::{StorageError, StorageReader};
use blockifier::blockifier::block::pre_process_block;
```

**File:** crates/apollo_state_sync/src/lib.rs (L312-333)
```rust
    async fn is_class_declared_at(
        &self,
        block_number: BlockNumber,
        class_hash: ClassHash,
    ) -> StateSyncResult<bool> {
        if self.is_cairo_1_class_declared_at(block_number, class_hash).await? {
            return Ok(true);
        }

        let storage_reader = self.storage_reader.clone();
        // TODO(noamsp): Add unit testing for cairo0
        let deprecated_class_definition_block_number_opt = storage_reader
            .begin_ro_txn()?
            .get_state_reader()?
            .get_deprecated_class_definition_block_number(&class_hash)?;

        Ok(deprecated_class_definition_block_number_opt.is_some_and(
            |deprecated_class_definition_block_number| {
                deprecated_class_definition_block_number <= block_number
            },
        ))
    }
```
