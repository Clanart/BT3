## Title
Stale `pending_classes` Leaked Into Pending Execution When `pending_data` Has Wrong Parent Hash — (`crates/apollo_rpc/src/v0_8/api/api_impl.rs`, `crates/apollo_rpc/src/pending.rs`)

---

### Summary

When a new block is committed to storage, there is a window during which `pending_data` holds a stale pending block (wrong `parent_block_hash`) while `pending_classes` still contains class entries from that prior pending block. `read_pending_data` detects the mismatch and returns a synthetic fallback with an empty `state_diff`, but it does **not** clear `pending_classes`. The RPC execution path (`starknet_call`, `starknet_estimateFee`, `starknet_simulateTransactions`) reads `pending_classes` independently and passes it unchanged into `ExecutionPendingData.classes`. `ExecutionStateReader::get_compiled_class` then returns a `RunnableCompiledClass` from the stale cache for a class hash that is absent from the synthetic fallback's empty `declared_classes`, violating the invariant that pending execution must only use classes declared in the current pending block.

---

### Finding Description

**Step 1 — Normal operation populates `pending_classes`.**

`sync_pending_data` / `get_pending_data` in `pending_sync.rs` downloads a pending block whose `parent_block_hash` matches the latest committed block hash. It populates `pending_classes` with class entries for that pending block. [1](#0-0) 

**Step 2 — A new block is committed; `sync_pending_data` stops without clearing `pending_classes`.**

When `get_pending_data` detects that `new_pending_parent_hash != latest_block_hash`, it returns `PendingSyncFinished` immediately. It does **not** clear `pending_classes` and does **not** update `pending_data`. Both remain in their prior state. [2](#0-1) 

The clear only happens on the *next* call to `get_pending_data` when it detects the parent hash has changed: [3](#0-2) 

**Step 3 — RPC call arrives during the window.**

An unprivileged user calls `starknet_call` (or `starknet_estimateFee`) with `BlockId::Pending`. The handler reads `pending_data` and `pending_classes` in two **separate, non-atomic** lock acquisitions: [4](#0-3) 

**Step 4 — `read_pending_data` returns a synthetic fallback with empty `state_diff`.**

Because `pending_data.block.parent_block_hash()` no longer matches `latest_header.block_hash`, `read_pending_data` returns a synthetic `PendingData` with `state_diff: Default::default()` (no declared classes, no deployed contracts, no storage diffs). [5](#0-4) 

**Step 5 — Stale `pending_classes` is merged with the empty synthetic fallback.**

`client_pending_data_to_execution_pending_data` blindly assigns `pending_classes` (the stale snapshot) as `ExecutionPendingData.classes`, regardless of whether those classes are declared in the synthetic fallback's empty `state_diff`: [6](#0-5) 

**Step 6 — `ExecutionStateReader::get_compiled_class` returns the stale class.**

The execution state reader checks `pending_data.classes` first, with no cross-check against `declared_classes`. If the queried `class_hash` is in the stale `pending_classes`, it returns the stale `RunnableCompiledClass` immediately, bypassing storage: [7](#0-6) 

---

### Impact Explanation

`starknet_call` or `starknet_estimateFee` with `BlockId::Pending` returns an execution result computed using a contract class from a **prior pending block** that was never committed to the chain and is not part of the current pending state. The result is authoritative-looking (no error is returned) but is wrong: it reflects a class that does not exist in the current pending state diff. This falls squarely within the allowed High impact: *"RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value."*

---

### Likelihood Explanation

The window exists between the moment a new block is committed to storage and the moment `sync_pending_data` restarts and calls `get_pending_data` again to clear `pending_classes`. This window spans the full block-sync cycle (downloading headers, state diffs, classes for the new block, then restarting pending sync). On a live node this can be seconds to tens of seconds. Any unprivileged caller who issues a pending execution call during this window will receive the wrong result. No special privileges or knowledge of internal state are required beyond knowing a class hash that was in the prior pending block.

---

### Recommendation

The fix must ensure that when `read_pending_data` detects a parent hash mismatch and returns a synthetic fallback, the `pending_classes` passed to `client_pending_data_to_execution_pending_data` is also empty (or a freshly-constructed default). The simplest approach is:

```rust
// In call(), estimate_fee(), simulate_transactions(), estimate_message_fee():
let (client_pending, effective_classes) = {
    let pd = read_pending_data(&self.pending_data, &txn).await?;
    let classes = if pd.block.parent_block_hash() == /* latest hash */ {
        self.pending_classes.read().await.clone()
    } else {
        PendingClasses::default()  // synthetic fallback → no stale classes
    };
    (pd, classes)
};
let maybe_pending_data = Some(client_pending_data_to_execution_pending_data(client_pending, effective_classes));
```

Alternatively, `read_pending_data` could return a typed enum distinguishing "real pending" from "synthetic fallback", and callers could use an empty `PendingClasses` for the synthetic case.

A deeper fix is to clear `pending_classes` atomically with `pending_data` when `sync_pending_data` detects a new block (i.e., also clear on `PendingSyncFinished`), but this requires holding both locks together.

---

### Proof of Concept

```rust
#[tokio::test]
async fn stale_pending_classes_leak_on_wrong_parent_hash() {
    use papyrus_common::pending_classes::{ApiContractClass, PendingClasses, PendingClassesTrait};
    use starknet_api::core::ClassHash;
    use starknet_types_core::felt::Felt;

    // 1. Set up storage with one committed block (hash = 0x1).
    let (module, mut storage_writer) =
        get_test_rpc_server_and_storage_writer_from_params::<JsonRpcServerImpl>(
            None, None,
            Some(get_test_pending_data()),
            Some(get_test_pending_classes()),
            None,
        );
    let header = BlockHeader {
        block_hash: BlockHash(felt!("0x1")),
        ..Default::default()
    };
    storage_writer.begin_rw_txn().unwrap()
        .append_header(BlockNumber(0), &header).unwrap()
        .append_state_diff(BlockNumber(0), ThinStateDiff::default()).unwrap()
        .commit().unwrap();

    // 2. Populate pending_classes with a stale class (from prior pending block).
    let stale_class_hash = ClassHash(Felt::from(0xdeadu64));
    let stale_class = ApiContractClass::DeprecatedContractClass(
        DeprecatedContractClass::get_test_instance(&mut get_rng())
    );
    get_test_pending_classes().write().await.add_class(stale_class_hash, stale_class);

    // 3. Set pending_data with WRONG parent hash (not 0x1).
    *get_test_pending_data().write().await.block.parent_block_hash_mutable() =
        BlockHash(felt!("0xdead"));

    // 4. Call starknet_call on a contract that uses stale_class_hash.
    //    Assert: execution must NOT use the stale class.
    //    (In the buggy version, get_compiled_class returns the stale class.)
    let result = module.call::<_, Vec<Felt>>(
        "starknet_V0_8_call",
        (CallRequest {
            contract_address: some_contract_using(stale_class_hash),
            entry_point_selector: selector_from_name("some_fn"),
            calldata: calldata![],
        }, BlockId::Tag(Tag::Pending)),
    ).await;

    // Should fail with UndeclaredClassHash, not succeed with stale class execution.
    assert!(result.is_err(), "Expected error: stale class must not be used");
}
```

### Citations

**File:** crates/apollo_central_sync/src/pending_sync.rs (L158-165)
```rust
    let new_pending_parent_hash =
        new_pending_data.block.block_hash().unwrap_or(new_pending_data.block.parent_block_hash());
    if new_pending_parent_hash != latest_block_hash {
        // TODO(shahak): If block_hash is present, consider writing the pending data here so that
        // the pending data will be available until the node syncs on the new block.
        debug!("A new block was found. Stopping pending sync.");
        return Ok(PendingSyncTaskResult::PendingSyncFinished);
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

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L894-899)
```rust
        let maybe_pending_data = if let BlockId::Tag(Tag::Pending) = block_id {
            Some(client_pending_data_to_execution_pending_data(
                read_pending_data(&self.pending_data, &txn).await?,
                self.pending_classes.read().await.clone(),
            ))
        } else {
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1569-1594)
```rust
    let pending_data = &pending_data.read().await;
    if pending_data.block.parent_block_hash() == latest_header.block_hash {
        Ok((*pending_data).clone())
    } else {
        Ok(PendingData {
            block: PendingBlockOrDeprecated::Deprecated(DeprecatedPendingBlock {
                parent_block_hash: latest_header.block_hash,
                eth_l1_gas_price: latest_header.block_header_without_hash.l1_gas_price.price_in_wei,
                strk_l1_gas_price: latest_header
                    .block_header_without_hash
                    .l1_gas_price
                    .price_in_fri,
                timestamp: latest_header.block_header_without_hash.timestamp,
                sequencer_address: latest_header.block_header_without_hash.sequencer,
                starknet_version: latest_header
                    .block_header_without_hash
                    .starknet_version
                    .to_string(),
                ..Default::default()
            }),
            state_update: ClientPendingStateUpdate {
                old_root: latest_header.block_header_without_hash.state_root,
                state_diff: Default::default(),
            },
        })
    }
```

**File:** crates/apollo_rpc/src/pending.rs (L5-24)
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
