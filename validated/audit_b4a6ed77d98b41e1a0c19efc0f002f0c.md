### Title
Stale-Pending Fallback Emits Zero `l1_data_gas_price` / `l2_gas_price`, Causing RPC Pending View and Fee Estimation to Return Authoritative-Looking Wrong Values — (`File: crates/apollo_rpc/src/v0_8/api/api_impl.rs`)

---

### Summary

When the in-memory pending block is stale (its `parent_block_hash` no longer matches the latest committed block), `read_pending_data` synthesises a replacement using the legacy `DeprecatedPendingBlock` type. That type has no `l1_data_gas_price` or `l2_gas_price` fields; both accessors return `GasPricePerToken::default()` (all-zero). Every RPC endpoint that calls `read_pending_data` then serves those zeroes as authoritative pending-block gas prices. In the execution path the zeroes are silently promoted to `NonzeroGasPrice::MIN` (1 wei / 1 fri), so `estimate_fee`, `simulate_transactions`, and `trace_transaction` complete successfully but return fee estimates computed against a data-gas price and L2-gas price of 1 instead of the real market price.

---

### Finding Description

**Root cause — `read_pending_data` fallback (`api_impl.rs` lines 1572-1593)**

```
Ok(PendingData {
    block: PendingBlockOrDeprecated::Deprecated(DeprecatedPendingBlock {
        parent_block_hash: latest_header.block_hash,
        eth_l1_gas_price: latest_header…l1_gas_price.price_in_wei,
        strk_l1_gas_price: latest_header…l1_gas_price.price_in_fri,
        timestamp: latest_header…timestamp,
        …                          // l1_data_gas_price and l2_gas_price absent
    }),
    …
})
``` [1](#0-0) 

The fallback is triggered whenever `pending_data.block.parent_block_hash() != latest_header.block_hash`, which occurs in normal operation every time a new block is committed before the pending-data writer has updated the in-memory cache.

**Zero propagation — `PendingBlockOrDeprecated` accessors (`pending_data.rs` lines 155-168)**

```rust
pub fn l1_data_gas_price(&self) -> GasPricePerToken {
    match self {
        // In older versions, data gas price was 0.
        PendingBlockOrDeprecated::Deprecated(_) => GasPricePerToken::default(),
        …
    }
}
pub fn l2_gas_price(&self) -> GasPricePerToken {
    match self {
        // In older versions, L2 gas price was 0.
        PendingBlockOrDeprecated::Deprecated(_) => GasPricePerToken::default(),
        …
    }
}
``` [2](#0-1) 

**Wrong value surfaced in `get_block` (`api_impl.rs` lines 1618-1625)**

```rust
l1_data_gas_price: GasPricePerToken {
    price_in_wei: block.l1_data_gas_price().price_in_wei,  // 0
    price_in_fri: block.l1_data_gas_price().price_in_fri,  // 0
},
l2_gas_price: GasPricePerToken {
    price_in_wei: block.l2_gas_price().price_in_wei,        // 0
    price_in_fri: block.l2_gas_price().price_in_fri,        // 0
},
``` [3](#0-2) 

**Silent promotion to `NonzeroGasPrice::MIN` in execution context (`lib.rs` lines 384-395)**

```rust
l1_data_gas_price: NonzeroGasPrice::new(l1_data_gas_price.price_in_wei)
    .unwrap_or(NonzeroGasPrice::MIN),   // 0 → 1 wei, silently
l2_gas_price: NonzeroGasPrice::new(l2_gas_price.price_in_wei)
    .unwrap_or(NonzeroGasPrice::MIN),   // 0 → 1 wei, silently
``` [4](#0-3) 

Because the zero is silently replaced with 1, no error is raised; `estimate_fee` and `simulate_transactions` return a complete, well-formed `FeeEstimation` struct whose `l1_data_gas_price` and `l2_gas_price` fields are 1 wei / 1 fri instead of the real market values. The `overall_fee` field is therefore also wrong for any transaction that consumes data gas or L2 gas.

**Conversion path from `client_pending_data_to_execution_pending_data` (`pending.rs` lines 19-20)**

```rust
l1_data_gas_price: client_pending_data.block.l1_data_gas_price(),  // 0
l2_gas_price:      client_pending_data.block.l2_gas_price(),        // 0
``` [5](#0-4) 

---

### Impact Explanation

Every RPC caller that queries the pending block during the staleness window receives wrong data:

| Endpoint | Wrong output |
|---|---|
| `starknet_getBlockWithTxHashes` / `starknet_getBlockWithTxs` | `PendingBlockHeader.l1_data_gas_price = 0`, `l2_gas_price = 0` |
| `starknet_estimateFee` (pending) | `FeeEstimation.l1_data_gas_price = 1`, `l2_gas_price = 1`; `overall_fee` understated |
| `starknet_simulateTransactions` (pending) | Same wrong gas prices in simulation trace |
| `starknet_traceTransaction` (pending) | Same wrong gas prices in trace |

A user who calls `estimate_fee` against the pending block during the staleness window receives an authoritative-looking fee estimate that is severely understated for any transaction with non-zero data-gas or L2-gas consumption. If they submit the transaction with `max_fee` set to that estimate, the transaction may revert on-chain due to insufficient fee.

This matches: **High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value.**

---

### Likelihood Explanation

The staleness window is a normal, recurring condition: it opens every time a new block is committed and closes only when the pending-data writer pushes a fresh pending block whose `parent_block_hash` equals the new head. On a busy network this window is short but non-zero; on a slow or congested sequencer it can persist for many seconds. Any client that polls `estimate_fee` at a moderate rate will hit the window regularly. No special privilege or adversarial action is required.

---

### Recommendation

Replace the `DeprecatedPendingBlock` fallback with a `PendingBlock` (the current variant) that carries all gas-price fields populated from the latest committed block header:

```rust
block: PendingBlockOrDeprecated::Current(PendingBlock {
    parent_block_hash: latest_header.block_hash,
    l1_gas_price: latest_header.block_header_without_hash.l1_gas_price,
    l1_data_gas_price: latest_header.block_header_without_hash.l1_data_gas_price,
    l2_gas_price: latest_header.block_header_without_hash.l2_gas_price,
    l1_da_mode: latest_header.block_header_without_hash.l1_da_mode,
    timestamp: latest_header.block_header_without_hash.timestamp,
    sequencer_address: latest_header.block_header_without_hash.sequencer,
    starknet_version: latest_header.block_header_without_hash.starknet_version.to_string(),
    ..Default::default()
}),
```

This ensures that all three gas-price dimensions are correctly propagated to every downstream consumer without changing the staleness-detection logic.

---

### Proof of Concept

1. Run the node and wait for block N to be committed.
2. Before the pending-data writer emits a new pending block whose `parent_block_hash == block_N.hash`, call:
   ```
   starknet_getBlockWithTxHashes { block_id: "pending" }
   ```
   Observe `l1_data_gas_price = { price_in_wei: "0x0", price_in_fri: "0x0" }` and `l2_gas_price = { price_in_wei: "0x0", price_in_fri: "0x0" }` in the response, while the committed block N has non-zero values for both.

3. During the same window, call:
   ```
   starknet_estimateFee [invoke_tx_with_l2_gas_consumption], [], "pending"
   ```
   Observe that `l2_gas_price` in the returned `FeeEstimation` is `0x1` (1 wei) and `overall_fee` is drastically understated compared to the same call against `"latest"`.

The trigger is unprivileged (any RPC caller), the corrupted values are `l1_data_gas_price = 0` / `l2_gas_price = 0` in the view layer and `l1_data_gas_price = 1` / `l2_gas_price = 1` in the execution layer, and the root cause is the use of `DeprecatedPendingBlock` — which structurally cannot carry those fields — as the staleness fallback in `read_pending_data`. [6](#0-5) [2](#0-1) [7](#0-6) [8](#0-7)

### Citations

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

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1618-1625)
```rust
                l1_data_gas_price: GasPricePerToken {
                    price_in_wei: block.l1_data_gas_price().price_in_wei,
                    price_in_fri: block.l1_data_gas_price().price_in_fri,
                },
                l2_gas_price: GasPricePerToken {
                    price_in_wei: block.l2_gas_price().price_in_wei,
                    price_in_fri: block.l2_gas_price().price_in_fri,
                },
```

**File:** crates/apollo_starknet_client/src/reader/objects/pending_data.rs (L155-168)
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
```

**File:** crates/apollo_rpc_execution/src/lib.rs (L380-396)
```rust
        gas_prices: GasPrices {
            eth_gas_prices: GasPriceVector {
                l1_gas_price: NonzeroGasPrice::new(l1_gas_price.price_in_wei)
                    .unwrap_or(NonzeroGasPrice::MIN),
                l1_data_gas_price: NonzeroGasPrice::new(l1_data_gas_price.price_in_wei)
                    .unwrap_or(NonzeroGasPrice::MIN),
                l2_gas_price: NonzeroGasPrice::new(l2_gas_price.price_in_wei)
                    .unwrap_or(NonzeroGasPrice::MIN),
            },
            strk_gas_prices: GasPriceVector {
                l1_gas_price: NonzeroGasPrice::new(l1_gas_price.price_in_fri)
                    .unwrap_or(NonzeroGasPrice::MIN),
                l1_data_gas_price: NonzeroGasPrice::new(l1_data_gas_price.price_in_fri)
                    .unwrap_or(NonzeroGasPrice::MIN),
                l2_gas_price: NonzeroGasPrice::new(l2_gas_price.price_in_fri)
                    .unwrap_or(NonzeroGasPrice::MIN),
            },
```

**File:** crates/apollo_rpc/src/pending.rs (L17-21)
```rust
        timestamp: client_pending_data.block.timestamp(),
        l1_gas_price: client_pending_data.block.l1_gas_price(),
        l1_data_gas_price: client_pending_data.block.l1_data_gas_price(),
        l2_gas_price: client_pending_data.block.l2_gas_price(),
        l1_da_mode: client_pending_data.block.l1_da_mode(),
```
