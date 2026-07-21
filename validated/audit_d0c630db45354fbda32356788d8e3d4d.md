### Title
Stale Block Context (Timestamp, Gas Prices, DA Mode) Injected into `starknet_estimateFee` / `starknet_simulateTransactions` / `starknet_call` When Pending Data Is Out-of-Date — (`crates/apollo_rpc/src/v0_8/api/api_impl.rs`)

---

### Summary

When the in-memory `PendingData` cache is stale (its `parent_block_hash` does not match the latest committed block hash), `read_pending_data` synthesises a fallback `DeprecatedPendingBlock` that copies the **latest committed block's** `timestamp`, `l1_gas_price`, `sequencer_address`, and `starknet_version` — but **omits** `l1_data_gas_price`, `l2_gas_price`, and `l1_da_mode` (they default to zero / `Calldata`). This fallback is then passed verbatim into `create_block_context`, which uses it to build the `BlockContext` for `starknet_estimateFee`, `starknet_simulateTransactions`, `starknet_call`, and `starknet_traceTransaction`. The resulting fee estimates, execution traces, and call results are computed under a wrong block context and returned to callers as authoritative values.

---

### Finding Description

**Root cause — `read_pending_data` fallback construction:**

```rust
// crates/apollo_rpc/src/v0_8/api/api_impl.rs  L1572-1594
Ok(PendingData {
    block: PendingBlockOrDeprecated::Deprecated(DeprecatedPendingBlock {
        parent_block_hash: latest_header.block_hash,
        eth_l1_gas_price: latest_header.block_header_without_hash.l1_gas_price.price_in_wei,
        strk_l1_gas_price: latest_header.block_header_without_hash.l1_gas_price.price_in_fri,
        timestamp: latest_header.block_header_without_hash.timestamp,   // ← PAST block's timestamp
        sequencer_address: latest_header.block_header_without_hash.sequencer,
        starknet_version: ...,
        ..Default::default()   // ← l1_data_gas_price = 0, l2_gas_price = 0
    }),
    ...
})
``` [1](#0-0) 

The `DeprecatedPendingBlock` variant exposes `l1_data_gas_price` and `l2_gas_price` only through `..Default::default()`, which sets them to zero. The `l1_da_mode` defaults to `Calldata` for the `Deprecated` variant:

```rust
// crates/apollo_starknet_client/src/reader/objects/pending_data.rs  L169-175
pub fn l1_da_mode(&self) -> L1DataAvailabilityMode {
    match self {
        PendingBlockOrDeprecated::Deprecated(_) => L1DataAvailabilityMode::Calldata,
        ...
    }
}
``` [2](#0-1) 

**Propagation into block context:**

`create_block_context` in `crates/apollo_rpc_execution/src/lib.rs` directly consumes the pending data's `timestamp`, `l1_gas_price`, `l1_data_gas_price`, `l2_gas_price`, `sequencer`, and `l1_da_mode` to build the `BlockInfo` used for execution:

```rust
// crates/apollo_rpc_execution/src/lib.rs  L341-349
Some(pending_data) => (
    block_context_number.unchecked_next(),
    pending_data.timestamp,
    pending_data.l1_gas_price,
    pending_data.l1_data_gas_price,   // ← 0 from fallback
    pending_data.l2_gas_price,        // ← 0 from fallback
    pending_data.sequencer,
    pending_data.l1_da_mode,          // ← Calldata from fallback
),
``` [3](#0-2) 

**Affected RPC entry points:**

- `estimate_fee` (L997–L1064) passes `maybe_pending_data` from `read_pending_data` directly to `exec_estimate_fee` → `execute_transactions` → `create_block_context`.
- `simulate_transactions` (L1066–L1139) follows the same path.
- `estimate_message_fee` (L1429–L1505) follows the same path.
- `trace_transaction` (L1100–L1294) follows the same path. [4](#0-3) [5](#0-4) 

**Analogy to the seed bug:** Just as `NormalStrategyLib.transform()` always set `timeRemainingSeconds` to the full duration instead of the actual remaining time, `read_pending_data` always sets `l1_data_gas_price` and `l2_gas_price` to zero and `l1_da_mode` to `Calldata` in the fallback path, regardless of what the actual pending block's values should be.

---

### Impact Explanation

**Impact: High — RPC execution, fee estimation, tracing, simulation returns an authoritative-looking wrong value.**

When the pending data cache is stale (a normal operational condition that occurs whenever a new block is committed but the pending sync has not yet updated), any call to `starknet_estimateFee`, `starknet_simulateTransactions`, `starknet_call`, or `starknet_traceTransaction` with `block_id = "pending"` will:

1. Receive `l1_data_gas_price = 0` and `l2_gas_price = 0` in the block context.
2. Receive `l1_da_mode = Calldata` even if the network uses Blob DA.
3. Receive the **previous block's** `timestamp` instead of the current pending block's timestamp.

The fee estimate returned is computed under these wrong gas prices. For Blob DA networks, the DA cost component is entirely zeroed out, causing the returned `l1_data_gas_consumed` fee component to be wrong. Contracts that read `get_block_timestamp` or `get_execution_info` during simulation will observe the stale timestamp. These values are returned as authoritative RPC responses.

---

### Likelihood Explanation

The stale-pending condition is **routine**: it occurs every time a new block is committed and before the pending sync loop fetches fresh pending data. The window can be seconds to minutes depending on block time and sync latency. Any integrator polling `starknet_estimateFee` with `block_id = "pending"` during this window receives wrong values. The condition is unprivileged — no special access is required; any RPC caller can trigger it.

---

### Recommendation

In `read_pending_data`, when constructing the fallback `PendingData`, use the `PendingBlockOrDeprecated::Current` variant (or a dedicated struct) that correctly copies `l1_data_gas_price`, `l2_gas_price`, and `l1_da_mode` from the latest committed block header rather than defaulting them to zero/`Calldata`. Specifically:

```rust
// Replace the Deprecated fallback with a Current block that carries correct prices:
block: PendingBlockOrDeprecated::Current(PendingBlock {
    parent_block_hash: latest_header.block_hash,
    l1_gas_price: latest_header.block_header_without_hash.l1_gas_price,
    l1_data_gas_price: latest_header.block_header_without_hash.l1_data_gas_price,
    l2_gas_price: latest_header.block_header_without_hash.l2_gas_price,
    l1_da_mode: latest_header.block_header_without_hash.l1_da_mode,
    timestamp: latest_header.block_header_without_hash.timestamp,
    sequencer_address: latest_header.block_header_without_hash.sequencer,
    starknet_version: ...,
    ..Default::default()
}),
``` [6](#0-5) 

Add regression tests for `starknet_estimateFee` and `starknet_simulateTransactions` with `block_id = "pending"` when the pending cache is stale, asserting that the returned fee uses the correct `l1_data_gas_price` and `l2_gas_price` from the latest committed block.

---

### Proof of Concept

1. Deploy a node with Blob DA mode (`l1_da_mode = Blob`).
2. Commit block N with `l1_data_gas_price = X` (non-zero) and `l2_gas_price = Y` (non-zero).
3. Before the pending sync updates the in-memory `PendingData` (i.e., while `pending_data.block.parent_block_hash() != latest_header.block_hash`), call:
   ```
   starknet_estimateFee([<invoke_tx>], [], "pending")
   ```
4. Observe: the returned `l1_data_gas_price` in the fee estimate is `0` (from the `DeprecatedPendingBlock` default), and `l1_da_mode` is `Calldata`, even though the network uses Blob DA.
5. The same call immediately after the pending sync updates returns the correct non-zero `l1_data_gas_price`.

The stale window is reproducible by temporarily pausing the pending sync loop or by calling the RPC immediately after a block is committed. [7](#0-6) [8](#0-7)

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

**File:** crates/apollo_starknet_client/src/reader/objects/pending_data.rs (L169-175)
```rust
    pub fn l1_da_mode(&self) -> L1DataAvailabilityMode {
        match self {
            // In older versions, all blocks were using calldata.
            PendingBlockOrDeprecated::Deprecated(_) => L1DataAvailabilityMode::Calldata,
            PendingBlockOrDeprecated::Current(block) => block.l1_da_mode,
        }
    }
```

**File:** crates/apollo_rpc_execution/src/lib.rs (L322-366)
```rust
fn create_block_context(
    cached_state: &mut CachedState<ExecutionStateReader>,
    block_context_number: BlockNumber,
    chain_id: ChainId,
    storage_reader: &StorageReader,
    maybe_pending_data: Option<&PendingData>,
    execution_config: &ExecutionConfig,
    // TODO(shahak): Remove this once we stop supporting rpc v0.6.
    override_kzg_da_to_false: bool,
) -> ExecutionResult<BlockContext> {
    let (
        block_number,
        block_timestamp,
        l1_gas_price,
        l1_data_gas_price,
        l2_gas_price,
        sequencer_address,
        l1_da_mode,
    ) = match maybe_pending_data {
        Some(pending_data) => (
            block_context_number.unchecked_next(),
            pending_data.timestamp,
            pending_data.l1_gas_price,
            pending_data.l1_data_gas_price,
            pending_data.l2_gas_price,
            pending_data.sequencer,
            pending_data.l1_da_mode,
        ),
        None => {
            let header = storage_reader
                .begin_ro_txn()?
                .get_block_header(block_context_number)?
                .expect("Should have block header.")
                .block_header_without_hash;
            (
                header.block_number,
                header.timestamp,
                header.l1_gas_price,
                header.l1_data_gas_price,
                header.l2_gas_price,
                header.sequencer,
                header.l1_da_mode,
            )
        }
    };
```
