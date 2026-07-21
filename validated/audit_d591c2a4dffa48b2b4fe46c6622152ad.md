### Title
`starknet_getStorageAt` Returns `CONTRACT_NOT_FOUND` for Pending-Deployed Contracts with Zero Storage — (File: `crates/apollo_rpc/src/v0_8/api/api_impl.rs`)

---

### Summary

The `get_storage_at` RPC handler guards against returning `0` for a non-existent contract by checking whether the contract is deployed — but it checks only the **committed** state, not the **pending** state. When a contract is deployed in the pending block and a caller queries any storage key whose value has not been explicitly written in the pending diff (i.e., the default value `0`), the handler returns `CONTRACT_NOT_FOUND` instead of `0`, producing an authoritative-looking wrong value for a contract that legitimately exists in the pending view.

---

### Finding Description

In `get_storage_at` the handler first collects the pending storage-diff overlay, resolves the value, and then applies a contract-existence guard: [1](#0-0) 

```rust
let maybe_pending_storage_diffs = if let BlockId::Tag(Tag::Pending) = block_id {
    Some(
        read_pending_data(&self.pending_data, &txn)
            .await?
            .state_update
            .state_diff
            .storage_diffs,   // ← only storage diffs, NOT deployed_contracts
    )
} else { None };
```

`execution_utils::get_storage_at` applies the overlay only for keys that appear in the pending diff; for any absent key it falls through to committed state and returns `0` (the default): [2](#0-1) 

The handler then applies the existence guard using `state_number` derived from the **latest committed block**: [3](#0-2) 

```rust
if res == Felt::default() && contract_address != BLOCK_HASH_TABLE_ADDRESS {
    txn.get_state_reader()
        .get_class_hash_at(state_number, &contract_address)   // committed state only
        .ok_or_else(|| ErrorObjectOwned::from(CONTRACT_NOT_FOUND))?;
}
```

`state_number` is `StateNumber::unchecked_right_after_block(block_number)` where `block_number` is the latest **committed** block. A contract deployed only in the pending block is invisible to this query, so `get_class_hash_at` returns `None` and the handler returns `CONTRACT_NOT_FOUND`.

The correct pattern already exists in `maybe_get_class_hash_at`, which reads `pending_state_diff.deployed_contracts` and `pending_state_diff.replaced_classes` before falling back to committed state: [4](#0-3) 

`get_storage_at` does not perform this pending-aware check.

The existing test suite only exercises `get_storage_at` for contracts that are **already committed**; it never tests a contract that is deployed exclusively in the pending block: [5](#0-4) 

---

### Impact Explanation

Every freshly deployed contract has all storage values at `0` by default. Any caller invoking `starknet_getStorageAt` with `block_id = "pending"` for a contract deployed in the pending block receives `CONTRACT_NOT_FOUND` instead of `0`. This is an authoritative-looking wrong value: the contract exists in the pending view, but the RPC asserts it does not. Clients relying on this endpoint for fee estimation, simulation, or state queries receive incorrect results, matching the **High** impact category: *"RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value."*

---

### Likelihood Explanation

No privileged access or adversarial setup is required. The trigger is normal usage: deploy a contract (via `deploy_account` or `deploy`) and immediately query any storage key before the block is committed. Because all storage keys of a newly deployed contract default to `0`, every such query hits the bug. The scenario is common in any application that reads contract state immediately after deployment.

---

### Recommendation

In `get_storage_at`, when `block_id = Tag::Pending` and `res == Felt::default()`, also check whether the contract address appears in `pending_state_diff.deployed_contracts` or `pending_state_diff.replaced_classes` before returning `CONTRACT_NOT_FOUND`. This mirrors the logic already present in `maybe_get_class_hash_at` and `execution_utils::get_class_hash_at`. Concretely, replace the raw `get_class_hash_at(state_number, …)` call with a call to `maybe_get_class_hash_at(block_id, contract_address)` (or an equivalent inline check that includes the pending deployed-contracts list).

---

### Proof of Concept

1. Submit a `deploy_account` or `deploy` transaction; it lands in the pending block but is not yet committed.
2. Call `starknet_getStorageAt(contract_address, any_key, "pending")`.
3. `execution_utils::get_storage_at` finds no entry for `any_key` in the pending storage diff and falls through to committed state, returning `0` (contract absent from committed state).
4. The existence guard queries `get_class_hash_at(state_number, contract_address)` against committed state → returns `None`.
5. The handler returns `CONTRACT_NOT_FOUND` instead of `0`.

The correct return value is `0` (the default storage value for a deployed contract with no explicit writes), matching what `maybe_get_class_hash_at` would confirm is a live pending contract.

### Citations

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L347-357)
```rust
        let maybe_pending_storage_diffs = if let BlockId::Tag(Tag::Pending) = block_id {
            Some(
                read_pending_data(&self.pending_data, &txn)
                    .await?
                    .state_update
                    .state_diff
                    .storage_diffs,
            )
        } else {
            None
        };
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L375-382)
```rust
        if res == Felt::default() && contract_address != BLOCK_HASH_TABLE_ADDRESS {
            // check if the contract exists
            txn.get_state_reader()
                .map_err(internal_server_error)?
                .get_class_hash_at(state_number, &contract_address)
                .map_err(internal_server_error)?
                .ok_or_else(|| ErrorObjectOwned::from(CONTRACT_NOT_FOUND))?;
        }
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1656-1675)
```rust
        let maybe_pending_deployed_contracts_and_replaced_classes =
            if let BlockId::Tag(Tag::Pending) = block_id {
                let pending_state_diff =
                    read_pending_data(&self.pending_data, &txn).await?.state_update.state_diff;
                Some((pending_state_diff.deployed_contracts, pending_state_diff.replaced_classes))
            } else {
                None
            };

        let block_number = get_accepted_block_number(&txn, block_id)?;
        let state_number = StateNumber::unchecked_right_after_block(block_number);
        execution_utils::get_class_hash_at(
            &txn,
            state_number,
            // This map converts &(T, S) to (&T, &S).
            maybe_pending_deployed_contracts_and_replaced_classes.as_ref().map(|t| (&t.0, &t.1)),
            contract_address,
        )
        .map_err(internal_server_error)
    }
```

**File:** crates/apollo_rpc_execution/src/execution_utils.rs (L158-168)
```rust
    if let Some(pending_storage_diffs) = pending_storage_diffs {
        if let Some(storage_entries) = pending_storage_diffs.get(&contract_address) {
            if let Some(StorageEntry { key: _, value }) = storage_entries
                .iter()
                .find(|StorageEntry { key: other_key, value: _ }| key == *other_key)
            {
                return Ok(*value);
            }
        }
    }
    txn.get_state_reader()?.get_storage_at(state_number, &contract_address, &key)
```

**File:** crates/apollo_rpc/src/v0_8/api/test.rs (L2060-2065)
```rust
    // Ask for storage in pending block when contract's storage wasn't changed in pending block.
    let res = module
        .call::<_, Felt>(method_name, (*address, key, BlockId::Tag(Tag::Pending)))
        .await
        .unwrap();
    assert_eq!(res, *expected_value);
```
