### Title
Stale Pending Data Fallback Silently Zeros `l1_data_gas_price` and `l2_gas_price` in RPC Fee Estimation — (`crates/apollo_rpc/src/v0_8/api/api_impl.rs`)

---

### Summary

When the in-memory pending data is stale (its `parent_block_hash` does not match the latest committed block), `read_pending_data` constructs a fallback `DeprecatedPendingBlock`. That type structurally omits `l1_data_gas_price` and `l2_gas_price`, so both fields silently become zero. Every downstream RPC call that uses `block_id: "pending"` — `starknet_estimateFee`, `starknet_simulateTransactions`, `starknet_traceBlockTransactions` — then builds a `BlockContext` with `NonzeroGasPrice::MIN` for those two price dimensions, producing authoritative-looking but materially wrong fee estimates.

---

### Finding Description

**Root cause — `read_pending_data` fallback path**

`read_pending_data` is called for every pending-block RPC request. When the cached pending data is behind the latest committed block it falls into the `else` branch and synthesises a `DeprecatedPendingBlock`:

```rust
// crates/apollo_rpc/src/v0_8/api/api_impl.rs  lines 1572-1594
Ok(PendingData {
    block: PendingBlockOrDeprecated::Deprecated(DeprecatedPendingBlock {
        parent_block_hash: latest_header.block_hash,
        eth_l1_gas_price: latest_header.block_header_without_hash.l1_gas_price.price_in_wei,
        strk_l1_gas_price: latest_header.block_header_without_hash.l1_gas_price.price_in_fri,
        timestamp: ...,
        sequencer_address: ...,
        starknet_version: ...,
        ..Default::default()   // ← l1_data_gas_price and l2_gas_price are NOT set
    }),
    ...
})
``` [1](#0-0) 

**Structural zero — `DeprecatedPendingBlock` accessors**

`DeprecatedPendingBlock` has no `l1_data_gas_price` or `l2_gas_price` fields. The `PendingBlockOrDeprecated` accessor methods explicitly return `GasPricePerToken::default()` (i.e., both `price_in_wei` and `price_in_fri` = 0) for the deprecated variant:

```rust
// crates/apollo_starknet_client/src/reader/objects/pending_data.rs  lines 155-167
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

**Silent substitution — `prepare_block_execution_context`**

The zero values flow into `prepare_block_execution_context` in `apollo_rpc_execution`. Because `NonzeroGasPrice::new(0)` fails, the code silently falls back to `NonzeroGasPrice::MIN`:

```rust
// crates/apollo_rpc_execution/src/lib.rs  lines 384-395
l1_data_gas_price: NonzeroGasPrice::new(l1_data_gas_price.price_in_wei)
    .unwrap_or(NonzeroGasPrice::MIN),   // ← zero → MIN
l2_gas_price: NonzeroGasPrice::new(l2_gas_price.price_in_wei)
    .unwrap_or(NonzeroGasPrice::MIN),   // ← zero → MIN
``` [3](#0-2) 

**Propagation to fee output**

`tx_execution_output_to_fee_estimation` reads gas prices directly from the `BlockContext` built above and returns them as the authoritative `FeeEstimation`:

```rust
// crates/apollo_rpc_execution/src/objects.rs  lines 165-182
let gas_prices = &block_context.block_info().gas_prices;
let (l1_gas_price, l1_data_gas_price, l2_gas_price) = (
    gas_prices.l1_gas_price(&tx_execution_output.price_unit.into()).get(),
    gas_prices.l1_data_gas_price(&tx_execution_output.price_unit.into()).get(),
    gas_prices.l2_gas_price(&tx_execution_output.price_unit.into()).get(),
);
``` [4](#0-3) 

**Trigger condition**

The stale-pending-data window opens every time a new block is committed to storage before the pending-data cache is refreshed. This is a normal, recurring, unprivileged-observable condition. Any caller of `starknet_estimateFee`, `starknet_simulateTransactions`, or `starknet_traceBlockTransactions` with `block_id: "pending"` during that window receives wrong results.

---

### Impact Explanation

`starknet_estimateFee` and `starknet_simulateTransactions` return `l1_data_gas_price` and `l2_gas_price` equal to `NonzeroGasPrice::MIN` instead of the real current prices. For any transaction that consumes L2 gas or L1 data gas (i.e., all post-0.13.1 transactions), the `overall_fee` field is materially underestimated. A user who submits a transaction with the estimated `max_fee` will have it reverted on-chain for insufficient fee. This matches the allowed impact: **"High. RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value."**

---

### Likelihood Explanation

The stale-pending-data condition is not an edge case; it occurs on every block transition. The window duration depends on how quickly the pending-data cache is refreshed. During high block throughput or any delay in the feeder-gateway polling loop, the window can span multiple seconds. Any user calling `starknet_estimateFee` with `block_id: "pending"` during that window is affected without any special privileges.

---

### Recommendation

In the `read_pending_data` fallback branch, construct a `PendingBlockOrDeprecated::Current(PendingBlock)` instead of a `DeprecatedPendingBlock`, copying `l1_data_gas_price`, `l2_gas_price`, and `l1_da_mode` from the latest committed block header (`latest_header.block_header_without_hash`). This mirrors the same fields already copied for `l1_gas_price` and ensures the execution context used for fee estimation always carries the correct current-era gas prices.

---

### Proof of Concept

1. Commit block N to storage (e.g., via the batcher's `decision_reached` path).
2. Before the pending-data cache is refreshed, call `starknet_estimateFee` with `block_id: "pending"` for a V3 transaction that uses L2 gas.
3. `read_pending_data` detects `parent_block_hash` mismatch and returns a `DeprecatedPendingBlock` fallback.
4. `l1_data_gas_price` and `l2_gas_price` are both `GasPricePerToken { price_in_wei: 0, price_in_fri: 0 }`.
5. `prepare_block_execution_context` substitutes `NonzeroGasPrice::MIN` for both.
6. The returned `FeeEstimation.overall_fee` is computed with `l2_gas_price = NonzeroGasPrice::MIN` instead of the real price (e.g., 10 Gwei), producing a fee that is orders of magnitude too low.
7. Submitting the transaction with that `max_fee` causes an on-chain revert.

### Citations

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

**File:** crates/apollo_starknet_client/src/reader/objects/pending_data.rs (L155-167)
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
```

**File:** crates/apollo_rpc_execution/src/lib.rs (L384-395)
```rust
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
```

**File:** crates/apollo_rpc_execution/src/objects.rs (L165-182)
```rust
    let gas_prices = &block_context.block_info().gas_prices;
    let (l1_gas_price, l1_data_gas_price, l2_gas_price) = (
        gas_prices.l1_gas_price(&tx_execution_output.price_unit.into()).get(),
        gas_prices.l1_data_gas_price(&tx_execution_output.price_unit.into()).get(),
        gas_prices.l2_gas_price(&tx_execution_output.price_unit.into()).get(),
    );

    let gas_vector = tx_execution_output.execution_info.receipt.gas;

    Ok(FeeEstimation {
        gas_consumed: gas_vector.l1_gas.0.into(),
        l1_gas_price,
        data_gas_consumed: gas_vector.l1_data_gas.0.into(),
        l1_data_gas_price,
        l2_gas_price,
        overall_fee: tx_execution_output.execution_info.receipt.fee,
        unit: tx_execution_output.price_unit,
    })
```
