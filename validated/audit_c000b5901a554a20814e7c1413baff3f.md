### Title
Stale ETH/STRK Conversion Rate Silently Used Without Freshness Validation Produces Wrong Block Gas-Price Commitment — (`crates/apollo_l1_gas_price/src/eth_to_strk_oracle.rs`)

---

### Summary

The `EthToStrkOracleClient::eth_to_fri_rate` function silently falls back to a cached rate from the previous quantized-timestamp interval (up to `lag_interval_seconds` = 900 s old in production) with **no staleness guard**, while the parallel `get_price_info` path has an explicit `max_time_gap_seconds` (also 900 s) check that returns `StaleL1GasPricesError`. When both calls succeed, `get_l1_prices_in_fri_and_wei_and_conversion_rate` combines fresh Wei prices with the stale ETH/STRK rate, producing wrong `l1_gas_price_fri` and `l1_data_gas_price_fri` values that are embedded in the `ProposalInit`, executed against, and committed to the block header.

---

### Finding Description

**Root cause — asymmetric staleness enforcement**

`get_price_info` enforces freshness:

```rust
// l1_gas_price_provider.rs L119-124
if timestamp.0 > (*last_timestamp + self.config.max_time_gap_seconds) {
    return Err(L1GasPriceProviderError::StaleL1GasPricesError { … });
}
``` [1](#0-0) 

`eth_to_fri_rate` has **no equivalent check**. When the current interval's query is still in-flight it silently returns the previous interval's cached value:

```rust
// eth_to_strk_oracle.rs L214-224
if !handle.is_finished() {
    if let Some(rate) = cache.get(&(quantized_timestamp - NUMBER_OF_TIMESTAMPS_BACK)) {
        return Ok(*rate);   // ← stale rate, no age check
    }
    return Err(EthToStrkOracleClientError::QueryNotReadyError(timestamp));
}
``` [2](#0-1) 

The oracle response is validated only for format (`price` hex string, `decimals == 18`), never for freshness. [3](#0-2) 

**Propagation into block commitment**

`get_l1_prices_in_fri_and_wei_and_conversion_rate` joins both calls and, when both return `Ok`, uses the stale rate directly:

```rust
// utils.rs L147-174
let (eth_to_fri_rate, price_info) = tokio::join!(
    l1_gas_price_provider_client.get_eth_to_fri_rate(timestamp),
    l1_gas_price_provider_client.get_price_info(BlockTimestamp(timestamp))
);
if let (Ok(eth_to_fri_rate), Ok(mut price_info)) = (eth_to_fri_rate, price_info) {
    apply_fee_transformations(&mut price_info, gas_price_params);
    let prices_in_wei = L1PricesInWei { … };
    let l1_gas_prices_fri_result =
        L1PricesInFri::convert_from_wei(&prices_in_wei, eth_to_fri_rate);
    if let Ok(prices_in_fri) = l1_gas_prices_fri_result {
        return (prices_in_fri, prices_in_wei, eth_to_fri_rate);  // ← stale rate committed
    }
}
``` [4](#0-3) 

The resulting `l1_gas_price_fri` / `l1_data_gas_price_fri` are placed into `ProposalInit` and used by `convert_to_sn_api_block_info` to build the `BlockInfo` that drives execution and is hashed into the block commitment. [5](#0-4) 

**Validator accepts the wrong value**

During validation, `is_block_info_valid` calls the same `get_l1_prices_in_fri_and_wei` path with the same timestamp. If the validator's oracle is also in the same in-flight state (highly likely — both nodes start a new 15-minute interval at the same wall-clock time), it computes the same stale-rate-derived Fri price and the `within_margin` check passes, so the wrong commitment is accepted by consensus. [6](#0-5) 

---

### Impact Explanation

Every transaction in the affected block has its STRK-denominated fee (`l1_gas_price_fri`, `l1_data_gas_price_fri`) computed from a stale ETH/STRK rate. These values are committed to the block header hash and stored in the sequencer's MDBX storage. The wrong fee vector is also the input to the blockifier's resource-accounting logic, so actual fee charges and refunds for every transaction in the block are incorrect. This matches the allowed scope: **"Incorrect fee, gas, bouncer, resource accounting, refund, balance, or L1 gas price effect with economic impact."**

---

### Likelihood Explanation

The trigger is structural and periodic, not adversarial. Every time the 15-minute `lag_interval_seconds` window rolls over (production config: `lag_interval_seconds = 900`), the new interval's HTTP query is spawned but not yet resolved. During that window — which can span multiple L2 blocks — every call to `eth_to_fri_rate` returns the previous interval's cached value. No attacker action is required; the condition arises automatically in normal operation. [7](#0-6) 

---

### Recommendation

Add a staleness guard to `eth_to_fri_rate` symmetric to the one in `get_price_info`. Track the wall-clock time at which each quantized-timestamp entry was cached and reject (or at least warn and fall through to the previous-block-info fallback) if the cached entry is older than `max_time_gap_seconds`. Concretely:

```rust
// After retrieving from cache, check age:
if let Some((rate, cached_at)) = cache.get(&quantized_timestamp) {
    let age = current_unix_time - cached_at;
    if age > max_time_gap_seconds {
        return Err(EthToStrkOracleClientError::StaleRateError { age });
    }
    return Ok(*rate);
}
```

The same guard should apply to the `quantized_timestamp - 1` fallback path.

---

### Proof of Concept

1. At wall-clock second `T = N * 900` a new quantized interval begins. `eth_to_fri_rate(T+1)` computes `quantized_timestamp = (T+1 - 900) / 900 = N - 1 + ε → N-1` (integer division), spawns a new HTTP query, and — because the query is not yet finished — falls back to `cache[N-2]`, a rate that is up to 900 s old. [8](#0-7) 

2. Simultaneously, `get_price_info(BlockTimestamp(T+1))` succeeds (L1 scraper is current), returning fresh Wei prices. [9](#0-8) 

3. `get_l1_prices_in_fri_and_wei_and_conversion_rate` sees `(Ok(stale_rate), Ok(fresh_wei_prices))` and returns `L1PricesInFri::convert_from_wei(fresh_wei_prices, stale_rate)`. [10](#0-9) 

4. The proposer embeds the wrong `l1_gas_price_fri` / `l1_data_gas_price_fri` in `ProposalInit`. The validator, in the same interval-rollover state, computes the same stale-derived value and the `within_margin` check passes. [11](#0-10) 

5. The block is committed with wrong STRK gas prices in its header; all transaction fees in the block are computed against the stale rate.

### Citations

**File:** crates/apollo_l1_gas_price/src/l1_gas_price_provider.rs (L105-124)
```rust
    pub fn get_price_info(&self, timestamp: BlockTimestamp) -> L1GasPriceProviderResult<PriceInfo> {
        let Some(samples) = &self.price_samples_by_block else {
            return Err(L1GasPriceProviderError::NotInitializedError);
        };
        // timestamp of the newest price sample
        let last_timestamp = samples
            .back()
            .ok_or(L1GasPriceProviderError::MissingDataError {
                timestamp: timestamp.0,
                lag: self.config.lag_margin_seconds.as_secs(),
            })?
            .timestamp;

        // Check if the prices are stale.
        if timestamp.0 > (*last_timestamp + self.config.max_time_gap_seconds) {
            return Err(L1GasPriceProviderError::StaleL1GasPricesError {
                current_timestamp: timestamp.0,
                last_valid_price_timestamp: *last_timestamp,
            });
        }
```

**File:** crates/apollo_l1_gas_price/src/eth_to_strk_oracle.rs (L153-188)
```rust
fn resolve_query(body: String) -> Result<u128, EthToStrkOracleClientError> {
    let Ok(json): Result<serde_json::Value, _> = serde_json::from_str(&body) else {
        return Err(EthToStrkOracleClientError::ParseError(format!(
            "Failed to parse JSON: {body}"
        )));
    };
    // Extract price from API response. Also returns MissingFieldError if value is not a string.
    let price = match json.get("price").and_then(|v| v.as_str()) {
        Some(price) => price,
        None => {
            return Err(EthToStrkOracleClientError::MissingFieldError("price".to_string(), body));
        }
    };
    let rate = u128::from_str_radix(price.trim_start_matches("0x"), 16)
        .expect("Failed to parse price as u128");
    // Extract decimals from API response. Also returns MissingFieldError if value is not a number.
    let decimals = match json.get("decimals").and_then(|v| v.as_u64()) {
        Some(decimals) => decimals,
        None => {
            return Err(EthToStrkOracleClientError::MissingFieldError(
                "decimals".to_string(),
                body,
            ));
        }
    };
    if decimals != ETH_TO_STRK_QUANTIZATION {
        return Err(EthToStrkOracleClientError::InvalidDecimalsError(
            ETH_TO_STRK_QUANTIZATION,
            decimals,
        ));
    }
    ETH_TO_STRK_SUCCESS_COUNT.increment(1);
    set_unix_now_seconds(&ETH_TO_STRK_LAST_SUCCESS_TIMESTAMP_SECONDS);
    ETH_TO_STRK_RATE.set_lossy(rate);
    Ok(rate)
}
```

**File:** crates/apollo_l1_gas_price/src/eth_to_strk_oracle.rs (L196-228)
```rust
    async fn eth_to_fri_rate(&self, timestamp: u64) -> Result<u128, EthToStrkOracleClientError> {
        const NUMBER_OF_TIMESTAMPS_BACK: u64 = 1;
        let quantized_timestamp = (timestamp - self.config.lag_interval_seconds)
            .checked_div(self.config.lag_interval_seconds)
            .expect("lag_interval_seconds should be non-zero");

        let mut cache = self.cached_prices.lock().unwrap();

        if let Some(rate) = cache.get(&quantized_timestamp) {
            debug!("Cached conversion rate for timestamp {timestamp} is {rate}");
            return Ok(*rate);
        }

        // Check if there is a query already sent out for this timestamp, if not, start one.
        let mut queries = self.queries.lock().unwrap();
        let handle = queries
            .get_or_insert_mut(quantized_timestamp, || self.spawn_query(quantized_timestamp));
        // If the query is not finished, return an error.
        if !handle.is_finished() {
            debug!("Query not yet resolved: timestamp={timestamp}");
            // If the previous quantized timestamp is in the cache, use it.
            if let Some(rate) = cache.get(&(quantized_timestamp - NUMBER_OF_TIMESTAMPS_BACK)) {
                debug!(
                    "Query not yet resolved: timestamp={timestamp}, using previous rate {rate} \
                     from quantized timestamp={}",
                    (quantized_timestamp - NUMBER_OF_TIMESTAMPS_BACK)
                        * self.config.lag_interval_seconds
                );
                return Ok(*rate);
            }
            // If not, return a query not ready error.
            return Err(EthToStrkOracleClientError::QueryNotReadyError(timestamp));
        }
```

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L147-181)
```rust
    let (eth_to_fri_rate, price_info) = tokio::join!(
        l1_gas_price_provider_client.get_eth_to_fri_rate(timestamp),
        l1_gas_price_provider_client.get_price_info(BlockTimestamp(timestamp))
    );
    if price_info.is_err() {
        warn!("Failed to get l1 gas price from provider: {:?}", price_info);
        CONSENSUS_L1_GAS_PRICE_PROVIDER_ERROR.increment(1);
    }
    if eth_to_fri_rate.is_err() {
        warn!("Failed to get eth to fri rate from oracle: {:?}", eth_to_fri_rate);
    }
    if let (Ok(eth_to_fri_rate), Ok(mut price_info)) = (eth_to_fri_rate, price_info) {
        // Both L1 prices and rate are Ok, so we can use them.
        info!(
            "raw eth_to_fri_rate (from oracle): {eth_to_fri_rate}, raw l1 gas price wei (from \
             provider): {price_info:?}"
        );
        apply_fee_transformations(&mut price_info, gas_price_params);
        let prices_in_wei = L1PricesInWei {
            l1_gas_price: price_info.base_fee_per_gas,
            l1_data_gas_price: price_info.blob_fee,
        };
        // Apply the eth/strk rate to get prices in fri.
        let l1_gas_prices_fri_result =
            L1PricesInFri::convert_from_wei(&prices_in_wei, eth_to_fri_rate);
        // If conversion fails, leave return_value=None to try backup methods.
        if let Ok(prices_in_fri) = l1_gas_prices_fri_result {
            return (prices_in_fri, prices_in_wei, eth_to_fri_rate);
        } else {
            warn!(
                "Failed to convert L1 gas prices to FRI: {:?}",
                l1_gas_prices_fri_result.clone().err()
            );
        }
    }
```

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L287-334)
```rust
pub(crate) fn convert_to_sn_api_block_info(
    init: &ProposalInit,
) -> Result<starknet_api::block::BlockInfo, StarknetApiError> {
    if init.l1_gas_price_fri.0 == 0
        || init.l1_gas_price_wei.0 == 0
        || init.l1_data_gas_price_fri.0 == 0
        || init.l1_data_gas_price_wei.0 == 0
        || init.l2_gas_price_fri.0 == 0
    {
        warn!("Zero gas price detected in block info: {:?}", init);
    }

    let l1_gas_price_fri = NonzeroGasPrice::new(init.l1_gas_price_fri)?;
    let l1_data_gas_price_fri = NonzeroGasPrice::new(init.l1_data_gas_price_fri)?;
    let l1_gas_price_wei = NonzeroGasPrice::new(init.l1_gas_price_wei)?;
    let l1_data_gas_price_wei = NonzeroGasPrice::new(init.l1_data_gas_price_wei)?;
    let l2_gas_price_fri = NonzeroGasPrice::new(init.l2_gas_price_fri)?;
    let previous_block_info = PreviousBlockInfo::from(init);
    let eth_to_fri_rate = calculate_eth_to_fri_rate(&previous_block_info)?;

    let l2_gas_price_wei = NonzeroGasPrice::new(init.l2_gas_price_fri.fri_to_wei(eth_to_fri_rate)?)
        .inspect_err(|_| {
            warn!(
                "L2 gas price in wei is zero! Conversion rate: {eth_to_fri_rate}, L2 gas price in \
                 FRI: {}",
                init.l2_gas_price_fri
            )
        })?;
    Ok(starknet_api::block::BlockInfo {
        block_number: init.height,
        block_timestamp: BlockTimestamp(init.timestamp),
        sequencer_address: init.builder,
        gas_prices: GasPrices {
            strk_gas_prices: GasPriceVector {
                l1_gas_price: l1_gas_price_fri,
                l1_data_gas_price: l1_data_gas_price_fri,
                l2_gas_price: l2_gas_price_fri,
            },
            eth_gas_prices: GasPriceVector {
                l1_gas_price: l1_gas_price_wei,
                l1_data_gas_price: l1_data_gas_price_wei,
                l2_gas_price: l2_gas_price_wei,
            },
        },
        use_kzg_da: init.l1_da_mode.is_use_kzg_da(),
        starknet_version: init.starknet_version,
    })
}
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L286-319)
```rust
    let (l1_gas_prices_fri, _l1_gas_prices_wei) = get_l1_prices_in_fri_and_wei(
        l1_gas_price_provider,
        init_proposed.timestamp,
        block_info_validation.previous_block_info.as_ref(),
        gas_price_params,
    )
    .await;
    let l1_gas_price_margin_percent =
        VersionedConstants::latest_constants().l1_gas_price_margin_percent.into();
    debug!("L1 price info: {l1_gas_prices_fri:?}");

    let l1_gas_price_fri = l1_gas_prices_fri.l1_gas_price;
    let l1_data_gas_price_fri = l1_gas_prices_fri.l1_data_gas_price;
    let l1_gas_price_fri_proposed = init_proposed.l1_gas_price_fri;
    let l1_data_gas_price_fri_proposed = init_proposed.l1_data_gas_price_fri;

    if !(within_margin(l1_gas_price_fri_proposed, l1_gas_price_fri, l1_gas_price_margin_percent)
        && within_margin(
            l1_data_gas_price_fri_proposed,
            l1_data_gas_price_fri,
            l1_gas_price_margin_percent,
        ))
    {
        return Err(ValidateProposalError::InvalidBlockInfo(
            init_proposed.clone(),
            block_info_validation.clone(),
            format!(
                "L1 gas price mismatch: expected L1 gas price FRI={l1_gas_price_fri}, \
                 proposed={l1_gas_price_fri_proposed}, expected L1 data gas price \
                 FRI={l1_data_gas_price_fri}, proposed={l1_data_gas_price_fri_proposed}, \
                 l1_gas_price_margin_percent={l1_gas_price_margin_percent}"
            ),
        ));
    }
```

**File:** crates/apollo_deployments/resources/app_configs/l1_gas_price_provider_config.json (L1-9)
```json
{
  "l1_gas_price_provider_config.eth_to_strk_oracle_config.lag_interval_seconds": 900,
  "l1_gas_price_provider_config.eth_to_strk_oracle_config.max_cache_size": 100,
  "l1_gas_price_provider_config.eth_to_strk_oracle_config.query_timeout_sec": 10,
  "l1_gas_price_provider_config.lag_margin_seconds": 600,
  "l1_gas_price_provider_config.number_of_blocks_for_mean": 300,
  "l1_gas_price_provider_config.storage_limit": 3000,
  "l1_gas_price_provider_config.max_time_gap_seconds": 900
}
```
