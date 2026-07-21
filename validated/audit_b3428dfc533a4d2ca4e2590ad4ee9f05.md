### Title
Gateway L2 Gas Price Threshold Validation Uses Stale `l2_gas_price` Instead of `next_l2_gas_price`, Admitting Transactions That Will Fail at Execution - (File: `crates/apollo_gateway/src/stateful_transaction_validator.rs`)

---

### Summary

The gateway's stateful validator checks a transaction's `max_price_per_unit` against the **current block's** `l2_gas_price`, but the block that will actually execute the transaction uses `next_l2_gas_price` (computed via EIP-1559 from the current block's gas consumption). When the price increases between blocks, transactions whose `max_price_per_unit` falls between `l2_gas_price` and `next_l2_gas_price` pass gateway admission but are rejected by the blockifier's pre-validation at execution time. The inverse (price decrease) causes valid transactions to be incorrectly rejected at the gateway.

---

### Finding Description

`validate_resource_bounds` in `StatefulTransactionValidator` calls `get_block_info()` and reads `strk_gas_prices.l2_gas_price` — the price **used in the already-committed block** — as the reference for the threshold check:

```rust
// crates/apollo_gateway/src/stateful_transaction_validator.rs:196-213
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

A developer TODO in the same function acknowledges the problem:

```
// TODO(Arni): getnext_l2_gas_price from the block header.
```

The block header stores a distinct field `next_l2_gas_price` — the EIP-1559-derived price for the **next** block — computed by `calculate_next_l2_gas_price_for_fin` from `l2_gas_consumed` and the current price:

```rust
// crates/apollo_consensus_orchestrator/src/fee_market/mod.rs:57-74
pub fn calculate_next_l2_gas_price_for_fin(
    current_l2_gas_price: GasPrice,
    height: BlockNumber,
    l2_gas_used: GasAmount,
    ...
) -> GasPrice { ... }
```

This `next_l2_gas_price` is stored in `BlockHeaderWithoutHash.next_l2_gas_price` and `StorageBlockHeader.next_l2_gas_price`, and is what the blockifier actually enforces during pre-validation:

```rust
// crates/blockifier/src/transaction/account_transaction.rs:437-444
if resource_bounds.max_price_per_unit < actual_gas_price.get() {
    insufficiencies_resource.push(
        ResourceBoundsError::MaxGasPriceTooLow { ... }
    );
}
```

The gap between `l2_gas_price` (gateway check) and `next_l2_gas_price` (blockifier check) is the vulnerability window.

---

### Impact Explanation

**Scenario A — price increasing (congested block):**
`next_l2_gas_price > l2_gas_price`. A transaction with `max_price_per_unit` in the range `[l2_gas_price * threshold_pct, next_l2_gas_price)` passes the gateway threshold check but fails the blockifier's `MaxGasPriceTooLow` pre-validation when the block is built. The transaction is admitted to the mempool, consumes sequencer resources, and is ultimately rejected at execution — matching the "admission accepts invalid transactions" High impact.

**Scenario B — price decreasing:**
`next_l2_gas_price < l2_gas_price`. A transaction with `max_price_per_unit` in the range `[next_l2_gas_price, l2_gas_price * threshold_pct)` is rejected by the gateway even though it would succeed at execution — matching the "rejects valid transactions" High impact.

Both scenarios are reachable by any unprivileged user submitting a V3 (`AllResources`) transaction through the public gateway endpoint.

---

### Likelihood Explanation

The EIP-1559 mechanism adjusts `next_l2_gas_price` every block based on `l2_gas_consumed` vs `gas_target`. Any block that deviates from the target (which is the normal case under real load) produces a `next_l2_gas_price ≠ l2_gas_price`. The discrepancy is bounded by `gas_price_max_change_denominator` per block but is always present. The TODO comment confirms the developers are aware the wrong field is being read.

---

### Recommendation

Replace the `l2_gas_price` read in `validate_resource_bounds` with `next_l2_gas_price` from the stored block header. The `StorageBlockHeader` already carries this field:

```rust
// crates/apollo_storage/src/header.rs:89
pub next_l2_gas_price: GasPrice,
```

The gateway's `gateway_fixed_block_state_reader` should expose `next_l2_gas_price` from the latest committed header, and `validate_tx_l2_gas_price_within_threshold` should receive that value instead of `l2_gas_price`.

---

### Proof of Concept

1. Observe the last committed block header: `l2_gas_price = P`, `next_l2_gas_price = P'` where `P' > P` (congested block).
2. Submit a V3 `AllResources` transaction with `l2_gas.max_price_per_unit = P * min_gas_price_percentage / 100` (just at the gateway threshold).
3. Gateway calls `validate_tx_l2_gas_price_within_threshold` with `previous_block_l2_gas_price = P`; the check passes because `max_price_per_unit >= P * threshold`.
4. Transaction enters the mempool and is included in the next block, which uses `P'` as its L2 gas price.
5. Blockifier pre-validation checks `max_price_per_unit >= P'`; since `max_price_per_unit < P'`, the transaction fails with `MaxGasPriceTooLow`.
6. The gateway admitted an invalid transaction — the threshold crossing from `P` to `P'` was not accounted for, directly analogous to the vault price-tier bypass in the external report. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L196-216)
```rust
    async fn validate_resource_bounds(
        &self,
        executable_tx: &ExecutableTransaction,
    ) -> StatefulTransactionValidatorResult<()> {
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
        }
        Ok(())
    }
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L330-362)
```rust
    // TODO(Arni): Consider running this validation for all gas prices.
    fn validate_tx_l2_gas_price_within_threshold(
        &self,
        tx_resource_bounds: ValidResourceBounds,
        previous_block_l2_gas_price: NonzeroGasPrice,
    ) -> StatefulTransactionValidatorResult<()> {
        match tx_resource_bounds {
            ValidResourceBounds::AllResources(tx_resource_bounds) => {
                let tx_l2_gas_price = tx_resource_bounds.l2_gas.max_price_per_unit;
                let gas_price_threshold_multiplier =
                    Ratio::new(self.config.min_gas_price_percentage.into(), 100_u128);
                let threshold = (gas_price_threshold_multiplier
                    * previous_block_l2_gas_price.get().0)
                    .to_integer();
                if tx_l2_gas_price.0 < threshold {
                    return Err(StarknetError {
                        // We didn't have this kind of an error.
                        code: StarknetErrorCode::UnknownErrorCode(
                            "StarknetErrorCode.GAS_PRICE_TOO_LOW".to_string(),
                        ),
                        message: format!(
                            "Transaction L2 gas price {tx_l2_gas_price} is below the required \
                             threshold {threshold}.",
                        ),
                    });
                }
            }
            ValidResourceBounds::L1Gas(_) => {
                // No validation required for legacy transactions.
            }
        }
        Ok(())
    }
```

**File:** crates/apollo_consensus_orchestrator/src/fee_market/mod.rs (L57-74)
```rust
pub fn calculate_next_l2_gas_price_for_fin(
    current_l2_gas_price: GasPrice,
    height: BlockNumber,
    l2_gas_used: GasAmount,
    override_l2_gas_price_fri: Option<u128>,
    min_l2_gas_price_per_height: &[PricePerHeight],
) -> GasPrice {
    if let Some(override_value) = override_l2_gas_price_fri {
        info!(
            "L2 gas price ({}) is not updated, remains on override value of {override_value} fri",
            current_l2_gas_price.0
        );
        return GasPrice(override_value);
    }
    let gas_target = VersionedConstants::latest_constants().gas_target;
    let min_gas_price = get_min_gas_price_for_height(height, min_l2_gas_price_per_height);
    calculate_next_base_gas_price(current_l2_gas_price, l2_gas_used, gas_target, min_gas_price)
}
```

**File:** crates/apollo_storage/src/header.rs (L87-90)
```rust
    pub l2_gas_consumed: GasAmount,
    /// The next L2 gas price.
    pub next_l2_gas_price: GasPrice,
    /// The state root after this block.
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L437-444)
```rust
                            if resource_bounds.max_price_per_unit < actual_gas_price.get() {
                                insufficiencies_resource.push(
                                    ResourceBoundsError::MaxGasPriceTooLow {
                                        resource: *resource,
                                        max_gas_price: resource_bounds.max_price_per_unit,
                                        actual_gas_price: (*actual_gas_price).into(),
                                    },
                                );
```

**File:** crates/starknet_api/src/block.rs (L236-238)
```rust
    pub l2_gas_consumed: GasAmount,
    pub next_l2_gas_price: GasPrice,
    pub state_root: GlobalRoot,
```
