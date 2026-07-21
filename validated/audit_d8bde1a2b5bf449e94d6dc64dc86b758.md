### Title
Gateway Validates L2 Gas Price Against Stale `l2_gas_price` Instead of `next_l2_gas_price`, Enabling Attacker-Triggered Mass Transaction Demotion - (`File: crates/apollo_gateway/src/stateful_transaction_validator.rs`)

---

### Summary

The gateway's stateful admission check validates a transaction's `max_price_per_unit` against the **current block's** `l2_gas_price`, while the mempool's sequencing threshold is set to the **next block's** `l2_gas_price` (the EIP-1559-adjusted `next_l2_gas_price`). When an attacker fills a block to near-capacity, the EIP-1559 formula raises `next_l2_gas_price` well above the current `l2_gas_price`. Transactions admitted by the gateway at the stale threshold are immediately demoted to the mempool's `pending_queue` and never sequenced, constituting a gateway-admission/mempool-ordering invariant break.

---

### Finding Description

**Step 1 – Gateway reads the wrong price.**

In `validate_resource_bounds`, the gateway fetches the latest committed block's `l2_gas_price` (the price used for execution in that block) as the admission threshold: [1](#0-0) 

The inline TODO at line 202 explicitly acknowledges the defect:

```rust
// TODO(Arni): getnext_l2_gas_price from the block header.
let previous_block_l2_gas_price = self
    .gateway_fixed_block_state_reader
    .get_block_info()
    .await?
    .gas_prices
    .strk_gas_prices
    .l2_gas_price;   // ← uses l2_gas_price, not next_l2_gas_price
```

`GatewayFixedBlockSyncStateClient::get_block_info_from_sync_client` maps `block_header.l2_gas_price.price_in_fri` into the returned `BlockInfo`: [2](#0-1) 

The block header stores both fields separately: [3](#0-2) 

**Step 2 – Orchestrator writes `next_l2_gas_price` into the block header.**

After each decided block, `update_state_sync_with_new_block` stores `self.l2_gas_price` (the EIP-1559-computed next price) as `next_l2_gas_price` and the execution price as `l2_gas_price`: [4](#0-3) 

`update_l2_gas_price` advances `self.l2_gas_price` via `calculate_next_l2_gas_price_for_fin`: [5](#0-4) 

**Step 3 – EIP-1559 formula raises `next_l2_gas_price` when a block is congested.**

`calculate_next_base_gas_price` increases the price proportionally to `(gas_used − gas_target)`: [6](#0-5) 

With `gas_target = 1_500_000_000` and `max_block_size = 5_800_000_000` (v0.14.2 constants), filling a block to 90 % of capacity raises the price by ≈ 8.3 % per block. Sustained over several blocks the gap between `l2_gas_price` and `next_l2_gas_price` grows unboundedly. [7](#0-6) 

**Step 4 – Batcher sets the mempool threshold to `next_l2_gas_price`.**

When proposing a new block the batcher calls `update_gas_price` with the **proposal's** L2 gas price, which equals the previous block's `next_l2_gas_price`: [8](#0-7) 

**Step 5 – Mempool demotes admitted transactions.**

`FeeTransactionQueue::demote_txs_to_pending` moves every transaction whose `max_l2_gas_price < new_threshold` from the priority queue to the pending queue: [9](#0-8) 

Transactions in the pending queue are never pulled by the batcher: [10](#0-9) 

---

### Impact Explanation

**Impact: High – Mempool/gateway admission rejects valid transactions before sequencing.**

A transaction submitted with `max_price_per_unit = P_current` (the gateway's threshold) passes gateway admission but is immediately demoted in the mempool when the batcher raises the threshold to `P_next > P_current`. The transaction is never sequenced despite being correctly priced at admission time. This is a broken admission invariant: the gateway's acceptance guarantee is not honored by the mempool.

---

### Likelihood Explanation

**Likelihood: Medium.**

The attacker must fill blocks to above `gas_target` for several consecutive blocks. This costs gas but is economically feasible for a motivated actor (analogous to the Liquity whale scenario). At early chain stages the absolute cost is lower because `gas_target` is smaller relative to the minimum gas price. The attack is self-sustaining: each congested block widens the `l2_gas_price` / `next_l2_gas_price` gap, progressively demoting more pending transactions.

---

### Recommendation

Replace the stale `l2_gas_price` read in `validate_resource_bounds` with `next_l2_gas_price` from the block header, resolving the existing TODO:

```rust
// In GatewayFixedBlockSyncStateClient::get_block_info_from_sync_client,
// expose next_l2_gas_price in the returned BlockInfo (or a dedicated field).

// In validate_resource_bounds, use:
let previous_block_l2_gas_price = self
    .gateway_fixed_block_state_reader
    .get_next_l2_gas_price()   // reads block_header.next_l2_gas_price
    .await?;
```

This ensures the gateway and mempool use the same price reference, preserving the admission invariant.

---

### Proof of Concept

1. **Setup**: Deploy a contract that consumes close to `max_block_size` gas per invocation.
2. **Congestion phase**: Submit N transactions from the attacker's account, each consuming `≈ max_block_size * 0.9 / N` gas, filling several consecutive blocks above `gas_target`.
3. **Observe price divergence**: After each block, `next_l2_gas_price` increases by `≈ price * (gas_used − gas_target) / (gas_target * 48)`. After 5 full blocks, `next_l2_gas_price ≈ 1.5 × l2_gas_price`.
4. **Victim submission**: Victim submits a transaction with `max_price_per_unit = l2_gas_price` (the gateway's current threshold). Gateway accepts it (`GAS_PRICE_TOO_LOW` check passes).
5. **Demotion**: Batcher starts the next block proposal, calls `update_gas_price(next_l2_gas_price)`. `demote_txs_to_pending` moves the victim's transaction to the pending queue.
6. **Result**: Victim's transaction is never sequenced. The victim must resubmit with `max_price_per_unit ≥ next_l2_gas_price`, but by then the attacker may have already raised the price further.

### Citations

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L200-213)
```rust
        // Skip this validation during the systems bootstrap phase.
        if self.config.validate_resource_bounds {
            // TODO(Arni): getnext_l2_gas_price from the block header.
            let previous_block_l2_gas_price = self
                .gateway_fixed_block_state_reader
                .get_block_info()
                .await?
                .gas_prices
                .strk_gas_prices
                .l2_gas_price;
            self.validate_tx_l2_gas_price_within_threshold(
                executable_tx.resource_bounds(),
                previous_block_l2_gas_price,
            )?;
```

**File:** crates/apollo_gateway/src/gateway_fixed_block_state_reader.rs (L52-56)
```rust
                strk_gas_prices: GasPriceVector {
                    l1_gas_price: block_header.l1_gas_price.price_in_fri.try_into()?,
                    l1_data_gas_price: block_header.l1_data_gas_price.price_in_fri.try_into()?,
                    l2_gas_price: block_header.l2_gas_price.price_in_fri.try_into()?,
                },
```

**File:** crates/apollo_storage/src/header.rs (L86-107)
```rust
    /// The amount of L2 gas consumed.
    pub l2_gas_consumed: GasAmount,
    /// The next L2 gas price.
    pub next_l2_gas_price: GasPrice,
    /// The state root after this block.
    pub state_root: GlobalRoot,
    /// The sequencer address that created this block.
    pub sequencer: SequencerContractAddress,
    /// The timestamp of this block.
    pub timestamp: BlockTimestamp,
    /// The L1 data availability mode.
    pub l1_da_mode: L1DataAvailabilityMode,
    /// The state diff commitment, if available.
    pub state_diff_commitment: Option<StateDiffCommitment>,
    /// The transaction commitment, if available.
    pub transaction_commitment: Option<TransactionCommitment>,
    /// The event commitment, if available.
    pub event_commitment: Option<EventCommitment>,
    /// The receipt commitment, if available.
    pub receipt_commitment: Option<ReceiptCommitment>,
    /// The length of the state diff, if available.
    pub state_diff_length: Option<usize>,
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L328-340)
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
            // TODO(guy.f): Figure out where/if to get the values below from and fill them.
            ..Default::default()
        };
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L365-369)
```rust
    fn update_l2_gas_price(&mut self, height: BlockNumber, l2_gas_used: GasAmount) {
        self.l2_gas_price = self.calculate_next_l2_gas_price(height, l2_gas_used);
        let gas_price_u64 = u64::try_from(self.l2_gas_price.0).unwrap_or(u64::MAX);
        CONSENSUS_L2_GAS_PRICE.set_lossy(gas_price_u64);
    }
```

**File:** crates/apollo_consensus_orchestrator/src/fee_market/mod.rs (L115-137)
```rust
    // Use U256 to avoid overflow, as multiplying a u128 by a u64 remains within U256 bounds.
    let gas_delta = U256::from(gas_used.0.abs_diff(gas_target.0));
    let gas_target_u256 = U256::from(gas_target.0);
    let price_u256 = U256::from(price.0);

    // Calculate price change by multiplying first, then dividing. This avoids the precision loss
    // that occurs when dividing before multiplying.
    let denominator =
        gas_target_u256 * U256::from(versioned_constants.gas_price_max_change_denominator);
    let price_change = (price_u256 * gas_delta) / denominator;

    let adjusted_price_u256 =
        if gas_used > gas_target { price_u256 + price_change } else { price_u256 - price_change };

    // Sanity check: ensure direction of change is correct
    assert!(
        gas_used > gas_target && adjusted_price_u256 >= price_u256
            || gas_used <= gas_target && adjusted_price_u256 <= price_u256
    );

    // Price should not realistically exceed u128::MAX, bound to avoid theoretical overflow.
    let adjusted_price = u128::try_from(adjusted_price_u256).unwrap_or(u128::MAX);
    GasPrice(max(adjusted_price, min_gas_price.0))
```

**File:** crates/apollo_consensus_orchestrator/resources/orchestrator_versioned_constants_0_14_2.json (L1-7)
```json
{
    "gas_price_max_change_denominator": 48,
    "gas_target": 1500000000,
    "max_block_size": 5800000000,
    "min_gas_price": "0x1dcd65000",
    "l1_gas_price_margin_percent": 10
}
```

**File:** crates/apollo_batcher/src/batcher.rs (L289-301)
```rust
        info!(
            "Updating gas price for block {}, round {} in Mempool client",
            block_number, propose_block_input.proposal_round
        );
        self.mempool_client
            .update_gas_price(
                propose_block_input.block_info.gas_prices.strk_gas_prices.l2_gas_price.get(),
            )
            .await
            .map_err(|err| {
                error!("Failed to update gas price in mempool: {}", err);
                BatcherError::InternalError
            })?;
```

**File:** crates/apollo_mempool/src/fee_transaction_queue.rs (L104-106)
```rust
    fn has_ready_txs(&self) -> bool {
        !self.priority_queue.is_empty()
    }
```

**File:** crates/apollo_mempool/src/fee_transaction_queue.rs (L180-194)
```rust
    fn demote_txs_to_pending(&mut self, threshold: GasPrice) {
        let mut txs_to_remove = Vec::new();

        // Remove all transactions from the priority queue that are below the threshold.
        for priority_tx in &self.priority_queue {
            if priority_tx.max_l2_gas_price < threshold {
                txs_to_remove.push(*priority_tx);
            }
        }

        for tx in &txs_to_remove {
            self.priority_queue.remove(tx);
        }
        self.pending_queue.extend(txs_to_remove.iter().map(|tx| PendingTransaction::from(tx.0)));
    }
```
