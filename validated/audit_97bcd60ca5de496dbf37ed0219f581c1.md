The bug is real and reachable. Here is the full trace.

---

### Title
Pending-deployed contract returns `CONTRACT_NOT_FOUND` instead of storage value via `starknet_getStorageAt` — (`crates/apollo_rpc/src/v0_8/api/api_impl.rs`)

### Summary

The `get_storage_at` RPC handler reads pending `storage_diffs` for the pending block tag but performs the contract-existence fallback check exclusively against committed state, ignoring pending `deployed_contracts`. A contract deployed only in the pending block with a zero (or absent) storage value at the queried key will always trigger `CONTRACT_NOT_FOUND` instead of returning `0x0`.

### Finding Description

In the `get_storage_at` handler, pending data is partially consumed: only `storage_diffs` is extracted from the pending state diff. [1](#0-0) 

`execution_utils::get_storage_at` checks those pending diffs first; if the key is absent (or its pending value is `Felt::default()`), it falls through to committed storage and returns `Felt::default()`. [2](#0-1) 

The handler then checks whether `res == Felt::default()` and, if so, verifies contract existence — but calls `txn.get_state_reader().get_class_hash_at(state_number, &contract_address)` directly against committed state only: [3](#0-2) 

The codebase already has `execution_utils::get_class_hash_at`, which correctly overlays pending `deployed_contracts` and `replaced_classes` on top of committed state: [4](#0-3) 

That function is **not called** in the existence check. The raw `get_class_hash_at` on the committed-state reader is called instead, so a pending-only deployment is invisible to it.

### Impact Explanation

Any unprivileged RPC client can trigger this by:
1. Observing (or causing) a contract to be deployed only in the pending block.
2. Calling `starknet_getStorageAt(address=A, key=K, block_id=Pending)` where key K has value `0` in pending state (the common initial case for any freshly deployed contract).

The RPC returns `CONTRACT_NOT_FOUND` (an authoritative-looking error) instead of `0x0`. This falls under **High — RPC pending view returns an authoritative-looking wrong value**.

### Likelihood Explanation

Every freshly deployed contract has all-zero storage by default. Any caller querying storage on a pending-deployed contract before it writes a non-zero value to that key will hit this path deterministically. No special privileges are required.

### Recommendation

Replace the raw committed-state class-hash lookup with `execution_utils::get_class_hash_at`, passing the pending `deployed_contracts` and `replaced_classes` extracted from the pending state diff alongside the existing `storage_diffs`. The pending state diff already contains this data; it just needs to be threaded through to the existence check.

### Proof of Concept

```
Precondition:
  - Pending block state diff contains:
      deployed_contracts: [{address: A, class_hash: C}]
      storage_diffs: {}   // no storage written yet

RPC call:
  starknet_getStorageAt(contract_address=A, key=K, block_id=Pending)

Trace:
  1. maybe_pending_storage_diffs = Some({})   // A not present
  2. execution_utils::get_storage_at → pending lookup misses → committed storage returns Felt::default()
  3. res == Felt::default() → existence check triggered
  4. txn.get_state_reader().get_class_hash_at(state_number, A)
       → committed state has no entry for A → returns None
  5. .ok_or_else(|| CONTRACT_NOT_FOUND) → error returned

Expected: Ok(Felt::default())  // 0x0, valid storage of a pending-deployed contract
Actual:   Err(CONTRACT_NOT_FOUND)
```

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

**File:** crates/apollo_rpc_execution/src/execution_utils.rs (L190-215)
```rust
pub fn get_class_hash_at<Mode: TransactionKind>(
    txn: &StorageTxn<'_, Mode>,
    state_number: StateNumber,
    pending_deployed_contracts_and_replaced_classes: Option<(
        &Vec<DeployedContract>,
        &Vec<ReplacedClass>,
    )>,
    contract_address: ContractAddress,
) -> StorageResult<Option<ClassHash>> {
    if let Some((pending_deployed_contracts, pending_replaced_classes)) =
        pending_deployed_contracts_and_replaced_classes
    {
        // Searching first in the replaced classes because if the contract was deployed and
        // replaced, the replaced class is the contract's class.
        for ReplacedClass { address, class_hash } in pending_replaced_classes {
            if *address == contract_address {
                return Ok(Some(*class_hash));
            }
        }
        for DeployedContract { address, class_hash } in pending_deployed_contracts {
            if *address == contract_address {
                return Ok(Some(*class_hash));
            }
        }
    }
    txn.get_state_reader()?.get_class_hash_at(state_number, &contract_address)
```
