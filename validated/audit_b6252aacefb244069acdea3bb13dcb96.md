### Title
`max_l2_gas_amount` Cap Unenforced for Declare Transactions Allows Oversized L2 Gas Bounds Through Gateway Admission - (`File: crates/apollo_gateway/src/stateless_transaction_validator.rs`)

### Summary

`StatelessTransactionValidatorConfig::max_l2_gas_amount` is defined as a per-transaction upper bound on `l2_gas.max_amount` and is enforced for `Invoke` and `DeployAccount` transactions, but is explicitly skipped for `Declare` transactions via a deliberate early-return branch with a `TODO` comment. This is the direct sequencer analog of the `maxMembers` bug: a cap is declared, partially enforced, and silently absent for one transaction class, allowing that class to bypass the intended admission gate.

### Finding Description

In `StatelessTransactionValidator::validate_resource_bounds`, the check against `config.max_l2_gas_amount` is guarded by a type-dispatch that short-circuits for `Declare`:

```rust
// TODO(Arni): Consider adding a validation for max_l2_gas_amount for declare.
if let RpcTransaction::Declare(_) = tx {
} else if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
    return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh { … });
}
```

The production default for `max_l2_gas_amount` is `1,210,000,000` gas units. A `Declare` transaction carrying `l2_gas.max_amount = u64::MAX` (or any value above the limit) passes `validate_resource_bounds` without error, is forwarded to the mempool, and is subsequently executed by the blockifier with that inflated gas ceiling as its enforced limit. The test `valid_l2_gas_amount_on_declare` in `stateless_transaction_validator_test.rs` explicitly asserts this bypass as expected behavior. [1](#0-0) 

The config field and its production value are: [2](#0-1) 

The test that documents the bypass: [3](#0-2) 

The enforcement path that works correctly for non-Declare types: [4](#0-3) 

### Impact Explanation

A `Declare` transaction with `l2_gas.max_amount` set above `max_l2_gas_amount` (e.g., `u64::MAX`) is admitted by the gateway and mempool. The blockifier then executes it with that inflated gas ceiling as the enforced L2 gas limit. If the transaction's actual gas consumption falls between `max_l2_gas_amount` and the inflated bound, the transaction succeeds and is included in a block — an outcome the gateway's admission control was designed to prevent. The block-level bouncer (`sierra_gas`, `proving_gas`) still applies, but the per-transaction admission gate is bypassed entirely for this transaction type.

This matches the **High** impact scope: *Mempool/gateway/RPC admission accepts invalid transactions before sequencing.*

### Likelihood Explanation

Any unprivileged user can submit a `Declare` transaction via the public RPC endpoint with an arbitrarily large `l2_gas.max_amount`. No special privilege, key, or peer relationship is required. The bypass is unconditional for all `Declare` transactions regardless of the configured limit value.

### Recommendation

Remove the `if let RpcTransaction::Declare(_) = tx { }` early-return branch and apply the same `max_l2_gas_amount` check uniformly to all transaction types, or introduce a separate `max_l2_gas_amount_declare` config field if a different limit is intentionally desired for declare transactions. The TODO comment should be resolved rather than left as a silent bypass.

### Proof of Concept

The existing test already demonstrates the bypass. To make it explicit:

```rust
#[test]
fn declare_bypasses_max_l2_gas_amount() {
    let config = StatelessTransactionValidatorConfig {
        validate_resource_bounds: true,
        max_l2_gas_amount: 100, // limit is 100
        ..*DEFAULT_VALIDATOR_CONFIG_FOR_TESTING
    };
    let tx_validator = StatelessTransactionValidator { config };

    // Declare with max_amount = 200 (2x the limit) — passes validation
    let tx = rpc_tx_for_testing(
        TransactionType::Declare,
        RpcTransactionArgs {
            resource_bounds: AllResourceBounds {
                l2_gas: ResourceBounds {
                    max_amount: GasAmount(200),
                    ..NON_EMPTY_RESOURCE_BOUNDS
                },
                ..Default::default()
            },
            ..Default::default()
        },
    );
    assert_matches!(tx_validator.validate(&tx), Ok(())); // succeeds — limit not enforced

    // Same amount on Invoke — correctly rejected
    let tx_invoke = rpc_tx_for_testing(
        TransactionType::Invoke,
        RpcTransactionArgs {
            resource_bounds: AllResourceBounds {
                l2_gas: ResourceBounds {
                    max_amount: GasAmount(200),
                    ..NON_EMPTY_RESOURCE_BOUNDS
                },
                ..Default::default()
            },
            ..Default::default()
        },
    );
    assert_matches!(
        tx_validator.validate(&tx_invoke),
        Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh { .. })
    );
}
``` [5](#0-4) [6](#0-5)

### Citations

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L56-88)
```rust
    fn validate_resource_bounds(
        &self,
        tx: &RpcTransaction,
    ) -> StatelessTransactionValidatorResult<()> {
        if !self.config.validate_resource_bounds {
            return Ok(());
        }

        let resource_bounds = *tx.resource_bounds();
        // The resource bounds should be positive even without the tip.
        if ValidResourceBounds::AllResources(resource_bounds).max_possible_fee(Tip::ZERO) == Fee(0)
        {
            return Err(StatelessTransactionValidatorError::ZeroResourceBounds { resource_bounds });
        }

        if resource_bounds.l2_gas.max_price_per_unit.0 < self.config.min_gas_price {
            return Err(StatelessTransactionValidatorError::MaxGasPriceTooLow {
                gas_price: resource_bounds.l2_gas.max_price_per_unit,
                min_gas_price: self.config.min_gas_price,
            });
        }

        // TODO(Arni): Consider adding a validation for max_l2_gas_amount for declare.
        if let RpcTransaction::Declare(_) = tx {
        } else if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
            return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh {
                gas_amount: resource_bounds.l2_gas.max_amount,
                max_gas_amount: self.config.max_l2_gas_amount,
            });
        }

        Ok(())
    }
```

**File:** crates/apollo_node/resources/config_schema.json (L3172-3176)
```json
  "gateway_config.static_config.stateless_tx_validator_config.max_l2_gas_amount": {
    "description": "Maximum allowed L2 gas amount for transactions.",
    "privacy": "Public",
    "value": 1210000000
  },
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator_test.rs (L173-201)
```rust
#[rstest]
#[case::l2_gas_amount_out_of_limit(
    StatelessTransactionValidatorConfig {
        validate_resource_bounds: true,
        max_l2_gas_amount: 100,
        ..*DEFAULT_VALIDATOR_CONFIG_FOR_TESTING
    },
    RpcTransactionArgs {
        resource_bounds: AllResourceBounds {
            l2_gas: ResourceBounds {
                max_amount: GasAmount(200),
                ..NON_EMPTY_RESOURCE_BOUNDS
            },
            ..Default::default()
        },
        ..Default::default()
    }
)]
fn valid_l2_gas_amount_on_declare(
    #[case] config: StatelessTransactionValidatorConfig,
    #[case] rpc_tx_args: RpcTransactionArgs,
) {
    let tx_type = TransactionType::Declare;
    let tx_validator = StatelessTransactionValidator { config };

    let tx = rpc_tx_for_testing(tx_type, rpc_tx_args);

    assert_matches!(tx_validator.validate(&tx), Ok(()));
}
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator_test.rs (L243-271)
```rust
#[rstest]
#[case::max_l2_gas_amount_too_high(
    RpcTransactionArgs {
        resource_bounds: AllResourceBounds {
            l2_gas: ResourceBounds {
                max_amount: GasAmount(DEFAULT_VALIDATOR_CONFIG.max_l2_gas_amount + 1),
                max_price_per_unit: GasPrice(DEFAULT_VALIDATOR_CONFIG.min_gas_price),
            },
            ..Default::default()
        },
        ..Default::default()
    },
    StatelessTransactionValidatorError::MaxGasAmountTooHigh {
        gas_amount: GasAmount(DEFAULT_VALIDATOR_CONFIG.max_l2_gas_amount + 1),
        max_gas_amount: DEFAULT_VALIDATOR_CONFIG.max_l2_gas_amount
    },
)]
fn test_invalid_max_l2_gas_amount(
    #[case] rpc_tx_args: RpcTransactionArgs,
    #[case] expected_error: StatelessTransactionValidatorError,
    #[values(TransactionType::DeployAccount, TransactionType::Invoke)] tx_type: TransactionType,
) {
    let tx_validator =
        StatelessTransactionValidator { config: DEFAULT_VALIDATOR_CONFIG.to_owned() };

    let tx = rpc_tx_for_testing(tx_type, rpc_tx_args);

    assert_eq!(tx_validator.validate(&tx).unwrap_err(), expected_error);
}
```

**File:** crates/apollo_gateway_config/src/config.rs (L276-300)
```rust
#[derive(Clone, Debug, Serialize, Deserialize, Validate, PartialEq)]
pub struct StatefulTransactionValidatorConfig {
    // If true, ensures the max L2 gas price exceeds (a configurable percentage of) the base gas
    // price of the previous block.
    pub validate_resource_bounds: bool,
    pub max_allowed_nonce_gap: u32,
    pub reject_future_declare_txs: bool,
    pub max_nonce_for_validation_skip: Nonce,
    pub versioned_constants_overrides: Option<VersionedConstantsOverrides>,
    // Minimum gas price as percentage of threshold to accept transactions.
    pub min_gas_price_percentage: u8, // E.g., 80 to require 80% of threshold.
}

impl Default for StatefulTransactionValidatorConfig {
    fn default() -> Self {
        StatefulTransactionValidatorConfig {
            validate_resource_bounds: true,
            max_allowed_nonce_gap: 200,
            reject_future_declare_txs: true,
            max_nonce_for_validation_skip: Nonce(Felt::ONE),
            min_gas_price_percentage: 100,
            versioned_constants_overrides: None,
        }
    }
}
```
