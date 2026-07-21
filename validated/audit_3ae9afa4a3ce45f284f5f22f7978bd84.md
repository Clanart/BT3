### Title
Stale Pending Data Fallback Silently Zeroes L2 and L1-Data Gas Prices, Returning Wrong Fee Estimates - (File: crates/apollo_rpc/src/v0_8/api/api_impl.rs)

### Summary
When the in-memory pending data is stale (its `parent_block_hash` does not match the latest committed block hash), `read_pending_data` synthesizes a fallback `DeprecatedPendingBlock`. Because `DeprecatedPendingBlock` always returns `GasPricePerToken::default()` (zero) for `l1_data_gas_price` and `l2_gas_price`, any RPC call that uses `block_id = "pending"` during this window—`starknet_estimateFee`, `starknet_simulateTransactions`, `starknet_estimateMessageFee`—executes against a block context with zero L2 and L1-data gas prices, producing systematically wrong fee estimates.

### Finding Description

`read_pending_data` checks whether the cached pending block's `parent_block_hash` equals the latest committed block's hash. If they differ (stale pending data), it constructs a synthetic fallback:

```rust
// crates/apollo_rpc/src/v0_8/api/api_impl.rs lines 1572-1593
Ok(PendingData {
    block: PendingBlockOrDeprecated::Deprecated(DeprecatedPendingBlock {
        parent_block_hash: latest_header.block_hash,
        eth_l1_gas_price: latest_header.block_header_without_hash.l1_gas_price.price_in_wei,
        strk_l1_gas_price: latest_header.block_header_without_hash.l1_gas_price.price_in_fri,
        timestamp: latest_header.block_header_without_hash.timestamp,
        sequencer_address: latest_header.block_header_without_hash.sequencer,
        starknet_version: ...,
        ..Default::default()
    }),
    ...
})
``` [1](#0-0) 

The fallback copies only `l1_gas_price` from the latest header. It does **not** copy `l1_data_gas_price` or `l2_gas_price`. Because the variant is `Deprecated`, the accessors hard-return zero:

```rust
// crates/apollo_starknet_client/src/reader/objects/pending_data.rs
pub fn l1_data_gas_price(&self) -> GasPricePerToken {
    match self {
        PendingBlockOrDeprecated::Deprecated(_) => GasPricePerToken::default(), // zero
        PendingBlockOrDeprecated::Current(block) => block.l1_data_gas_price,
    }
}
pub fn l2_gas_price(&self) -> GasPricePerToken {
    match self {
        PendingBlockOrDeprecated::Deprecated(_) => GasPricePerToken::default(), // zero
        PendingBlockOrDeprecated::Current(block) => block.l2_gas_price,
    }
}
``` [2](#0-1) 

`client_pending_data_to_execution_pending_data` then copies these zero values into `ExecutionPendingData.l1_data_gas_price` and `ExecutionPendingData.l2_gas_price`: [3](#0-2) 

This `ExecutionPendingData` is passed directly to `exec_estimate_fee` and `exec_simulate_transactions`: [4](#0-3) [5](#0-4) 

The `ExecutionStateReader` stores this pending data and uses it to build the `BlockContext` for execution: [6](#0-5) 

The `BlockContext` with zero `l2_gas_price` and `l1_data_gas_price` causes fee estimation to compute zero for those gas components.

The stale-pending window is a normal, recurring operational condition: it opens every time a new block is committed and closes only after the pending sync loop fetches fresh data. The `pending_sync.rs` loop explicitly detects this transition and stops: [7](#0-6) 

During that gap, every `starknet_estimateFee` / `starknet_simulateTransactions` / `starknet_estimateMessageFee` call with `block_id = "pending"` silently uses zero L2 and L1-data gas prices.

### Impact Explanation

For any post-v0.13 transaction that carries an L2 gas component, the fee estimate returned during the stale-pending window will have a zero L2 gas fee and zero L1 data gas fee. The estimate is authoritative-looking (no error is returned, a numeric fee is returned) but is systematically wrong. Users or tooling that rely on `starknet_estimateFee` to set resource bounds will submit transactions with insufficient fees, causing them to fail on-chain. Simulation traces (`starknet_simulateTransactions`) will also show incorrect fee breakdowns, misleading developers about actual execution costs.

### Likelihood Explanation

The stale-pending window occurs on every block transition—a high-frequency, unprivileged, and unavoidable condition. No attacker action is required; any user who calls `starknet_estimateFee` with `block_id = "pending"` in the seconds between block commitment and pending-sync refresh will receive the wrong value.

### Recommendation

Replace the `DeprecatedPendingBlock` fallback with a `PendingBlock` that copies all gas-price fields from the latest committed header, including `l1_data_gas_price` and `l2_gas_price`. The latest `BlockHeaderWithoutHash` already carries these fields; they simply need to be forwarded into the synthetic pending block instead of being silently dropped.

### Proof of Concept

1. Observe a node at block N. The pending sync is running normally.
2. Block N+1 is committed. The pending sync has not yet fetched new pending data (`pending_data.block.parent_block_hash()` still equals block N's hash, not block N+1's hash).
3. Call `starknet_estimateFee` with `block_id = "pending"` and a Declare V3 or Invoke V3 transaction that consumes L2 gas.
4. `read_pending_data` detects the mismatch and returns the `DeprecatedPendingBlock` fallback.
5. `client_pending_data_to_execution_pending_data` sets `l2_gas_price = GasPricePerToken::default()` (zero) and `l1_data_gas_price = GasPricePerToken::default()` (zero).
6. The returned fee estimate shows zero for the L2 gas component and zero for the L1 data gas component, regardless of the actual gas prices on block N+1.
7. After the pending sync updates (a few seconds later), the same call returns the correct non-zero fee.

### Citations

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1009-1016)
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

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1572-1594)
```rust
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

**File:** crates/apollo_starknet_client/src/reader/objects/pending_data.rs (L155-175)
```rust
    pub fn l1_data_gas_price(&self) -> GasPricePerToken {
        match self {
            // In older versions, data gas price was 0.
            PendingBlockOrDeprecated::Deprecated(_) => GasPricePerToken::default(),
            PendingBlockOrDeprecated::Current(block) => block.l1_data_gas_price,
        }
    }
    pub fn l2_gas_price(&self) -> GasPricePerToken {
        match self {
            // In older versions, L2 gas price was 0.
            PendingBlockOrDeprecated::Deprecated(_) => GasPricePerToken::default(),
            PendingBlockOrDeprecated::Current(block) => block.l2_gas_price,
        }
    }
    pub fn l1_da_mode(&self) -> L1DataAvailabilityMode {
        match self {
            // In older versions, all blocks were using calldata.
            PendingBlockOrDeprecated::Deprecated(_) => L1DataAvailabilityMode::Calldata,
            PendingBlockOrDeprecated::Current(block) => block.l1_da_mode,
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

**File:** crates/apollo_rpc_execution/src/state_reader.rs (L34-43)
```rust
/// A view into the state at a specific state number.
pub struct ExecutionStateReader {
    pub storage_reader: StorageReader,
    pub state_number: StateNumber,
    pub maybe_pending_data: Option<PendingData>,
    // We want to return a custom error when missing a compiled class, but we need to return
    // Blockifier's error, so we store the missing class's hash in case of error.
    pub missing_compiled_class: Cell<Option<ClassHash>>,
    pub class_manager_handle: Option<(SharedClassManagerClient, Handle)>,
}
```

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
