### Title
Missing `max_l2_gas_amount` Cap Enforcement for `Declare` Transactions Allows Unbounded Gas Admission - (`File: crates/apollo_gateway/src/stateless_transaction_validator.rs`)

---

### Summary

`StatelessTransactionValidator::validate_resource_bounds()` enforces a `max_l2_gas_amount` ceiling on `l2_gas.max_amount` for `Invoke` and `DeployAccount` transactions, but explicitly skips this check for `Declare` transactions. The omission is acknowledged with a TODO comment. A `Declare` transaction with `l2_gas.max_amount = u64::MAX` passes gateway admission, enters the mempool, and is handed to the blockifier with an effectively unbounded initial Sierra gas limit, bypassing the admission control that the cap is designed to enforce.

---

### Finding Description

In `validate_resource_bounds()`, the check reads:

```rust
// TODO(Arni): Consider adding a validation for max_l2_gas_amount for declare.
if let RpcTransaction::Declare(_) = tx {
} else if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
    return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh { … });
}
```

The empty `if let RpcTransaction::Declare(_) = tx {}` branch is a deliberate no-op: any `l2_gas.max_amount` value, including `u64::MAX`, passes for `Declare` transactions. [1](#0-0) 

The production default for `max_l2_gas_amount` is `1_200_000_000` (1.2 billion gas units). [2](#0-1) 

The test `valid_l2_gas_amount_on_declare` explicitly documents and asserts this bypass: a `Declare` transaction with `max_amount = GasAmount(200)` passes validation when `max_l2_gas_amount = 100`, confirming the gap is not accidental. [3](#0-2) 

By contrast, the same over-limit amount on `Invoke` or `DeployAccount` is rejected with `MaxGasAmountTooHigh`. [4](#0-3) 

Once admitted, `TransactionContext::initial_sierra_gas()` returns `l2_gas.max_amount` verbatim for `AllResources` transactions. A `Declare` transaction carrying `max_amount = u64::MAX` therefore receives `GasAmount(u64::MAX)` as its execution gas budget, which is then used to derive the VM step limit via `max_amount.0.saturating_div(l2_gas_per_step)`, capped only by the block-level global step bound. [5](#0-4) [6](#0-5) 

---

### Impact Explanation

**High — Mempool/gateway/RPC admission accepts invalid transactions before sequencing.**

Any unprivileged user can craft a `Declare` V3 transaction with `l2_gas.max_amount` set to any value above `max_l2_gas_amount` (up to `u64::MAX`). The gateway's stateless validator passes it without error. The transaction enters the mempool and is forwarded to the batcher. Inside the blockifier, `initial_sierra_gas()` hands the `__validate_declare__` entry point a gas budget equal to the attacker-supplied `max_amount`, not the operator-configured ceiling. The only downstream guard is the block-level step cap, which is a coarser bound than the per-transaction `max_l2_gas_amount` policy.

This breaks the admission invariant that `max_l2_gas_amount` is supposed to enforce uniformly across all transaction types, and it allows `Declare` transactions to consume disproportionate validation-phase resources relative to what the operator intended to permit.

---

### Likelihood Explanation

Exploitation requires only constructing a standard `Declare` V3 transaction with an oversized `l2_gas.max_amount` field. No privileged access, special account state, or network position is needed. The bypass is unconditional and deterministic.

---

### Recommendation

Remove the empty `if let RpcTransaction::Declare(_) = tx {}` branch and apply the same `max_l2_gas_amount` guard to all transaction types:

```rust
if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
    return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh {
        gas_amount: resource_bounds.l2_gas.max_amount,
        max_gas_amount: self.config.max_l2_gas_amount,
    });
}
```

If `Declare` transactions legitimately require a higher gas ceiling (e.g., for compilation), introduce a separate `max_l2_gas_amount_declare` config field with an explicit, documented value rather than leaving the bound entirely absent.

---

### Proof of Concept

1. Construct a valid `RpcDeclareTransaction::V3` with:
   - `resource_bounds.l2_gas.max_amount = GasAmount(u64::MAX)`
   - `resource_bounds.l2_gas.max_price_per_unit = GasPrice(min_gas_price)` (satisfies the price floor)
   - Any valid Sierra contract class

2. Submit to the gateway's `StatelessTransactionValidator::validate()`.

3. Observe: validation returns `Ok(())` despite `u64::MAX >> max_l2_gas_amount` (1.2 billion). [7](#0-6) 

4. The transaction is admitted to the mempool. When the batcher executes it, `initial_sierra_gas()` returns `GasAmount(u64::MAX)`, giving the `__validate_declare__` entry point a gas budget orders of magnitude above the operator-configured limit. [8](#0-7)

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

**File:** crates/apollo_gateway_config/src/config.rs (L139-155)
```rust
impl Default for StatelessTransactionValidatorConfig {
    fn default() -> Self {
        StatelessTransactionValidatorConfig {
            validate_resource_bounds: true,
            min_gas_price: 8_000_000_000,
            max_l2_gas_amount: 1_200_000_000,
            max_calldata_length: 5000,
            max_signature_length: 4000,
            max_contract_bytecode_size: 81920,
            max_contract_class_object_size: 4089446,
            min_sierra_version: VersionId::new(1, 1, 0),
            max_sierra_version: VersionId::new(1, 8, usize::MAX),
            allow_client_side_proving: false,
            max_proof_size: 480000,
        }
    }
}
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

**File:** crates/blockifier/src/context.rs (L54-72)
```rust
    pub fn initial_sierra_gas(&self) -> GasAmount {
        match &self.tx_info {
            TransactionInfo::Deprecated(_)
            | TransactionInfo::Current(CurrentTransactionInfo {
                resource_bounds: ValidResourceBounds::L1Gas(_),
                ..
            }) => self.block_context.versioned_constants.initial_gas_no_user_l2_bound(),
            TransactionInfo::Current(CurrentTransactionInfo {
                resource_bounds: ValidResourceBounds::AllResources(AllResourceBounds { l2_gas, .. }),
                ..
            }) => {
                #[cfg(feature = "reexecution")]
                if self.block_context.versioned_constants.ignore_user_l2_gas_bound {
                    return self.block_context.versioned_constants.initial_gas_no_user_l2_bound();
                }
                l2_gas.max_amount
            }
        }
    }
```

**File:** crates/blockifier/src/execution/entry_point.rs (L433-444)
```rust
                ValidResourceBounds::AllResources(AllResourceBounds {
                    l2_gas: ResourceBounds { max_amount, .. },
                    ..
                }) => {
                    if l2_gas_per_step.is_zero() {
                        u64::MAX
                    } else {
                        max_amount.0.saturating_div(l2_gas_per_step)
                    }
                }
            },
        };
```
