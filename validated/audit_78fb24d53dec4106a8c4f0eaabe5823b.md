### Title
Inconsistent Pending-Block Staleness Check: `get_class` Bypasses the `read_pending_data` Guard Applied by All Other Pending-Aware RPC Methods - (File: `crates/apollo_rpc/src/v0_8/api/api_impl.rs`)

### Summary

Every pending-aware RPC handler in `JsonRpcServerImpl` routes through `read_pending_data`, which immediately rejects stale pending data by comparing `pending_data.block.parent_block_hash()` against the latest accepted block hash. The single exception is `get_class`: when `block_id = Tag::Pending` it reads directly from `self.pending_classes` without any staleness guard. During the window between a new block being accepted and the asynchronous pending-sync loop clearing `pending_classes`, `starknet_getClass(pending, C)` returns a class from the now-invalid pending block while every other pending-state query correctly falls back to the accepted state.

### Finding Description

`read_pending_data` (lines 1553–1595) is the single authoritative staleness gate for pending data:

```rust
if pending_data.block.parent_block_hash() == latest_header.block_hash {
    Ok((*pending_data).clone())
} else {
    Ok(PendingData { /* synthetic empty block anchored to latest */ })
}
``` [1](#0-0) 

Every pending-aware handler calls this gate:

| Handler | Staleness gate |
|---|---|
| `get_storage_at` | `read_pending_data` (line 349) |
| `get_nonce` | `read_pending_data` (line 689) |
| `maybe_get_class_hash_at` | `read_pending_data` (line 1659) |
| `get_state_update` | `read_pending_data` (line 488) |
| `get_block_transaction_count` | `read_pending_data` (line 473) |
| `get_transaction_by_hash` | `read_pending_data` (line 407) |
| `get_transaction_receipt` | `read_pending_data` (line 576) |
| `trace_transaction` | `read_pending_data` (line 1149) |
| `trace_block_transactions` | `read_pending_data` (line 1304) | [2](#0-1) [3](#0-2) 

`get_class` is the sole exception. When `block_id = Tag::Pending` it reads `self.pending_classes` directly and returns immediately if the class is found, **never calling `read_pending_data`**:

```rust
let block_id = if let BlockId::Tag(Tag::Pending) = block_id {
    let maybe_class = &self.pending_classes.read().await.get_class(class_hash);
    if let Some(class) = maybe_class {
        return class.clone().try_into().map_err(internal_server_error);
    } else {
        BlockId::Tag(Tag::Latest)
    }
} else { block_id };
``` [4](#0-3) 

`pending_classes` is populated and cleared asynchronously by the central-sync pending loop. Clearing only happens when the loop detects a parent-hash change:

```rust
if current_pending_parent_hash != new_pending_parent_hash {
    pending_classes.write().await.clear();
}
``` [5](#0-4) 

There is therefore a race window between the moment a new block is accepted (making `pending_data.block.parent_block_hash()` stale) and the moment the pending-sync loop runs and clears `pending_classes`. During this window `read_pending_data` correctly returns an empty synthetic pending block, but `pending_classes` still holds class definitions from the now-invalid pending block.

### Impact Explanation

During the race window an attacker (or any client) can call `starknet_getClass(pending, C)` and receive the full Sierra/CASM definition of a class `C` that was declared only in the stale pending block. Simultaneously, `starknet_getClassHashAt(pending, addr)` and `starknet_getNonce(pending, addr)` correctly return values as if no pending data exists. The node therefore presents an internally inconsistent view of the pending state: class definitions from an invalid pending block are served alongside accepted-state nonces and storage values. Any downstream system (fee estimator, simulation engine, SDK) that trusts the pending class definition to construct or validate a transaction will operate on wrong data.

This matches the allowed impact: **High — RPC pending view returns an authoritative-looking wrong value.**

### Likelihood Explanation

The window exists on every block transition and lasts until the pending-sync polling interval elapses (configurable, typically seconds). No special privileges are required; any unprivileged RPC caller can trigger the inconsistency by querying `starknet_getClass` with `block_id = "pending"` immediately after a new block is accepted. Likelihood is **Medium**.

### Recommendation

Apply the same staleness guard to `get_class` that all other pending-aware handlers use. Before reading from `self.pending_classes`, call `read_pending_data` and check whether the result is a live or synthetic pending block. If the pending block is stale (synthetic), skip the `pending_classes` lookup and fall through to the accepted-state path, consistent with the behavior of `get_storage_at`, `get_nonce`, and `get_class_hash_at`.

Alternatively, centralise the staleness check so that `pending_classes` and `pending_data` are always cleared atomically when a new block is accepted, eliminating the race window entirely.

### Proof of Concept

1. Node has accepted block N with hash `H_N`. Pending block P is built on top of N; class `C` is declared in P. `pending_classes` contains `C`; `pending_data.block.parent_block_hash() == H_N`.
2. Block N+1 is accepted with hash `H_{N+1}`. Storage is updated; `get_latest_block_number` now returns N+1.
3. `read_pending_data` immediately detects staleness: `H_N ≠ H_{N+1}` → returns synthetic empty pending block.
4. The pending-sync loop has **not yet run**; `pending_classes` still contains `C`.
5. Client calls `starknet_getClass("pending", C)`:
   - `get_class` checks `self.pending_classes.get_class(C)` → finds `C` → **returns C's definition** (wrong: C is from an invalid pending block).
6. Client calls `starknet_getClassHashAt("pending", addr_of_C)`:
   - `maybe_get_class_hash_at` calls `read_pending_data` → synthetic empty diff → class hash not found → returns `CONTRACT_NOT_FOUND`.
7. The two responses are inconsistent: the class definition exists in the pending view but the deploying contract does not. [6](#0-5) [7](#0-6) [5](#0-4)

### Citations

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L340-384)
```rust
    async fn get_storage_at(
        &self,
        contract_address: ContractAddress,
        key: StorageKey,
        block_id: BlockId,
    ) -> RpcResult<Felt> {
        let txn = self.storage_reader.begin_ro_txn().map_err(internal_server_error)?;
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
        Ok(res)
    }
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L596-657)
```rust
    #[instrument(skip(self), level = "debug", err, ret)]
    async fn get_class(
        &self,
        block_id: BlockId,
        class_hash: ClassHash,
    ) -> RpcResult<GatewayContractClass> {
        // Check in pending classes.
        let block_id = if let BlockId::Tag(Tag::Pending) = block_id {
            let maybe_class = &self.pending_classes.read().await.get_class(class_hash);
            if let Some(class) = maybe_class {
                return class.clone().try_into().map_err(internal_server_error);
            } else {
                BlockId::Tag(Tag::Latest)
            }
        } else {
            block_id
        };

        let txn = self.storage_reader.begin_ro_txn().map_err(internal_server_error)?;

        let block_number = get_accepted_block_number(&txn, block_id)?;
        let state_number = StateNumber::unchecked_right_after_block(block_number);
        let state_reader = txn.get_state_reader().map_err(internal_server_error)?;

        // If class manager supplied, first check with it.
        if let Some(class_manager_client) = &self.class_manager_client {
            let optional_sierra_contract_class = class_manager_client
                .get_sierra(class_hash)
                .await
                .map_err(internal_server_error_with_msg)?;

            if let Some(sierra_contract_class) = optional_sierra_contract_class {
                let optional_class_definition_block_number = state_reader
                    .get_class_definition_block_number(&class_hash)
                    .map_err(internal_server_error)?;

                // Check if this class exists in the Cairo1 classes table.
                if optional_class_definition_block_number.is_some()
                    && optional_class_definition_block_number <= Some(block_number)
                {
                    return Ok(GatewayContractClass::Sierra(sierra_contract_class.into()));
                } else {
                    return Err(ErrorObjectOwned::from(CLASS_HASH_NOT_FOUND));
                }
            }
        }

        // The class might be a deprecated class. Search it first in the declared classes and if not
        // found, search in the deprecated classes.
        if let Some(class) = state_reader
            .get_class_definition_at(state_number, &class_hash)
            .map_err(internal_server_error)?
        {
            Ok(GatewayContractClass::Sierra(class.into()))
        } else {
            let class = state_reader
                .get_deprecated_class_definition_at(state_number, &class_hash)
                .map_err(internal_server_error)?
                .ok_or_else(|| ErrorObjectOwned::from(CLASS_HASH_NOT_FOUND))?;
            Ok(GatewayContractClass::Cairo0(class.try_into().map_err(internal_server_error)?))
        }
    }
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L680-705)
```rust
    #[instrument(skip(self), level = "debug", err, ret)]
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

**File:** crates/apollo_central_sync/src/pending_sync.rs (L174-183)
```rust
    let is_new_pending_data_more_advanced = current_pending_parent_hash != new_pending_parent_hash
        || new_pending_data.block.transactions().len() > current_pending_num_transactions;
    if is_new_pending_data_more_advanced {
        debug!("Received new pending data.");
        trace!("Pending data: {new_pending_data:#?}.");
        if current_pending_parent_hash != new_pending_parent_hash {
            pending_classes.write().await.clear();
        }
        *pending_data.write().await = new_pending_data;
        Ok(PendingSyncTaskResult::DownloadedNewPendingData)
```
