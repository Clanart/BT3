### Title
Gateway `validate_resource_bounds` uses committed-block L2 gas price while ignoring pending block gas price, producing wrong admission decisions - (`File: crates/apollo_gateway/src/stateful_transaction_validator.rs`)

### Summary
The gateway's `validate_resource_bounds` admission check derives its L2-gas-price threshold exclusively from the latest **committed** block, never consulting the pending block's gas price. Because the pending block is the block the transaction will actually execute in, the threshold is wrong whenever the two prices diverge. This is the direct sequencer-native analog of TRST-H-1: a gate-check that reads "current" committed state while ignoring the pending state, producing wrong accept/reject decisions for incoming transactions.

### Finding Description
In `stateful_transaction_validator.rs`, `validate_resource_bounds` calls `self.gateway_fixed_block_state_reader.get_block_info()` to obtain `previous_block_l2_gas_price`, then computes the admission threshold as `min_gas_price_percentage% × previous_block_l2_gas_price`. [1](#0-0) 

The code even carries an explicit TODO acknowledging the problem:

```rust
// TODO(Arni): getnext_l2_gas_price from the block header.
let previous_block_l2_gas_price = self
    .gateway_fixed_block_state_reader
    .get_block_info()
    .await?
    .gas_prices
    .strk_gas_prices
    .l2_gas_price;
```

`GatewayFixedBlockSyncStateClient` is constructed with `latest_block_number` (the last committed block), so `get_block_info()` always returns the committed block's gas price. [2](#0-1) [3](#0-2) 

The pending block's gas price is available via `PendingData.block.l2_gas_price()` and is already used correctly in the RPC execution paths (`estimate_fee`, `call`, `simulate_transactions`): [4](#0-3) 

But the gateway admission path never consults it. The `validate_tx_l2_gas_price_within_threshold` function therefore computes an exact wrong threshold value: [5](#0-4) 

### Impact Explanation
Two wrong outcomes arise whenever the pending block's L2 gas price diverges from the committed block's:

1. **False accept (accepts invalid transactions):** If `P_pending > P_committed`, a transaction with `max_price_per_unit` in the range `[threshold(P_committed), threshold(P_pending))` passes the gateway and enters the mempool, but will be rejected by the batcher when it tries to include it in the pending block. The gateway has accepted an invalid transaction.

2. **False reject (rejects valid transactions):** If `P_pending < P_committed`, a transaction with `max_price_per_unit` in the range `[threshold(P_pending), threshold(P_committed))` is rejected by the gateway even though it would succeed in the pending block. Valid transactions are blocked from sequencing.

Both outcomes match: **High. Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing.**

### Likelihood Explanation
L2 gas prices change between blocks as a normal operating condition. Any time the pending block's gas price diverges from the committed block's gas price — which is routine — the admission decision is wrong. The team has already identified this gap (the TODO comment), confirming it is a real, known discrepancy rather than an edge case.

### Recommendation
Replace `previous_block_l2_gas_price` with the pending block's L2 gas price. The pending block's gas price is already available via `PendingData.block.l2_gas_price()` and is used correctly in the RPC execution paths. The `StatefulTransactionValidatorFactory` should be extended to pass the pending data reference to the validator, mirroring how `estimate_fee` and `call` already consume it.

### Proof of Concept
1. Observe committed block has L2 gas price `P_committed = 100`.
2. Pending block has L2 gas price `P_pending = 200` (price doubled).
3. `min_gas_price_percentage = 100` → threshold = `100`.
4. Submit a transaction with `max_price_per_unit = 150` (above committed threshold, below pending threshold).
5. Gateway calls `validate_resource_bounds`: `150 >= 100` → **accepted**, transaction enters mempool.
6. Batcher tries to include the transaction: effective threshold against pending block = `200` → `150 < 200` → **rejected**.
7. Transaction is permanently stuck in the mempool, never sequenced, yet the gateway reported acceptance. [1](#0-0) [5](#0-4)

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

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L331-362)
```rust
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

**File:** crates/apollo_gateway/src/sync_state_reader.rs (L521-545)
```rust
    async fn get_blockifier_state_reader_and_gateway_fixed_block_from_latest_block(
        &self,
    ) -> StateSyncClientResult<(
        Self::TGatewayStateReaderWithCompiledClasses,
        Self::TGatewayFixedBlockStateReader,
    )> {
        let latest_block_number = self.shared_state_sync_client.get_latest_block_number().await?;

        // If no blocks exist yet, return genesis state readers for bootstrap transactions.
        let Some(latest_block_number) = latest_block_number else {
            info!("No blocks found yet; using genesis state readers for bootstrap transactions.");
            return Ok((GenesisStateReader.into(), GenesisFixedBlockStateReader.into()));
        };

        let blockifier_state_reader = SyncStateReader::from_number(
            self.shared_state_sync_client.clone(),
            self.class_manager_client.clone(),
            latest_block_number,
            self.runtime.clone(),
        );
        let gateway_fixed_block_sync_state_client = GatewayFixedBlockSyncStateClient::new(
            self.shared_state_sync_client.clone(),
            latest_block_number,
        );
        Ok((blockifier_state_reader.into(), gateway_fixed_block_sync_state_client.into()))
```

**File:** crates/apollo_gateway/src/gateway_fixed_block_state_reader.rs (L31-83)
```rust
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

    async fn get_nonce(&self, contract_address: ContractAddress) -> StarknetResult<Nonce> {
        match self.state_sync_client.get_nonce_at(self.block_number, contract_address).await {
            Ok(nonce) => Ok(nonce),
            Err(StateSyncClientError::StateSyncError(StateSyncError::ContractNotFound(_))) => {
                Ok(Nonce::default())
            }
            Err(e) => Err(StarknetError::internal_with_logging("Failed to get nonce", e)),
        }
    }
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1009-1016)
```rust
        let maybe_pending_data = if let BlockId::Tag(Tag::Pending) = block_id {
            Some(client_pending_data_to_execution_pending_data(
                read_pending_data(&self.pending_data, &storage_txn).await?,
                self.pending_classes.read().await.clone(),
            ))
        } else {
            None
        };
```
