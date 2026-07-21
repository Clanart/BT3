### Title
Stale Pending Block Fallback in `read_pending_data` Silently Zeroes L2/L1-Data Gas Prices, Returning Authoritative-Looking Wrong Fee Estimates — (File: `crates/apollo_rpc/src/v0_8/api/api_impl.rs`)

---

### Summary

When the in-memory pending block is stale (its `parent_block_hash` no longer matches the latest finalized block hash), `read_pending_data` synthesizes a fallback `DeprecatedPendingBlock`. Because `DeprecatedPendingBlock` carries no `l2_gas_price` or `l1_data_gas_price` fields, both prices are silently returned as zero. Every call to `starknet_estimateFee`, `starknet_simulateTransactions`, or `starknet_traceTransaction` that targets `Tag::Pending` during this window receives a block context with L2 gas price = 1 (`NonzeroGasPrice::MIN`) and L1 data gas price = 1, producing a fee estimate that is orders of magnitude too low relative to the actual next-block prices.

---

### Finding Description

**Root cause — `read_pending_data` fallback uses the wrong block variant**

`read_pending_data` checks freshness by comparing `pending_data.block.parent_block_hash()` to `latest_header.block_hash`. When they differ it constructs:

```rust
// crates/apollo_rpc/src/v0_8/api/api_impl.rs  lines 1572-1594
Ok(PendingData {
    block: PendingBlockOrDeprecated::Deprecated(DeprecatedPendingBlock {
        parent_block_hash: latest_header.block_hash,
        eth_l1_gas_price: latest_header.block_header_without_hash.l1_gas_price.price_in_wei,
        strk_l1_gas_price: latest_header.block_header_without_hash.l1_gas_price.price_in_fri,
        timestamp: latest_header.block_header_without_hash.timestamp,
        sequencer_address: latest_header.block_header_without_hash.sequencer,
        starknet_version: ...,
        ..Default::default()          // ← l2_gas_price and l1_data_gas_price absent
    }),
    ...
})
```

`DeprecatedPendingBlock` has no `l2_gas_price` or `l1_data_gas_price` fields. The accessor methods on `PendingBlockOrDeprecated` explicitly return zero for the `Deprecated` variant:

```rust
// crates/apollo_starknet_client/src/reader/objects/pending_data.rs  lines 162-168
pub fn l2_gas_price(&self) -> GasPricePerToken {
    match self {
        // In older versions, L2 gas price was 0.
        PendingBlockOrDeprecated::Deprecated(_) => GasPricePerToken::default(),
        PendingBlockOrDeprecated::Current(block) => block.l2_gas_price,
    }
}
```

**Propagation into fee estimation**

`estimate_fee` (and `simulate_transactions`) calls `read_pending_data` and passes the result through `client_pending_data_to_execution_pending_data`:

```rust
// crates/apollo_rpc/src/pending.rs  lines 18-21
l2_gas_price: client_pending_data.block.l2_gas_price(),       // → 0
l1_data_gas_price: client_pending_data.block.l1_data_gas_price(), // → 0
```

In `create_block_context_for_execution`, zero prices are clamped to `NonzeroGasPrice::MIN` = 1:

```rust
// crates/apollo_rpc_execution/src/lib.rs  lines 386-394
l2_gas_price: NonzeroGasPrice::new(l2_gas_price.price_in_wei)
    .unwrap_or(NonzeroGasPrice::MIN),   // 0 → 1
l1_data_gas_price: NonzeroGasPrice::new(l1_data_gas_price.price_in_wei)
    .unwrap_or(NonzeroGasPrice::MIN),   // 0 → 1
```

The block context used for the entire fee simulation therefore has L2 gas price = 1 and L1 data gas price = 1, regardless of the actual network prices.

**Trigger condition**

The stale window opens the moment a new block is finalized and closes when `sync_pending_data` writes fresh pending data. During this window — which occurs with every block finalization — any `starknet_estimateFee` or `starknet_simulateTransactions` call with `block_id = Tag::Pending` hits the broken fallback path.

---

### Impact Explanation

`starknet_estimateFee` and `starknet_simulateTransactions` return a fee estimate computed with L2 gas price = 1 and L1 data gas price = 1. If the actual network L2 gas price is on the order of 10⁹ fri (typical mainnet values), the returned estimate is off by ~9 orders of magnitude. A user who sets their v3 transaction's `max_price_per_unit` based on this estimate will have their transaction fail the post-execution bounds check (`TransactionFeeError::MaxGasAmountExceeded` / `InsufficientResourceBounds`) when the sequencer executes it against the real block context. The RPC response carries no staleness indicator, so the estimate is authoritative-looking.

This matches the allowed impact scope: **High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value.**

---

### Likelihood Explanation

The stale window is a normal, recurring event: it opens on every block finalization and closes when the pending-sync loop (`sync_pending_data`) fetches and writes fresh data. The window duration is bounded by the polling interval of the pending sync loop. Any client that calls `starknet_estimateFee(Tag::Pending)` during this window — which can be triggered by any user simply by timing their call to coincide with block finalization — receives the wrong estimate. No privileged access is required.

---

### Recommendation

Replace the `DeprecatedPendingBlock` fallback with a `PendingBlock` (current variant) that explicitly copies `l2_gas_price` and `l1_data_gas_price` from the latest block header:

```rust
// read_pending_data fallback — proposed fix
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
```

---

### Proof of Concept

```
Step 1 — Fresh pending data:
  Call starknet_estimateFee(transactions=[tx], block_id=Tag::Pending)
  → pending_data.block.parent_block_hash() == latest_header.block_hash  ✓
  → l2_gas_price = 1_000_000_000 fri (actual network price)
  → fee_estimate.overall_fee = X  (correct)

Step 2 — New block finalized, pending data not yet refreshed:
  (pending_data.block.parent_block_hash() ≠ latest_header.block_hash)

Step 3 — Same call during stale window:
  Call starknet_estimateFee(transactions=[tx], block_id=Tag::Pending)
  → read_pending_data returns DeprecatedPendingBlock fallback
  → l2_gas_price() returns GasPricePerToken::default() = 0
  → create_block_context_for_execution clamps 0 → NonzeroGasPrice::MIN = 1
  → fee_estimate.overall_fee = Y  (Y << X, wrong by ~9 orders of magnitude)

Step 4 — User submits transaction with max_price_per_unit = Y/gas_consumed:
  → Sequencer executes against real block context (l2_gas_price = 1_000_000_000)
  → Post-execution check: actual_fee > max_fee  → transaction reverted/rejected
```

**Exact corrupted value**: `ExecutionPendingData::l2_gas_price` and `l1_data_gas_price` are `GasPricePerToken { price_in_wei: 0, price_in_fri: 0 }` instead of the latest block's actual prices, causing the block context passed to `exec_estimate_fee` to use `NonzeroGasPrice::MIN` = 1 for both dimensions. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** crates/apollo_starknet_client/src/reader/objects/pending_data.rs (L162-168)
```rust
    pub fn l2_gas_price(&self) -> GasPricePerToken {
        match self {
            // In older versions, L2 gas price was 0.
            PendingBlockOrDeprecated::Deprecated(_) => GasPricePerToken::default(),
            PendingBlockOrDeprecated::Current(block) => block.l2_gas_price,
        }
    }
```

**File:** crates/apollo_rpc/src/pending.rs (L17-22)
```rust
        timestamp: client_pending_data.block.timestamp(),
        l1_gas_price: client_pending_data.block.l1_gas_price(),
        l1_data_gas_price: client_pending_data.block.l1_data_gas_price(),
        l2_gas_price: client_pending_data.block.l2_gas_price(),
        l1_da_mode: client_pending_data.block.l1_da_mode(),
        sequencer: client_pending_data.block.sequencer_address(),
```

**File:** crates/apollo_rpc_execution/src/lib.rs (L380-397)
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
        },
```
