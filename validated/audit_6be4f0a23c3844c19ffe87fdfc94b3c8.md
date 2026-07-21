### Title
Inconsistent Pending-State Overlay in `get_storage_at` Contract Existence Check Returns Wrong `CONTRACT_NOT_FOUND` for Pending-Deployed Contracts - (`File: crates/apollo_rpc/src/v0_8/api/api_impl.rs`)

---

### Summary

`starknet_getStorageAt` with `block_id = "pending"` applies the pending storage-diff overlay when reading a value, but its contract-existence guard falls back to committed state only. The sibling RPC methods `starknet_getClassHashAt` and `starknet_getNonce` both correctly consult the pending state diff for their respective pending-deployed-contract checks. The inconsistency means that for any contract deployed inside the pending block, `getStorageAt(pending, addr, key)` returns `CONTRACT_NOT_FOUND` whenever the queried key is absent from the pending storage diff — even though `getClassHashAt(pending, addr)` correctly returns the class hash for the same address.

---

### Finding Description

**Root cause — `get_storage_at`, lines 347–381:**

```rust
// ✓ Pending storage diffs ARE applied
let maybe_pending_storage_diffs = if let BlockId::Tag(Tag::Pending) = block_id {
    Some(read_pending_data(...).state_update.state_diff.storage_diffs)
} else { None };

let block_number = get_accepted_block_number(&txn, block_id)?;
let state_number = StateNumber::unchecked_right_after_block(block_number);
let res = execution_utils::get_storage_at(
    &txn, state_number, maybe_pending_storage_diffs.as_ref(), contract_address, key,
)?;

// ✗ Contract existence check uses COMMITTED state only — pending deployed contracts ignored
if res == Felt::default() && contract_address != BLOCK_HASH_TABLE_ADDRESS {
    txn.get_state_reader()?
        .get_class_hash_at(state_number, &contract_address)?   // ← no pending overlay
        .ok_or_else(|| ErrorObjectOwned::from(CONTRACT_NOT_FOUND))?;
}
``` [1](#0-0) 

**Contrast with `maybe_get_class_hash_at`, lines 1649–1675:**

```rust
// ✓ Pending deployed contracts ARE consulted
let maybe_pending_deployed_contracts_and_replaced_classes =
    if let BlockId::Tag(Tag::Pending) = block_id {
        let pending_state_diff = read_pending_data(...).state_update.state_diff;
        Some((pending_state_diff.deployed_contracts, pending_state_diff.replaced_classes))
    } else { None };
...
execution_utils::get_class_hash_at(&txn, state_number,
    maybe_pending_deployed_contracts_and_replaced_classes.as_ref().map(|t| (&t.0, &t.1)),
    contract_address)
``` [2](#0-1) 

**And `get_nonce`, lines 681–705:**

```rust
// ✓ Pending nonces ARE consulted
let maybe_pending_nonces = if let BlockId::Tag(Tag::Pending) = block_id {
    Some(read_pending_data(...).state_update.state_diff.nonces)
} else { None };
...
execution_utils::get_nonce_at(&txn, state_number, maybe_pending_nonces.as_ref(), contract_address)?
    .ok_or_else(|| ErrorObjectOwned::from(CONTRACT_NOT_FOUND))
``` [3](#0-2) 

The helper `execution_utils::get_class_hash_at` correctly walks `pending_deployed_contracts` and `pending_replaced_classes` before falling back to committed state: [4](#0-3) 

The `read_pending_data` function that supplies the pending diff is the same for all three callers: [5](#0-4) 

---

### Impact Explanation

A contract deployed inside the pending block exists in `pending_state_diff.deployed_contracts` but not yet in committed storage. When a caller queries `starknet_getStorageAt(pending, addr, key)` for any key whose value is `0` (i.e., not overridden in the pending storage diff), the function:

1. Reads `0` from committed state (correct — the contract has no committed storage).
2. Enters the existence guard.
3. Calls `get_class_hash_at(state_number, addr)` against committed state only → `None`.
4. Returns `CONTRACT_NOT_FOUND`.

The correct answer is `0` (the slot is unset). The returned error is wrong and authoritative-looking. Simultaneously, `starknet_getClassHashAt(pending, addr)` returns the correct class hash for the same address, and `starknet_getNonce(pending, addr)` returns the correct nonce. Callers receive contradictory state views depending solely on which RPC method they invoke — a direct analog to the `balanceOfNFT` / `balanceOfNFTAt` inconsistency in the external report.

**Matched impact:** *High — RPC pending view returns an authoritative-looking wrong value.*

---

### Likelihood Explanation

- Trigger is fully unprivileged: any caller can issue `starknet_getStorageAt` with `block_id = "pending"`.
- The condition fires whenever a contract is deployed in the pending block and the queried storage slot is `0` (the default for any freshly deployed contract).
- Wallets, explorers, and off-chain tooling routinely poll `getStorageAt(pending, ...)` to track in-flight state; the wrong `CONTRACT_NOT_FOUND` response will be treated as authoritative.

---

### Recommendation

Replace the bare committed-state existence check in `get_storage_at` with the same pending-aware lookup used by `maybe_get_class_hash_at`. Concretely, when `block_id = Pending`, read `pending_state_diff.deployed_contracts` and `pending_state_diff.replaced_classes` alongside the committed state reader, mirroring the pattern in `execution_utils::get_class_hash_at`. The pending data is already fetched earlier in the same function for the storage-diff overlay, so the additional lookup requires no extra I/O.

---

### Proof of Concept

1. Submit a `DeployAccount` or `Deploy` transaction; it lands in the pending block (`pending_state_diff.deployed_contracts` contains `(addr, class_hash)`).
2. Call `starknet_getClassHashAt("pending", addr)` → returns `class_hash` ✓
3. Call `starknet_getNonce("pending", addr)` → returns `0x0` ✓
4. Call `starknet_getStorageAt("pending", addr, "0x0")` → returns `CONTRACT_NOT_FOUND` ✗ (expected: `0x0`)

The divergence is reproducible with any pending-deployed contract whose queried storage slot has not been explicitly written in the same pending block.

### Citations

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L347-382)
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

        // Check that the block is valid and get the state number.
        let block_number = get_accepted_block_number(&txn, block_id)?;
        let state_number = StateNumber::unchecked_right_after_block(block_number);
        let res = execution_utils::get_storage_at(
            &txn,
            state_number,
            maybe_pending_storage_diffs.as_ref(),
            contract_address,
            key,
        )
        .map_err(internal_server_error)?;

        // If the contract is not deployed, res will be 0. Checking if that's the case so that
        // we'll return an error instead.
        // Contract address 0x1 is a special address, it stores the block
        // hashes. Contracts are not deployed to this address.
        if res == Felt::default() && contract_address != BLOCK_HASH_TABLE_ADDRESS {
            // check if the contract exists
            txn.get_state_reader()
                .map_err(internal_server_error)?
                .get_class_hash_at(state_number, &contract_address)
                .map_err(internal_server_error)?
                .ok_or_else(|| ErrorObjectOwned::from(CONTRACT_NOT_FOUND))?;
        }
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L681-705)
```rust
    async fn get_nonce(
        &self,
        block_id: BlockId,
        contract_address: ContractAddress,
    ) -> RpcResult<Nonce> {
        let txn = self.storage_reader.begin_ro_txn().map_err(internal_server_error)?;

        let maybe_pending_nonces = if let BlockId::Tag(Tag::Pending) = block_id {
            Some(read_pending_data(&self.pending_data, &txn).await?.state_update.state_diff.nonces)
        } else {
            None
        };

        // Check that the block is valid and get the state number.
        let block_number = get_accepted_block_number(&txn, block_id)?;
        let state_number = StateNumber::unchecked_right_after_block(block_number);
        execution_utils::get_nonce_at(
            &txn,
            state_number,
            maybe_pending_nonces.as_ref(),
            contract_address,
        )
        .map_err(internal_server_error)?
        .ok_or_else(|| ErrorObjectOwned::from(CONTRACT_NOT_FOUND))
    }
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1553-1595)
```rust
async fn read_pending_data<Mode: TransactionKind>(
    pending_data: &Arc<RwLock<PendingData>>,
    txn: &StorageTxn<'_, Mode>,
) -> RpcResult<PendingData> {
    let latest_header = match get_latest_block_number(txn)? {
        Some(latest_block_number) => get_block_header_by_number(txn, latest_block_number)?,
        None => starknet_api::block::BlockHeader {
            // TODO(Shahak): Consider adding genesis hash to the config to support chains that have
            // different genesis hash.
            block_header_without_hash: BlockHeaderWithoutHash {
                parent_hash: BlockHash::GENESIS_PARENT_HASH,
                ..Default::default()
            },
            ..Default::default()
        },
    };
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
}
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1649-1675)
```rust
    async fn maybe_get_class_hash_at(
        &self,
        block_id: BlockId,
        contract_address: ContractAddress,
    ) -> RpcResult<Option<ClassHash>> {
        let txn = self.storage_reader.begin_ro_txn().map_err(internal_server_error)?;

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
