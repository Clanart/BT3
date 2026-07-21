### Title
Gateway Unconditionally Applies L2 Gas Price Check to Optional `l2_gas` Field, Incorrectly Rejecting Valid Transactions - (File: `crates/apollo_gateway/src/stateless_transaction_validator.rs`)

### Summary

`StatelessTransactionValidator::validate_resource_bounds` first permits transactions whose only non-zero resource bound is `l1_gas` (the `ZeroResourceBounds` guard passes), then unconditionally applies a `min_gas_price` threshold to `l2_gas.max_price_per_unit`. Because `l2_gas` is optional, a transaction that legitimately sets only `l1_gas` has `l2_gas.max_price_per_unit = 0`, which is always below the production threshold of `8_000_000_000`, causing the gateway to reject a structurally valid transaction before it ever reaches the mempool.

### Finding Description

`validate_resource_bounds` performs two sequential checks:

**Check 1 – `ZeroResourceBounds`** (line 66): accepts any transaction where at least one resource bound is non-zero. A transaction with `l1_gas = NON_EMPTY_RESOURCE_BOUNDS` and `l2_gas = Default::default()` passes this guard because `max_possible_fee > 0`. [1](#0-0) 

**Check 2 – `min_gas_price`** (line 71): unconditionally compares `l2_gas.max_price_per_unit` against `self.config.min_gas_price`. When `l2_gas` is unused, `l2_gas.max_price_per_unit = 0`. In production the threshold is `8_000_000_000`, so `0 < 8_000_000_000` is `true` and the transaction is rejected with `MaxGasPriceTooLow`. [2](#0-1) 

The production gateway config confirms the non-zero threshold: [3](#0-2) 

The unit test `valid_l1_gas` demonstrates that the protocol considers such a transaction valid, but it uses `min_gas_price: 0` in the test config, masking the production failure: [4](#0-3) 

By contrast, the `max_l2_gas_amount` check on the same field is correctly guarded — it only fires when `l2_gas.max_amount > 0` — making the unconditional `min_gas_price` check the outlier: [5](#0-4) 

### Impact Explanation

Any user submitting a V3 transaction that allocates budget only to `l1_gas` (a protocol-valid configuration) will have that transaction silently rejected at the gateway with `MaxGasPriceTooLow` before it reaches the mempool or batcher. This matches the allowed High impact: **"Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing."**

### Likelihood Explanation

The condition is deterministic and configuration-driven. With the default production `min_gas_price = 8_000_000_000` and `validate_resource_bounds = true`, every transaction that omits `l2_gas` (sets it to the zero default) triggers the rejection. Any user or integration that constructs a transaction with only `l1_gas` bounds will be affected.

### Recommendation

Guard the `min_gas_price` check so it is only applied when `l2_gas` is actually in use:

```rust
if resource_bounds.l2_gas.max_amount.0 > 0
    && resource_bounds.l2_gas.max_price_per_unit.0 < self.config.min_gas_price
{
    return Err(StatelessTransactionValidatorError::MaxGasPriceTooLow { ... });
}
```

This mirrors the pattern already used for `max_l2_gas_amount` and is consistent with the intent of `ZeroResourceBounds` (at least one bound must be non-zero, not necessarily `l2_gas`).

### Proof of Concept

1. Construct a V3 `Invoke` transaction with:
   - `l1_gas = ResourceBounds { max_amount: GasAmount(1000), max_price_per_unit: GasPrice(1_000_000_000) }`
   - `l2_gas = ResourceBounds::default()` (all zeros)
   - `l1_data_gas = ResourceBounds::default()`
2. Submit to the gateway running with the production config (`min_gas_price = 8_000_000_000`, `validate_resource_bounds = true`).
3. **Expected (correct)**: transaction admitted; `max_possible_fee > 0`, `l2_gas` not used.
4. **Actual**: gateway returns `MaxGasPriceTooLow { gas_price: GasPrice(0), min_gas_price: 8000000000 }` because `l2_gas.max_price_per_unit = 0 < 8_000_000_000`.

The existing test `valid_l1_gas` already encodes the correct expectation but only passes because the test config sets `min_gas_price: 0`, hiding the production regression. [6](#0-5)

### Citations

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L64-69)
```rust
        let resource_bounds = *tx.resource_bounds();
        // The resource bounds should be positive even without the tip.
        if ValidResourceBounds::AllResources(resource_bounds).max_possible_fee(Tip::ZERO) == Fee(0)
        {
            return Err(StatelessTransactionValidatorError::ZeroResourceBounds { resource_bounds });
        }
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L71-76)
```rust
        if resource_bounds.l2_gas.max_price_per_unit.0 < self.config.min_gas_price {
            return Err(StatelessTransactionValidatorError::MaxGasPriceTooLow {
                gas_price: resource_bounds.l2_gas.max_price_per_unit,
                min_gas_price: self.config.min_gas_price,
            });
        }
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L79-85)
```rust
        if let RpcTransaction::Declare(_) = tx {
        } else if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
            return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh {
                gas_amount: resource_bounds.l2_gas.max_amount,
                max_gas_amount: self.config.max_l2_gas_amount,
            });
        }
```

**File:** crates/apollo_deployments/resources/app_configs/gateway_config.json (L30-30)
```json
  "gateway_config.static_config.stateless_tx_validator_config.min_gas_price": 8000000000,
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator_test.rs (L54-67)
```rust
static DEFAULT_VALIDATOR_CONFIG_FOR_TESTING: LazyLock<StatelessTransactionValidatorConfig> =
    LazyLock::new(|| StatelessTransactionValidatorConfig {
        validate_resource_bounds: false,
        min_gas_price: 0,
        max_l2_gas_amount: 1_000_000_000,
        max_calldata_length: 10,
        max_signature_length: 1,
        max_proof_size: 10,
        max_contract_bytecode_size: 100_000,
        max_contract_class_object_size: 100_000,
        min_sierra_version: *MIN_SIERRA_VERSION,
        max_sierra_version: *MAX_SIERRA_VERSION,
        allow_client_side_proving: true,
    });
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator_test.rs (L70-82)
```rust
#[case::valid_l1_gas(
    StatelessTransactionValidatorConfig {
        validate_resource_bounds: true,
        ..*DEFAULT_VALIDATOR_CONFIG_FOR_TESTING
    },
    RpcTransactionArgs {
        resource_bounds: AllResourceBounds {
            l1_gas: NON_EMPTY_RESOURCE_BOUNDS,
            ..Default::default()
        },
        ..Default::default()
    }
)]
```
