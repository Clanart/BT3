### Title
Integer Division Truncation in `validate_tx_l2_gas_price_within_threshold` Collapses Threshold to Zero, Admitting Transactions with Insufficient L2 Gas Price - (File: crates/apollo_gateway/src/stateful_transaction_validator.rs)

### Summary

The gateway's L2 gas price admission check uses `.to_integer()` (floor/truncating integer division) on a `Ratio<u128>` to compute the minimum acceptable L2 gas price threshold. When `min_gas_price_percentage × previous_block_l2_gas_price < 100`, the threshold collapses to exactly `0`, allowing a transaction with `max_price_per_unit = 0` to pass the check unconditionally.

### Finding Description

In `validate_tx_l2_gas_price_within_threshold`:

```rust
let gas_price_threshold_multiplier =
    Ratio::new(self.config.min_gas_price_percentage.into(), 100_u128);
let threshold = (gas_price_threshold_multiplier
    * previous_block_l2_gas_price.get().0)
    .to_integer();
if tx_l2_gas_price.0 < threshold {
    return Err(...)
}
```

`Ratio::to_integer()` returns `numer / denom` — integer (floor) division. The threshold is therefore `⌊(min_gas_price_percentage × price) / 100⌋`.

**Truncation to zero:** Whenever `min_gas_price_percentage × previous_block_l2_gas_price < 100`, the numerator of the ratio is less than the denominator, so `to_integer()` returns `0`. The guard `tx_l2_gas_price.0 < 0` is always `false` for a `u128`, so **any** transaction — including one with `max_price_per_unit = 0` — passes the check.

Concrete example:
- `min_gas_price_percentage = 1`, `previous_block_l2_gas_price = 99`
- Exact threshold = 0.99 → intended to reject price-0 transactions
- Computed threshold = `⌊(1 × 99) / 100⌋ = ⌊0.99⌋ = 0`
- A transaction with `tx_l2_gas_price = 0` satisfies `0 < 0 == false` → **admitted**

The existing test suite only covers the case `previous_block_l2_gas_price = 100` with `min_gas_price_percentage = 1`, which gives threshold = 1 (no truncation to zero), masking the edge case. [1](#0-0) 

### Impact Explanation

A transaction admitted with `max_price_per_unit = 0` has `enforce_fee() = false`. The blockifier skips fee enforcement for such transactions, meaning they execute without paying any L2 gas fee. This constitutes the gateway accepting an economically invalid transaction — matching the **High** impact: *"Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing."*

### Likelihood Explanation

The condition `min_gas_price_percentage × previous_block_l2_gas_price < 100` is reachable when:
- The L2 gas price is near its minimum (e.g., during low-activity periods or at chain genesis), **and**
- `min_gas_price_percentage` is a small integer (e.g., 1–10%).

The EIP-1559-style fee market (`calculate_next_base_gas_price`) can drive the L2 gas price toward `min_gas_price` over time. If `min_gas_price` is configured below `100 / min_gas_price_percentage`, the window opens. The attacker needs no special privilege — any user can submit a V3 transaction with `AllResourceBounds` and `l2_gas.max_price_per_unit = 0`. [2](#0-1) 

### Recommendation

Replace floor division with ceiling division for the threshold computation, consistent with how the rest of the codebase handles gas-price rounding (e.g., `checked_div_ceil` in `to_discounted_l1_gas`):

```rust
// Before (floor):
let threshold = (gas_price_threshold_multiplier
    * previous_block_l2_gas_price.get().0)
    .to_integer();

// After (ceiling):
let threshold = (gas_price_threshold_multiplier
    * previous_block_l2_gas_price.get().0)
    .ceil()
    .to_integer();
```

`Ratio::ceil()` returns `⌈numer / denom⌉`, ensuring the threshold is at least 1 whenever `min_gas_price_percentage > 0` and `previous_block_l2_gas_price > 0`, which preserves the intended invariant. [3](#0-2) 

### Proof of Concept

```
min_gas_price_percentage = 1   (1%)
previous_block_l2_gas_price   = 99 FRI

threshold (floor) = ⌊(1 × 99) / 100⌋ = 0
threshold (ceil)  = ⌈(1 × 99) / 100⌉ = 1

Attacker submits AllResourceBounds tx with l2_gas.max_price_per_unit = 0:
  check: 0 < 0  → false  → tx ADMITTED  (wrong)
  check: 0 < 1  → true   → tx REJECTED  (correct, after fix)

Result: tx executes with enforce_fee() = false, paying zero L2 gas fee.
``` [4](#0-3)

### Citations

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

**File:** crates/starknet_api/src/execution_resources.rs (L214-220)
```rust
    let l1_data_gas_in_l1_gas_units =
        l1_data_gas_fee.checked_div_ceil(l1_gas_price).unwrap_or_else(|| {
            panic!(
                "Discounted L1 gas cost overflowed: division of L1 data fee ({l1_data_gas_fee}) \
                 by regular L1 gas price ({l1_gas_price}) resulted in overflow."
            );
        });
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator_test.rs (L226-284)
```rust
#[rstest]
#[case::tx_gas_price_meets_threshold_exactly_pass(
    100_u128.try_into().unwrap(),
    100,
    100_u128.into(),
    Ok(())
)]
#[case::tx_gas_price_below_threshold_fail(
    100_u128.try_into().unwrap(),
    100,
    99_u128.into(),
    Err(StarknetError {
        code: StarknetErrorCode::UnknownErrorCode(
            "StarknetErrorCode.GAS_PRICE_TOO_LOW".to_string(),
        ),
        message: "Transaction L2 gas price 99 is below the required threshold 100.".to_string(),
    })
)]
#[case::tx_gas_price_meets_threshold_with_factor_pass(
    100_u128.try_into().unwrap(),
    50,
    50_u128.into(),
    Ok(())
)]
#[case::tx_gas_price_above_threshold_with_factor_pass(
    100_u128.try_into().unwrap(),
    50,
    51_u128.into(),
    Ok(())
)]
#[case::tx_gas_price_below_threshold_with_factor_fail(
    100_u128.try_into().unwrap(),
    50,
    49_u128.into(),
    Err(StarknetError {
        code: StarknetErrorCode::UnknownErrorCode(
            "StarknetErrorCode.GAS_PRICE_TOO_LOW".to_string(),
        ),
        message: "Transaction L2 gas price 49 is below the required threshold 50.".to_string(),
    })
)]
#[case::gas_price_check_disabled_when_percentage_zero_pass(
    100_u128.try_into().unwrap(),
    0,
    0_u128.into(),
    Ok(()),
)]
#[case::tx_gas_price_zero_fails_when_percentage_nonzero_fail(
    100_u128.try_into().unwrap(),
    10,
    0_u128.into(),
    Err(StarknetError {
        code: StarknetErrorCode::UnknownErrorCode(
            "StarknetErrorCode.GAS_PRICE_TOO_LOW".to_string(),
        ),
        message: "Transaction L2 gas price 0 is below the required threshold 10.".to_string(),
    })
)]
#[tokio::test]
```
