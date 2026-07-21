### Title
`update_l2_gas_price` Called Before `record_fee_proposal` Causes `next_l2_gas_price` to Be Computed Without the Current Block's Fee Proposal — (`File: crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs`)

---

### Summary

In `finalize_decision`, `update_l2_gas_price(height, l2_gas_used)` is called **before** `record_fee_proposal(height, init.fee_proposal_fri)`. Because `update_l2_gas_price` internally calls `compute_fee_actual` over the window `[height − window_size, height − 1]`, the current block's fee proposal is never in the window at the time the next gas price is computed. The resulting `next_l2_gas_price` — written into `BlockHeaderWithoutHash` (state sync storage) and the cende blob's `FeeMarketInfo` — is systematically one block behind the correct value, causing every subsequent block to be built and validated with a wrong EIP-1559 floor.

---

### Finding Description

**Ordering in `finalize_decision`:**

```rust
// crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs  lines 517-518
self.update_l2_gas_price(height, l2_gas_used);          // ← mutates self.l2_gas_price
self.record_fee_proposal(height, init.fee_proposal_fri); // ← inserts height into window
```

`update_l2_gas_price` calls `calculate_next_l2_gas_price(height, l2_gas_used)`:

```rust
// lines 427-441
fn calculate_next_l2_gas_price(&self, height: BlockNumber, l2_gas_used: GasAmount) -> GasPrice {
    let fee_actual = compute_fee_actual(
        &self.fee_proposals_window,
        height,                                          // reads [height-W, height-1]
        VersionedConstants::latest_constants().fee_proposal_window_size,
    );
    calculate_next_l2_gas_price_for_fin(self.l2_gas_price, height, l2_gas_used, ..., fee_actual)
}
```

`compute_fee_actual` is defined to read `[height − window_size, height − 1]`:

```rust
// crates/apollo_consensus_orchestrator/src/dynamic_gas_price/mod.rs  lines 70-79
for source_height in (start..height.0).map(BlockNumber) {   // exclusive upper bound
    match fee_proposals_window.get(&source_height) { ... }
}
```

Because `record_fee_proposal(height, ...)` has not yet been called, the entry for `height` is absent from the window. The `fee_actual` used as the EIP-1559 floor in `calculate_next_l2_gas_price_for_fin` is therefore the median of `[height − W, height − 1]` instead of the correct `[height − W + 1, height]`.

The wrong `self.l2_gas_price` is then written into two authoritative outputs:

```rust
// line 405  (update_state_sync_with_new_block)
next_l2_gas_price: self.l2_gas_price,   // stored in BlockHeaderWithoutHash → state sync

// line 605  (prepare_blob_for_next_height)
fee_market_info: FeeMarketInfo { l2_gas_consumed: l2_gas_used, next_l2_gas_price: self.l2_gas_price },
```

---

### Impact Explanation

1. **Wrong `next_l2_gas_price` in state sync storage.** Every `BlockHeaderWithoutHash` written to state sync carries a `next_l2_gas_price` that was computed without the current block's fee proposal. RPC endpoints that expose block headers return this authoritative-looking wrong value.

2. **Wrong gas price for the next block.** When the next block is built, the proposer reads `self.l2_gas_price` (set by the previous block's `update_l2_gas_price`) as the L2 gas price. Because this value was computed with the wrong `fee_actual` floor, the gas price for every block is systematically off. Validators compute the same wrong value (same ordering bug), so consensus does not fail, but the gas price diverges from the spec-correct value.

3. **Wrong cende blob `FeeMarketInfo.next_l2_gas_price`.** The centralized recorder receives and persists the wrong next gas price, propagating the error to downstream consumers.

4. **Fee estimation wrong.** Any RPC fee estimation call for the next block uses the wrong `l2_gas_price`, returning an authoritative-looking incorrect fee.

Matches: **High — RPC execution / fee estimation returns an authoritative-looking wrong value.**

---

### Likelihood Explanation

This fires on every block once `StarknetVersion >= V0_14_3` (when `fee_proposal_fri` is `Some`). No special attacker action is required; the ordering bug is unconditional. The magnitude of the error grows when the current block's fee proposal diverges from the previous window's median (e.g., during rapid fee-market movement).

---

### Recommendation

Swap the two calls so the current block's fee proposal is in the window before the next gas price is computed:

```rust
// finalize_decision — correct order
self.record_fee_proposal(height, init.fee_proposal_fri);   // insert height first
self.update_l2_gas_price(height, l2_gas_used);             // now window includes height
```

Additionally, change `calculate_next_l2_gas_price` to call `compute_fee_actual` with `height.next()` (i.e., `height + 1`) so the window `[height − W + 1, height]` is used, which is the correct `fee_actual` for the block being priced:

```rust
fn calculate_next_l2_gas_price(&self, height: BlockNumber, l2_gas_used: GasAmount) -> GasPrice {
    let fee_actual = compute_fee_actual(
        &self.fee_proposals_window,
        height.next(),   // ← was `height`; now includes current block's fee_proposal
        VersionedConstants::latest_constants().fee_proposal_window_size,
    );
    ...
}
```

---

### Proof of Concept

**Step 1 — Steady state.** Suppose `window_size = 10` and blocks 90–99 each have `fee_proposal = 100 gwei`. At block 100, the proposer sets `fee_proposal = 200 gwei` (a large jump).

**Step 2 — `finalize_decision` for block 100.**
- `update_l2_gas_price(100, gas_used)` is called first.
- `compute_fee_actual(window, 100, 10)` reads heights 90–99 → median = 100 gwei.
- `effective_min = max(config_min, 100 gwei)` → floor = 100 gwei.
- `self.l2_gas_price` is set using this floor.
- `record_fee_proposal(100, 200 gwei)` is called after — too late.

**Step 3 — Block 101 is built.**
- Proposer uses `self.l2_gas_price` (computed with floor = 100 gwei, missing the 200 gwei proposal).
- Correct behavior: `compute_fee_actual(window, 101, 10)` would read heights 91–100 → median = 110 gwei (or higher), giving a higher floor.
- The gas price for block 101 is lower than the spec-correct value.

**Step 4 — RPC.**
- `starknet_getBlockWithTxHashes(block_id=100)` returns `next_l2_gas_price` = the wrong value computed in Step 2.
- Fee estimation for block 101 uses this wrong price. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L308-310)
```rust
    fn record_fee_proposal(&mut self, height: BlockNumber, fee_proposal_fri: Option<GasPrice>) {
        self.fee_proposals_window.insert(height, fee_proposal_fri);
    }
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L399-412)
```rust
        let block_header_without_hash = BlockHeaderWithoutHash {
            block_number: height,
            l1_gas_price,
            l1_data_gas_price,
            l2_gas_price,
            l2_gas_consumed: l2_gas_used,
            next_l2_gas_price: self.l2_gas_price,
            sequencer,
            timestamp: BlockTimestamp(init.timestamp),
            l1_da_mode: init.l1_da_mode,
            fee_proposal_fri: init.fee_proposal_fri,
            // TODO(guy.f): Figure out where/if to get the values below from and fill them.
            ..Default::default()
        };
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L427-441)
```rust
    fn calculate_next_l2_gas_price(&self, height: BlockNumber, l2_gas_used: GasAmount) -> GasPrice {
        let fee_actual = compute_fee_actual(
            &self.fee_proposals_window,
            height,
            VersionedConstants::latest_constants().fee_proposal_window_size,
        );
        calculate_next_l2_gas_price_for_fin(
            self.l2_gas_price,
            height,
            l2_gas_used,
            self.config.dynamic_config.override_l2_gas_price_fri,
            &self.config.dynamic_config.min_l2_gas_price_per_height,
            fee_actual,
        )
    }
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L515-518)
```rust
        let DecisionReachedResponse { state_diff, central_objects } = decision_reached_response;

        self.update_l2_gas_price(height, l2_gas_used);
        self.record_fee_proposal(height, init.fee_proposal_fri);
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L603-606)
```rust
                fee_market_info: FeeMarketInfo {
                    l2_gas_consumed: l2_gas_used,
                    next_l2_gas_price: self.l2_gas_price,
                },
```

**File:** crates/apollo_consensus_orchestrator/src/dynamic_gas_price/mod.rs (L56-91)
```rust
pub fn compute_fee_actual(
    fee_proposals_window: &BTreeMap<BlockNumber, Option<GasPrice>>,
    height: BlockNumber,
    window_size: u64,
) -> Option<GasPrice> {
    let Some(start) = height.0.checked_sub(window_size) else {
        warn!(
            "Cannot compute fee_actual for height {height}: height is below window_size \
             ({window_size})"
        );
        return None;
    };
    let window_size_usize = usize::try_from(window_size).expect("window_size fits in usize");
    let mut window = Vec::with_capacity(window_size_usize);
    for source_height in (start..height.0).map(BlockNumber) {
        match fee_proposals_window.get(&source_height) {
            Some(Some(price)) => window.push(*price),
            Some(None) | None => {
                warn!(
                    "Cannot compute fee_actual for height {height}: fee_proposals_window has no \
                     recorded fee_proposal for height {source_height}"
                );
                return None;
            }
        }
    }
    window.sort();
    let mid = window_size_usize / 2;
    let median = if window_size_usize.is_multiple_of(2) {
        // Even: average of the two middle values, rounded down.
        // Overflow-safe averaging: a + (b - a) / 2 (safe because sorted, so b >= a).
        GasPrice(window[mid - 1].0 + (window[mid].0 - window[mid - 1].0) / 2)
    } else {
        window[mid]
    };
    Some(median)
```

**File:** crates/apollo_consensus_orchestrator/src/fee_market/mod.rs (L55-77)
```rust
pub fn calculate_next_l2_gas_price_for_fin(
    current_l2_gas_price: GasPrice,
    height: BlockNumber,
    l2_gas_used: GasAmount,
    override_l2_gas_price_fri: Option<u128>,
    min_l2_gas_price_per_height: &[PricePerHeight],
    fee_actual: Option<GasPrice>,
) -> GasPrice {
    if let Some(override_value) = override_l2_gas_price_fri {
        info!(
            "L2 gas price ({}) is not updated, remains on override value of {override_value} fri",
            current_l2_gas_price.0
        );
        return GasPrice(override_value);
    }
    let gas_target = VersionedConstants::latest_constants().gas_target;
    let config_min = get_min_gas_price_for_height(height, min_l2_gas_price_per_height);
    let effective_min = match fee_actual {
        Some(fa) => GasPrice(max(config_min.0, fa.0)),
        None => config_min,
    };
    calculate_next_base_gas_price(current_l2_gas_price, l2_gas_used, gas_target, effective_min)
}
```
