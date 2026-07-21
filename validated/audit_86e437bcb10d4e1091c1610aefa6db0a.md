### Title
Gateway `validate_resource_bounds` Uses Stale Previous-Block L2 Gas Price, Admitting Transactions the Batcher Rejects — (`crates/apollo_gateway/src/stateful_transaction_validator.rs`)

### Summary

The gateway's stateful admission check validates a V3 transaction's `max_price_per_unit` against the **previous block's** L2 gas price, while the blockifier's pre-validation enforces the **next block's** L2 gas price (computed by EIP-1559). When the previous block was heavily used, the next block's price is strictly higher, so the gateway admits transactions that the batcher will unconditionally reject during sequencing.

### Finding Description

In `validate_resource_bounds`, the gateway reads the L2 gas price from the most-recently-finalized block header:

```rust
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
``` [1](#0-0) 

`validate_tx_l2_gas_price_within_threshold` then requires `tx.max_price_per_unit >= min_gas_price_percentage% × previous_block_l2_gas_price` (default: 100%): [2](#0-1) 

The batcher, however, builds the next block using a gas price computed by `calculate_next_base_gas_price` (EIP-1559). When the previous block's gas usage exceeds `gas_target`, this price is strictly higher than the previous block's price: [3](#0-2) 

During execution, `check_fee_bounds` in `perform_pre_validation_stage` rejects any transaction whose `max_price_per_unit` is below the **current block's** actual L2 gas price: [4](#0-3) 

The two checks use different reference prices with no reconciliation between them. The TODO comment in the gateway code explicitly acknowledges the correct fix has not been applied.

### Impact Explanation

A transaction with `max_price_per_unit = X` where `previous_block_price ≤ X < next_block_price` passes the gateway's admission check and enters the mempool, but is unconditionally rejected by `check_fee_bounds` when the batcher attempts to sequence it. This is a **High** impact under the "Mempool/gateway/RPC admission accepts invalid transactions before sequencing" criterion: the gateway's authoritative admission decision is wrong, and the discrepancy is not caught until sequencing time.

### Likelihood Explanation

The condition triggers whenever the previous block's L2 gas usage exceeds `gas_target` — a routine occurrence on a busy network. The EIP-1559 price increase per block is bounded but non-zero, so the window `[previous_price, next_price)` is always open after a full block. Any unprivileged user can craft a V3 (`AllResources`) transaction whose `l2_gas.max_price_per_unit` falls in this window.

### Recommendation

Replace the stale `previous_block_l2_gas_price` read with the **next block's** L2 gas price, which is already stored in the block header as `next_l2_gas_price` and is available via `BlockHeaderWithoutHash`. This is exactly what the existing TODO comment requests:

```rust
// TODO(Arni): getnext_l2_gas_price from the block header.
``` [5](#0-4) 

The `next_l2_gas_price` field is already serialized in the block header and propagated through the P2P layer: [6](#0-5) 

### Proof of Concept

1. Observe the previous block's L2 gas price `P` from the block header and its gas usage `G > gas_target`.
2. Compute `P' = calculate_next_base_gas_price(P, G, gas_target, min_gas_price)` — this gives `P' > P`.
3. Submit a V3 `InvokeTransaction` with `AllResources` bounds where `l2_gas.max_price_per_unit = P` (exactly equal to the previous block price).
4. The gateway calls `validate_tx_l2_gas_price_within_threshold(bounds, P)`: threshold = `100% × P = P`; `P >= P` → **admitted**.
5. The batcher builds the next block with gas price `P'`. `check_fee_bounds` evaluates `P < P'` → `MaxGasPriceTooLow` → **rejected**.
6. The transaction occupies a mempool slot and consumes batcher CPU but is never sequenced. [7](#0-6) [8](#0-7)

### Citations

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L223-243)
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

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L358-390)
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

**File:** crates/apollo_consensus_orchestrator/src/fee_market/mod.rs (L86-140)
```rust
pub fn calculate_next_base_gas_price(
    price: GasPrice,
    gas_used: GasAmount,
    gas_target: GasAmount,
    min_gas_price: GasPrice,
) -> GasPrice {
    let versioned_constants = VersionedConstants::latest_constants();
    assert!(
        gas_target < versioned_constants.max_block_size,
        "Gas target must be lower than max block size."
    );
    assert!(gas_target.0 > 0, "Gas target must be greater than zero.");
    assert!(
        versioned_constants.gas_price_max_change_denominator > 0,
        "Denominator constant must be greater than zero."
    );

    // If the current price is below the minimum, apply a gradual adjustment and return early.
    // This allows the price to increase by at most 1/MIN_GAS_PRICE_INCREASE_DENOMINATOR per block.
    if price < min_gas_price {
        let max_increase = price.0 / MIN_GAS_PRICE_INCREASE_DENOMINATOR;
        let adjusted = price.0 + max_increase;
        // Cap at min_gas_price to avoid overshooting
        let adjusted_price = adjusted.min(min_gas_price.0);
        info!(
            "Fee Market: Price {} below minimum gas price {}, adjusted price: {} )",
            price.0, min_gas_price.0, adjusted_price
        );
        return GasPrice(adjusted_price);
    }

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
}
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L363-367)
```rust
        if self.execution_flags.charge_fee {
            self.check_fee_bounds(tx_context)?;

            verify_can_pay_committed_bounds(state, tx_context).map_err(Box::new)?;
        }
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L440-458)
```rust
                            }
                            if resource_bounds.max_price_per_unit < actual_gas_price.get() {
                                insufficiencies_resource.push(
                                    ResourceBoundsError::MaxGasPriceTooLow {
                                        resource: *resource,
                                        max_gas_price: resource_bounds.max_price_per_unit,
                                        actual_gas_price: (*actual_gas_price).into(),
                                    },
                                );
                            }
                            insufficiencies_resource
                        },
                    )
                    .collect::<Vec<_>>();
                if !insufficiencies.is_empty() {
                    return Err(Box::new(TransactionFeeError::InsufficientResourceBounds {
                        errors: insufficiencies,
                    }))?;
                }
```

**File:** crates/apollo_protobuf/src/converters/header.rs (L175-178)
```rust
        let next_l2_gas_price = u128::from(
            value.next_l2_gas_price.ok_or(missing("SignedBlockHeader::next_l2_gas_price"))?,
        )
        .into();
```
