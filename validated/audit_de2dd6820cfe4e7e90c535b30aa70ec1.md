### Title
Torn Read Between `pending_data` and `pending_classes` Causes Inconsistent Pending State View in RPC Execution — (`crates/apollo_rpc/src/v0_8/api/api_impl.rs`)

### Summary

The RPC execution paths for `starknet_estimateFee`, `starknet_simulateTransactions`, `starknet_call`, `starknet_traceTransaction`, and `starknet_traceBlockTransactions` read `pending_data` (state diffs, nonces, storage diffs) and `pending_classes` (compiled contract classes) from **two separate `RwLock`s** in non-atomic sequence. A concurrent pending-sync update can clear `pending_classes` between the two reads, leaving the execution engine with a state diff that references classes that are no longer available in the pending view. The result is that fee estimation, simulation, and call execution return wrong authoritative results — either a spurious `UndeclaredClassHash` error when the transaction should succeed, or execution against a stale state diff — for any `block_id = "pending"` request.

### Finding Description

In `estimate_fee`, `simulate_transactions`, and related handlers, the pending state is assembled as:

```rust
// crates/apollo_rpc/src/v0_8/api/api_impl.rs  ~line 1009-1013
let maybe_pending_data = if let BlockId::Tag(Tag::Pending) = block_id {
    Some(client_pending_data_to_execution_pending_data(
        read_pending_data(&self.pending_data, &storage_txn).await?,  // read #1
        self.pending_classes.read().await.clone(),                    // read #2
    ))
} else { None };
``` [1](#0-0) 

Read #1 acquires and releases the `pending_data` `RwLock`. Read #2 acquires and releases the `pending_classes` `RwLock`. These are two independent critical sections with no atomicity guarantee between them.

Concurrently, the background pending-sync task in `sync_pending_data` executes the following sequence when a new block is discovered:

```rust
// crates/apollo_central_sync/src/pending_sync.rs  ~line 179-182
if current_pending_parent_hash != new_pending_parent_hash {
    pending_classes.write().await.clear();   // step A: clear classes
}
*pending_data.write().await = new_pending_data;  // step B: update state diff
``` [2](#0-1) 

The clear (step A) happens **before** the state-diff update (step B). This creates a window where:

- `pending_data` still holds the **old** pending block's state diff (which may declare new class `X`)
- `pending_classes` is **empty** (cleared for the new block)

If an RPC request's read #1 lands before step A, and read #2 lands after step A but before step B, the assembled `ExecutionPendingData` contains:
- `storage_diffs`, `deployed_contracts`, `declared_classes` from the old pending block (referencing class `X`)
- `classes` = empty `PendingClasses`

The `ExecutionStateReader` then consults `pending_classes` first for compiled classes:

```rust
// crates/apollo_rpc_execution/src/state_reader.rs  ~line 90-113
fn get_compiled_class(&self, class_hash: ClassHash) -> StateResult<RunnableCompiledClass> {
    if let Some(pending_classes) =
        self.maybe_pending_data.as_ref().map(|pending_data| &pending_data.classes)
    {
        if let Some(api_contract_class) = pending_classes.get_class(class_hash) {
            // ... compile and return
        }
    }
    // falls through to storage
``` [3](#0-2) 

If class `X` was only declared in the pending block (not yet committed to storage), the fallthrough to storage returns `StateError::UndeclaredClassHash(X)`, causing the execution to fail with an error that would not occur if the pending state were read atomically.

The `block_not_reverted_validator` called after execution does not detect this inconsistency — it only checks whether the base block was reverted, not whether the pending snapshot is coherent. [4](#0-3) 

The `client_pending_data_to_execution_pending_data` conversion that merges the two reads has no cross-validation between the state diff and the class map: [5](#0-4) 

### Impact Explanation

Any caller of `starknet_estimateFee`, `starknet_simulateTransactions`, `starknet_call`, `starknet_traceTransaction`, or `starknet_traceBlockTransactions` with `block_id = "pending"` can receive a wrong authoritative result during the race window:

1. **Spurious execution failure**: A transaction that deploys or calls a contract whose class is only in the pending block returns `UndeclaredClassHash` / `CONTRACT_ERROR`, telling the caller the transaction is invalid when it is not.
2. **Wrong fee estimate**: Fee estimation runs against a state diff that references classes not present in the class map, producing an incorrect (failed) fee result that the caller treats as authoritative.
3. **Wrong simulation trace**: `starknet_simulateTransactions` returns a revert trace for a transaction that would actually succeed, or omits execution steps that depend on the pending class.

This matches the allowed impact: **High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value.**

### Likelihood Explanation

The race window exists on every RPC node running a pending sync. It is triggered whenever:
- A new Starknet block is produced (which happens every ~30 seconds on mainnet), AND
- A `block_id = "pending"` RPC request arrives during the gap between `pending_classes.clear()` and `*pending_data.write() = new_pending_data`.

The window is narrow (microseconds to milliseconds) but the event (new block) is frequent and the RPC endpoint is publicly accessible. Under load, the probability of a hit is non-negligible. No special privileges are required.

### Recommendation

Read `pending_data` and `pending_classes` atomically under a single combined lock, or wrap both in a single `Arc<RwLock<(PendingData, PendingClasses)>>` that is updated and read as a unit. Alternatively, add a parent-hash consistency check after reading `pending_classes`: if `pending_classes` was cleared (empty) but `pending_data.declared_classes` is non-empty, retry the read or return an empty pending view.

The sync side should also update both atomically:

```rust
// Atomic update: hold both write locks simultaneously
let mut pd = pending_data.write().await;
let mut pc = pending_classes.write().await;
if current_pending_parent_hash != new_pending_parent_hash {
    pc.clear();
}
*pd = new_pending_data;
// both locks released together
```

### Proof of Concept

1. Node is running with pending sync enabled. Latest finalized block is N.
2. Pending block N+1 declares a new Sierra class `X` (not yet in storage) and deploys contract `A` with class `X`.
3. `pending_data` = `{declared_classes: [(X, H)], deployed_contracts: [(A, X)], ...}`.
4. `pending_classes` = `{X → CASM_A}`.
5. A new block N+1 is finalized. Sync task begins updating pending state for block N+2.
6. Sync executes `pending_classes.write().await.clear()` → `pending_classes` = `{}`.
7. **At this exact moment**, an RPC request for `starknet_estimateFee` with `block_id = "pending"` arrives:
   - Read #1: `read_pending_data` returns N+1's state diff (parent = N, still matches latest header in the storage snapshot).
   - Read #2: `pending_classes.read()` returns `{}` (already cleared).
8. Sync completes: `*pending_data.write() = new_pending_data` (N+2's state diff).
9. Execution runs with state diff declaring class `X` but empty `pending_classes`.
10. When the transaction tries to invoke contract `A` (class `X`): `get_compiled_class(X)` → not in `pending_classes` → not in storage (not yet committed) → `StateError::UndeclaredClassHash(X)`.
11. `starknet_estimateFee` returns `TRANSACTION_EXECUTION_ERROR` / `CONTRACT_ERROR` to the caller, even though the transaction would succeed against the correct pending state. [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1007-1016)
```rust
        let storage_txn = self.storage_reader.begin_ro_txn().map_err(internal_server_error)?;

        let maybe_pending_data = if let BlockId::Tag(Tag::Pending) = block_id {
            Some(client_pending_data_to_execution_pending_data(
                read_pending_data(&self.pending_data, &storage_txn).await?,
                self.pending_classes.read().await.clone(),
            ))
        } else {
            None
        };
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1050-1050)
```rust
        block_not_reverted_validator.validate(&self.storage_reader)?;
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1079-1086)
```rust
        let maybe_pending_data = if let BlockId::Tag(Tag::Pending) = block_id {
            Some(client_pending_data_to_execution_pending_data(
                read_pending_data(&self.pending_data, &storage_txn).await?,
                self.pending_classes.read().await.clone(),
            ))
        } else {
            None
        };
```

**File:** crates/apollo_central_sync/src/pending_sync.rs (L176-183)
```rust
    if is_new_pending_data_more_advanced {
        debug!("Received new pending data.");
        trace!("Pending data: {new_pending_data:#?}.");
        if current_pending_parent_hash != new_pending_parent_hash {
            pending_classes.write().await.clear();
        }
        *pending_data.write().await = new_pending_data;
        Ok(PendingSyncTaskResult::DownloadedNewPendingData)
```

**File:** crates/apollo_rpc_execution/src/state_reader.rs (L45-59)
```rust
impl BlockifierStateReader for ExecutionStateReader {
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
```

**File:** crates/apollo_rpc_execution/src/state_reader.rs (L89-113)
```rust
    fn get_compiled_class(&self, class_hash: ClassHash) -> StateResult<RunnableCompiledClass> {
        if let Some(pending_classes) =
            self.maybe_pending_data.as_ref().map(|pending_data| &pending_data.classes)
        {
            if let Some(api_contract_class) = pending_classes.get_class(class_hash) {
                match api_contract_class {
                    ApiContractClass::ContractClass(sierra) => {
                        if let Some(pending_casm) = pending_classes.get_compiled_class(class_hash) {
                            let sierra_version = sierra.get_sierra_version()?;
                            let runnable_compiled_class = RunnableCompiledClass::V1(
                                CompiledClassV1::try_from((pending_casm, sierra_version))
                                    .map_err(StateError::ProgramError)?,
                            );
                            return Ok(runnable_compiled_class);
                        }
                    }
                    ApiContractClass::DeprecatedContractClass(pending_deprecated_class) => {
                        return Ok(RunnableCompiledClass::V0(
                            CompiledClassV0::try_from(pending_deprecated_class)
                                .map_err(StateError::ProgramError)?,
                        ));
                    }
                }
            }
        }
```

**File:** crates/apollo_rpc/src/pending.rs (L5-23)
```rust
pub(crate) fn client_pending_data_to_execution_pending_data(
    client_pending_data: ClientPendingData,
    pending_classes: PendingClasses,
) -> ExecutionPendingData {
    ExecutionPendingData {
        storage_diffs: client_pending_data.state_update.state_diff.storage_diffs,
        deployed_contracts: client_pending_data.state_update.state_diff.deployed_contracts,
        declared_classes: client_pending_data.state_update.state_diff.declared_classes,
        old_declared_contracts: client_pending_data.state_update.state_diff.old_declared_contracts,
        nonces: client_pending_data.state_update.state_diff.nonces,
        replaced_classes: client_pending_data.state_update.state_diff.replaced_classes,
        classes: pending_classes,
        timestamp: client_pending_data.block.timestamp(),
        l1_gas_price: client_pending_data.block.l1_gas_price(),
        l1_data_gas_price: client_pending_data.block.l1_data_gas_price(),
        l2_gas_price: client_pending_data.block.l2_gas_price(),
        l1_da_mode: client_pending_data.block.l1_da_mode(),
        sequencer: client_pending_data.block.sequencer_address(),
    }
```
