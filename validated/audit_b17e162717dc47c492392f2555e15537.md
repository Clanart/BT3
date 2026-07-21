### Title
Gateway admission validates only L2 gas price threshold, silently admitting transactions with under-priced `l1_data_gas` that fail at execution — (File: `crates/apollo_gateway/src/stateful_transaction_validator.rs`)

### Summary

The gateway's `validate_resource_bounds` function enforces a minimum-price threshold only for the L2 gas component of `AllResourceBounds`. The L1 gas price and L1 data gas price are never checked against any threshold at admission time. An explicit `TODO` comment acknowledges this gap. Because L1 data gas prices (Ethereum blob fees) are highly volatile, transactions admitted with `l1_data_gas.max_price_per_unit` exactly at the current price will fail at execution whenever the blob fee rises between admission and sequencing, causing users to be charged fees for reverted transactions.

### Finding Description

**Admission path — only L2 gas price is threshold-checked**

`validate_resource_bounds` in the stateful validator reads the previous block's L2 gas price and rejects any transaction whose `l2_gas.max_price_per_unit` falls below `min_gas_price_percentage% × previous_block_l2_gas_price`. The function contains an explicit developer note acknowledging the gap:

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
            // ... threshold check for L2 only ...
        }
        ValidResourceBounds::L1Gas(_) => {
            // No validation required for legacy transactions.
        }
    }
    Ok(())
}
``` [1](#0-0) 

The stateless validator's `validate_resource_bounds` mirrors this: it checks `l2_gas.max_price_per_unit` against `min_gas_price` but performs no analogous check for `l1_gas` or `l1_data_gas`:

```rust
if resource_bounds.l2_gas.max_price_per_unit.0 < self.config.min_gas_price {
    return Err(StatelessTransactionValidatorError::MaxGasPriceTooLow { ... });
}
``` [2](#0-1) 

**Blockifier pre-validation uses current-block prices, not execution-block prices**

`run_validate_entry_point` builds a `BlockContext` from the *current* block's `block_info` (with `block_number` incremented by one) and calls `blockifier_validator.validate(account_tx)`, which internally calls `perform_pre_validation_stage`. That function checks all three gas prices against the current block's prices — but only when `charge_fee` is true:

```rust
if self.execution_flags.charge_fee {
    self.check_fee_bounds(tx_context)?;
    verify_can_pay_committed_bounds(state, tx_context).map_err(Box::new)?;
}
``` [3](#0-2) 

`check_fee_bounds` rejects a transaction only when `max_price_per_unit < actual_gas_price`. A transaction with `l1_data_gas.max_price_per_unit` exactly equal to the current blob fee passes this check. [4](#0-3) 

**Execution uses a different (potentially higher) gas price**

The batcher derives gas prices for each new block from the `L1GasPriceProvider`, which computes a rolling mean of recent L1 block headers:

```rust
let price_info_out = price_info_summed
    .checked_div(actual_number_of_blocks)
    .expect("Actual number of blocks should be non-zero");
``` [5](#0-4) 

The scraper feeds `blob_fee` (L1 data gas price) directly from L1 block headers:

```rust
let price_info = PriceInfo {
    base_fee_per_gas: GasPrice(header.base_fee_per_gas),
    blob_fee: GasPrice(header.blob_fee),
};
``` [6](#0-5) 

Ethereum blob fees are highly volatile (they can change by orders of magnitude between consecutive blocks). A transaction admitted with `l1_data_gas.max_price_per_unit = current_blob_fee` will fail at execution with `MaxGasPriceTooLow` for `L1DataGas` whenever the blob fee rises before the batcher seals the block.

### Impact Explanation

A transaction that passes gateway admission but fails at execution is an **invalid transaction accepted by the mempool/gateway before sequencing**. The user is charged a fee for the reverted transaction. Because the L2 gas price has a protective threshold (`min_gas_price_percentage`) but L1DataGas does not, the system provides asymmetric protection: L2-gas-price spikes are buffered, but L1-data-gas-price spikes are not. This matches the allowed High impact: *"Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing."*

### Likelihood Explanation

Ethereum blob fees (EIP-4844) are highly volatile. They can increase by 12.5% per block under the EIP-4844 fee market, and can spike dramatically during periods of high L2 activity. Any transaction admitted with `l1_data_gas.max_price_per_unit` at exactly the current blob fee is at risk of failing in the very next block. The developer TODO comment confirms this gap is known but unresolved.

### Recommendation

Extend `validate_tx_l2_gas_price_within_threshold` (or introduce a parallel function) to apply the same `min_gas_price_percentage` threshold to `l1_gas.max_price_per_unit` and `l1_data_gas.max_price_per_unit`, using the previous block's L1 gas price and blob fee respectively. This mirrors the existing L2 gas price protection and ensures admitted transactions have a margin against short-term price increases.

### Proof of Concept

1. Read the current block's `l1_data_gas_price` (blob fee) from the latest block header, call it `P`.
2. Submit an invoke transaction (V3, `AllResources`) with:
   - `l2_gas.max_price_per_unit` ≥ threshold (passes `validate_tx_l2_gas_price_within_threshold`)
   - `l1_data_gas.max_price_per_unit = P` (passes blockifier pre-validation against current block)
   - `l1_gas.max_price_per_unit = current_l1_gas_price`
3. The transaction passes both the stateless validator and the stateful validator (including `run_validate_entry_point`) and is admitted to the mempool.
4. In the next Starknet block, the batcher queries `L1GasPriceProvider::get_price_info` and receives a blob fee `P' > P` (blob fees rose).
5. The batcher executes the transaction; `check_fee_bounds` finds `l1_data_gas.max_price_per_unit (P) < actual_l1_data_gas_price (P')` and the transaction is reverted with `MaxGasPriceTooLow` for `L1DataGas`.
6. The user is charged a fee for the reverted transaction, despite the transaction having passed all gateway admission checks.

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

**File:** crates/blockifier/src/transaction/account_transaction.rs (L349-368)
```rust
    // Performs static checks before executing validation entry point.
    // Note that nonce is incremented during these checks.
    pub fn perform_pre_validation_stage<S: State + StateReader>(
        &self,
        state: &mut S,
        tx_context: &TransactionContext,
    ) -> TransactionPreValidationResult<()> {
        let tx_info = &tx_context.tx_info;
        Self::handle_nonce(state, tx_info, self.execution_flags.strict_nonce_check)?;

        if self.execution_flags.charge_fee {
            self.check_fee_bounds(tx_context)?;

            verify_can_pay_committed_bounds(state, tx_context).map_err(Box::new)?;
        }

        self.validate_proof_facts(&tx_context.block_context, state)?;

        Ok(())
    }
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L437-445)
```rust
                            if resource_bounds.max_price_per_unit < actual_gas_price.get() {
                                insufficiencies_resource.push(
                                    ResourceBoundsError::MaxGasPriceTooLow {
                                        resource: *resource,
                                        max_gas_price: resource_bounds.max_price_per_unit,
                                        actual_gas_price: (*actual_gas_price).into(),
                                    },
                                );
                            }
```

**File:** crates/apollo_l1_gas_price/src/l1_gas_price_provider.rs (L171-184)
```rust
        let price_info_out = price_info_summed
            .checked_div(actual_number_of_blocks)
            .expect("Actual number of blocks should be non-zero");
        info_every_n_ms!(
            1_000,
            "Calculated L1 gas price for timestamp {}: {:?} (based on blocks {}-{}, inclusive)",
            timestamp.0,
            price_info_out,
            samples[first_index].block_number,
            samples[last_index - 1].block_number,
        );
        L1_GAS_PRICE_LATEST_MEAN_VALUE.set_lossy(price_info_out.base_fee_per_gas.0);
        L1_DATA_GAS_PRICE_LATEST_MEAN_VALUE.set_lossy(price_info_out.blob_fee.0);
        Ok(price_info_out)
```

**File:** crates/apollo_l1_gas_price/src/l1_gas_price_scraper.rs (L139-142)
```rust
            let price_info = PriceInfo {
                base_fee_per_gas: GasPrice(header.base_fee_per_gas),
                blob_fee: GasPrice(header.blob_fee),
            };
```
