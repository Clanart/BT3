### Title
Gateway `validate_resource_bounds` Uses Stale `l2_gas_price` Instead of `next_l2_gas_price`, Causing Valid Transactions to Be Rejected or Invalid Transactions Accepted — (File: `crates/apollo_gateway/src/stateful_transaction_validator.rs`)

### Summary

`StatefulTransactionValidator::validate_resource_bounds` computes its admission threshold from the **current** block's `l2_gas_price` (block N), but the transaction will actually execute in block N+1 whose price is `next_l2_gas_price` — a distinct value stored in block N's header. Because the two prices can diverge by up to the EIP-1559 per-block cap, the gateway either rejects transactions that are valid for the next block or admits transactions that will revert in it. A developer-acknowledged TODO comment in the source confirms the wrong field is being read.

### Finding Description

`validate_resource_bounds` reads the L2 gas price threshold from `gateway_fixed_block_state_reader.get_block_info()`:

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

`GatewayFixedBlockSyncStateClient::get_block_info_from_sync_client` constructs a `BlockInfo` that exposes only `l2_gas_price` (the price **of** block N), not `next_l2_gas_price` (the price **for** block N+1):

```rust
strk_gas_prices: GasPriceVector {
    l2_gas_price: block_header.l2_gas_price.price_in_fri.try_into()?,
    ...
},
``` [2](#0-1) 

`next_l2_gas_price` is a separate field in `BlockHeaderWithoutHash`, computed by `update_l2_gas_price` and stored in the block header by `update_state_sync_with_new_block`:

```rust
next_l2_gas_price: self.l2_gas_price,
``` [3](#0-2) 

The threshold check in `validate_tx_l2_gas_price_within_threshold` is:

```rust
let threshold = (gas_price_threshold_multiplier
    * previous_block_l2_gas_price.get().0)
    .to_integer();
if tx_l2_gas_price.0 < threshold {
    return Err(StarknetError { code: GAS_PRICE_TOO_LOW, ... });
}
``` [4](#0-3) 

The `SyncStateReaderFactory` creates a fresh `GatewayFixedBlockSyncStateClient` pinned to `latest_block_number` (block N) for every transaction:

```rust
let gateway_fixed_block_sync_state_client = GatewayFixedBlockSyncStateClient::new(
    self.shared_state_sync_client.clone(),
    latest_block_number,
);
``` [5](#0-4) 

The `next_l2_gas_price` for block N+1 is derived from block N's gas usage via `calculate_next_l2_gas_price_for_fin` / `calculate_next_base_gas_price` (EIP-1559 formula), which can move the price up or down by up to `gas_delta / (gas_target * denominator)` per block: [6](#0-5) 

### Impact Explanation

Two admission errors arise:

**Case A — valid transaction rejected.** When block N+1's `next_l2_gas_price` is lower than block N's `l2_gas_price` (price decreased), a user who sets `max_price_per_unit` to exactly the next block's price passes the execution check but fails the gateway threshold (which is still anchored to the higher block-N price). The gateway returns `GAS_PRICE_TOO_LOW` and the transaction never reaches the mempool, even though it would have executed successfully.

**Case B — invalid transaction admitted.** When block N+1's `next_l2_gas_price` is higher than block N's `l2_gas_price` (price increased due to congestion), a transaction with `max_price_per_unit` between the two prices passes the gateway threshold (anchored to the lower block-N price) but will revert during execution because the actual block price exceeds the user's declared maximum. The gateway admits a transaction that is guaranteed to fail.

Both cases match the allowed impact: **"High. Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing."**

### Likelihood Explanation

The L2 gas price adjusts every block via EIP-1559. Any block with gas usage above or below the target shifts the price, making the divergence between `l2_gas_price` (block N) and `next_l2_gas_price` (block N+1) a routine occurrence rather than an edge case. No special privilege is required — any user submitting a v3 (`AllResources`) transaction with an `l2_gas` bound near the threshold can trigger either case. The developer TODO comment confirms the issue is known and unresolved.

### Recommendation

Replace the read of `l2_gas_price` with `next_l2_gas_price` from the block header. Concretely:

1. Expose `next_l2_gas_price` through `GatewayFixedBlockStateReader::get_block_info` (or add a dedicated `get_next_l2_gas_price` method).
2. In `GatewayFixedBlockSyncStateClient::get_block_info_from_sync_client`, populate the returned value from `block_header.next_l2_gas_price` instead of `block_header.l2_gas_price`.
3. Update `validate_resource_bounds` to use the next-block price as the threshold base, resolving the TODO.

### Proof of Concept

1. Block N is finalized with `l2_gas_price = 100 fri` and `next_l2_gas_price = 90 fri` (low-congestion block, price decreased).
2. `min_gas_price_percentage = 50`, so the gateway threshold = `50% × 100 = 50 fri`.
3. User submits a v3 transaction with `l2_gas.max_price_per_unit = 90 fri` (exactly the next block's price — valid for execution).
4. `validate_resource_bounds` reads `previous_block_l2_gas_price = 100 fri`, computes `threshold = 50 fri`, and since `90 >= 50` the check passes — **but** the threshold is wrong.
5. Now consider the symmetric case: `next_l2_gas_price = 110 fri`. User sets `max_price_per_unit = 60 fri` (above the 50-fri threshold, passes gateway). Block N+1 executes at 110 fri > 60 fri → transaction reverts. Gateway admitted an invalid transaction. [1](#0-0) [7](#0-6) [8](#0-7)

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

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L339-355)
```rust
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
```

**File:** crates/apollo_gateway/src/gateway_fixed_block_state_reader.rs (L25-73)
```rust
pub struct GatewayFixedBlockSyncStateClient {
    state_sync_client: Arc<dyn StateSyncClient>,
    block_number: BlockNumber,
    block_info_cache: OnceCell<BlockInfo>,
}

impl GatewayFixedBlockSyncStateClient {
    pub fn new(state_sync_client: SharedStateSyncClient, block_number: BlockNumber) -> Self {
        Self { state_sync_client, block_number, block_info_cache: OnceCell::new() }
    }

    async fn get_block_info_from_sync_client(&self) -> StarknetResult<BlockInfo> {
        let block = self.state_sync_client.get_block(self.block_number).await.map_err(|e| {
            StarknetError::internal_with_logging("Failed to get latest block info", e)
        })?;

        let block_header = block.block_header_without_hash;
        let block_info = BlockInfo {
            block_number: block_header.block_number,
            block_timestamp: block_header.timestamp,
            sequencer_address: block_header.sequencer.0,
            gas_prices: GasPrices {
                eth_gas_prices: GasPriceVector {
                    l1_gas_price: block_header.l1_gas_price.price_in_wei.try_into()?,
                    l1_data_gas_price: block_header.l1_data_gas_price.price_in_wei.try_into()?,
                    l2_gas_price: block_header.l2_gas_price.price_in_wei.try_into()?,
                },
                strk_gas_prices: GasPriceVector {
                    l1_gas_price: block_header.l1_gas_price.price_in_fri.try_into()?,
                    l1_data_gas_price: block_header.l1_data_gas_price.price_in_fri.try_into()?,
                    l2_gas_price: block_header.l2_gas_price.price_in_fri.try_into()?,
                },
            },
            use_kzg_da: block_header.l1_da_mode.is_use_kzg_da(),
            starknet_version: block_header.starknet_version,
        };

        Ok(block_info)
    }
}

#[async_trait]
impl GatewayFixedBlockStateReader for GatewayFixedBlockSyncStateClient {
    async fn get_block_info(&self) -> StarknetResult<BlockInfo> {
        self.block_info_cache
            .get_or_try_init(|| self.get_block_info_from_sync_client())
            .await
            .cloned()
    }
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

**File:** crates/apollo_gateway/src/sync_state_reader.rs (L541-544)
```rust
        let gateway_fixed_block_sync_state_client = GatewayFixedBlockSyncStateClient::new(
            self.shared_state_sync_client.clone(),
            latest_block_number,
        );
```

**File:** crates/apollo_consensus_orchestrator/src/fee_market/mod.rs (L83-138)
```rust
pub fn calculate_next_base_gas_price(
    price: GasPrice,
    gas_used: GasAmount,
    gas_target: GasAmount,
    min_gas_price: GasPrice,
) -> GasPrice {
    let versioned_constants =
        orchestrator_versioned_constants::VersionedConstants::latest_constants();
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
