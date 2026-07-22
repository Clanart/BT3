### Title
Single `max_time_gap_seconds` staleness guard covers only L1 gas price data, leaving ETH-to-STRK oracle rate unchecked — (`crates/apollo_l1_gas_price/src/l1_gas_price_provider.rs`)

---

### Summary

`L1GasPriceProvider` combines two data sources with fundamentally different update cadences to produce the FRI-denominated gas prices embedded in every block header. The L1 gas price samples (Ethereum base fee + blob fee) are guarded by an explicit time-gap check (`max_time_gap_seconds`). The ETH-to-STRK oracle rate has **no equivalent staleness check**: it silently returns a cached value up to `2 × lag_interval_seconds` old. Because both values are multiplied together to produce `l1_gas_price_fri` / `l1_data_gas_price_fri`, a stale conversion rate produces incorrect FRI prices that are committed into the block header and used for all fee calculations in that block.

---

### Finding Description

**Two data sources, one staleness policy**

`get_price_info` enforces:

```rust
// crates/apollo_l1_gas_price/src/l1_gas_price_provider.rs  line 119
if timestamp.0 > (*last_timestamp + self.config.max_time_gap_seconds) {
    return Err(L1GasPriceProviderError::StaleL1GasPricesError { … });
}
```

Production config sets `max_time_gap_seconds = 900` s. [1](#0-0) [2](#0-1) 

`eth_to_fri_rate` has **no time-based staleness check**. It returns the cached rate for `quantized_timestamp`, and if that is missing, silently falls back to `quantized_timestamp - 1` (one full `lag_interval_seconds` older) without any error:

```rust
// crates/apollo_l1_gas_price/src/eth_to_strk_oracle.rs  lines 196-227
let quantized_timestamp = (timestamp - self.config.lag_interval_seconds)
    .checked_div(self.config.lag_interval_seconds) …;
if let Some(rate) = cache.get(&quantized_timestamp) {
    return Ok(*rate);          // ← no age check
}
…
if !handle.is_finished() {
    if let Some(rate) = cache.get(&(quantized_timestamp - NUMBER_OF_TIMESTAMPS_BACK)) {
        return Ok(*rate);      // ← up to 2 × lag_interval_seconds old, no check
    }
    return Err(QueryNotReadyError(timestamp));
}
``` [3](#0-2) 

Production config sets `lag_interval_seconds = 900` s, so the oracle can silently return a rate up to **1 800 s** old while `get_price_info` would already have rejected L1 gas price data older than **900 s**. [4](#0-3) 

**How the two values are combined**

`get_l1_prices_in_fri_and_wei_and_conversion_rate` calls both in parallel and, when both succeed, multiplies them:

```rust
// crates/apollo_consensus_orchestrator/src/utils.rs  lines 147-174
let (eth_to_fri_rate, price_info) = tokio::join!(
    l1_gas_price_provider_client.get_eth_to_fri_rate(timestamp),
    l1_gas_price_provider_client.get_price_info(BlockTimestamp(timestamp))
);
…
apply_fee_transformations(&mut price_info, gas_price_params);
let prices_in_wei = L1PricesInWei { … };
let l1_gas_prices_fri_result =
    L1PricesInFri::convert_from_wei(&prices_in_wei, eth_to_fri_rate);
``` [5](#0-4) 

The result is placed directly into `ProposalInit.l1_gas_price_fri` / `l1_data_gas_price_fri` and forwarded to the batcher as the authoritative `BlockInfo`:

```rust
// crates/apollo_consensus_orchestrator/src/build_proposal.rs  lines 156-176
let (l1_prices_fri, l1_prices_wei) = get_l1_prices_in_fri_and_wei(…).await;
let init = ProposalInit {
    l1_gas_price_fri: l1_prices_fri.l1_gas_price,
    l1_data_gas_price_fri: l1_prices_fri.l1_data_gas_price,
    …
};
``` [6](#0-5) 

**The asymmetry**

| Data source | Update cadence | Staleness bound enforced |
|---|---|---|
| L1 gas price (Wei) | ~12 s (Ethereum block) | `max_time_gap_seconds` = 900 s |
| ETH-to-STRK rate | `lag_interval_seconds` = 900 s | **None** (up to 1 800 s silently accepted) |

This is the direct sequencer analog of the Chainlink bug: one threshold (`max_time_gap_seconds`) is applied to only one of the two price inputs, leaving the other unchecked.

---

### Impact Explanation

`l1_gas_price_fri` and `l1_data_gas_price_fri` in the committed `ProposalInit` / `BlockInfo` are computed as `Wei_price × eth_to_fri_rate`. When the oracle rate is up to 2× more stale than the Wei price, the FRI prices diverge from the true market rate by the same factor as the ETH/STRK price movement over that extra 900 s window. Every transaction fee charged in STRK for that block is computed against this incorrect price. The block header (and therefore the block hash) encodes the wrong FRI gas prices, causing incorrect fee accounting with direct economic impact on users and the protocol.

Matches: **Critical — Incorrect fee, gas, bouncer, resource accounting, refund, balance, or L1 gas price effect with economic impact.**

---

### Likelihood Explanation

The oracle quantizes time into 900 s buckets and always lags by at least one bucket. The fallback to `quantized_timestamp - 1` is exercised on every block produced during the first few seconds of a new bucket (i.e., while the fresh query is still in flight). This is a **routine, unprivileged, non-adversarial condition** that occurs multiple times per 900 s interval under normal operation. No special privileges or network manipulation are required.

---

### Recommendation

Add a dedicated `max_oracle_age_seconds` field to `L1GasPriceProviderConfig` (analogous to `max_time_gap_seconds`) and enforce it inside `eth_to_fri_rate` before returning any cached value:

```rust
// Proposed check in EthToStrkOracleClient::eth_to_fri_rate
let cache_age_seconds = current_unix_time()
    .saturating_sub(quantized_timestamp * self.config.lag_interval_seconds);
if cache_age_seconds > self.config.max_oracle_age_seconds {
    return Err(EthToStrkOracleClientError::StaleRateError(cache_age_seconds));
}
```

This mirrors the `max_time_gap_seconds` guard in `get_price_info` and ensures both inputs to the FRI price calculation are bounded by their own appropriate freshness thresholds.

---

### Proof of Concept

1. Deploy with production config: `lag_interval_seconds = 900`, `max_time_gap_seconds = 900`.
2. At time `T`, the oracle successfully fetches and caches the rate for `quantized_timestamp = Q`.
3. At time `T + 901` s, a new block is proposed. The new `quantized_timestamp = Q+1`. The query for `Q+1` is in flight but not yet resolved.
4. `eth_to_fri_rate` falls back to `Q` (the previous bucket), returning a rate that is up to **1 800 s** old — no error is raised.
5. `get_price_info` returns fresh Wei prices (last L1 block was 12 s ago, well within 900 s).
6. `get_l1_prices_in_fri_and_wei_and_conversion_rate` takes the **Ok/Ok** branch and multiplies fresh Wei prices by the 1 800 s-old rate.
7. `ProposalInit.l1_gas_price_fri` is set to the incorrect value and broadcast to all validators; the batcher executes all transactions with this wrong FRI price as the authoritative block gas price. [7](#0-6) [3](#0-2) [8](#0-7) [9](#0-8) [10](#0-9)

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

**File:** crates/apollo_l1_gas_price_provider_config/src/config.rs (L93-119)
```rust
#[derive(Clone, Debug, Serialize, Deserialize, Validate, PartialEq)]
pub struct L1GasPriceProviderConfig {
    // TODO(guyn): these two fields need to go into VersionedConstants.
    pub number_of_blocks_for_mean: u64,
    // Use seconds not Duration since seconds is the basic quanta of time for both Starknet and
    // Ethereum.
    #[serde(deserialize_with = "deserialize_float_seconds_to_duration")]
    pub lag_margin_seconds: Duration,
    pub storage_limit: usize,
    // Maximum valid time gap between the requested timestamp and the last price sample in seconds.
    pub max_time_gap_seconds: u64,
    #[validate(nested)]
    pub eth_to_strk_oracle_config: EthToStrkOracleConfig,
}

impl Default for L1GasPriceProviderConfig {
    fn default() -> Self {
        const MEAN_NUMBER_OF_BLOCKS: u64 = 300;
        Self {
            number_of_blocks_for_mean: MEAN_NUMBER_OF_BLOCKS,
            lag_margin_seconds: Duration::from_secs(60),
            storage_limit: usize::try_from(10 * MEAN_NUMBER_OF_BLOCKS).unwrap(),
            max_time_gap_seconds: 900, // 15 minutes
            eth_to_strk_oracle_config: EthToStrkOracleConfig::default(),
        }
    }
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

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L135-181)
```rust
pub(crate) async fn get_l1_prices_in_fri_and_wei_and_conversion_rate(
    l1_gas_price_provider_client: Arc<dyn L1GasPriceProviderClient>,
    timestamp: u64,
    previous_block_info: Option<&PreviousBlockInfo>,
    gas_price_params: &GasPriceParams,
) -> (L1PricesInFri, L1PricesInWei, u128) {
    // One of these paths should fill the return values:
    // 1. Both L1 gas price and eth/strk rate are Ok, use those.
    // 2. Otherwise, use previous block info.
    // 3. If that isn't available either, use min gas prices and default eth/strk rate.

    // Get the eth to fri rate from the oracle, and the L1 gas price (in wei) from the provider.
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

**File:** crates/apollo_consensus_orchestrator/src/build_proposal.rs (L149-207)
```rust
async fn initiate_build(args: &mut ProposalBuildArguments) -> BuildProposalResult<ProposalInit> {
    let timestamp = get_proposal_timestamp(
        args.override_timestamp,
        args.deps.batcher.as_ref(),
        args.deps.clock.as_ref(),
    )
    .await;
    let (l1_prices_fri, l1_prices_wei) = get_l1_prices_in_fri_and_wei(
        args.deps.l1_gas_price_provider.clone(),
        timestamp,
        args.previous_block_info.as_ref(),
        &args.gas_price_params,
    )
    .await;
    let init = ProposalInit {
        height: args.build_param.height,
        round: args.build_param.round,
        valid_round: args.build_param.valid_round,
        proposer: args.build_param.proposer,
        builder: args.builder_address,
        timestamp,
        l1_da_mode: args.l1_da_mode,
        l2_gas_price_fri: args.l2_gas_price,
        l1_gas_price_wei: l1_prices_wei.l1_gas_price,
        l1_data_gas_price_wei: l1_prices_wei.l1_data_gas_price,
        l1_gas_price_fri: l1_prices_fri.l1_gas_price,
        l1_data_gas_price_fri: l1_prices_fri.l1_data_gas_price,
        starknet_version: starknet_api::block::StarknetVersion::LATEST,
        // TODO(Asmaa): Put the real value once we have it.
        version_constant_commitment: Default::default(),
    };

    let retrospective_block_hash = wait_for_retrospective_block_hash(
        args.deps.batcher.clone(),
        args.deps.state_sync_client.clone(),
        &init,
        args.deps.clock.as_ref(),
        args.retrospective_block_hash_deadline,
        args.retrospective_block_hash_retry_interval_millis,
        args.compare_retrospective_block_hash,
    )
    .await?;

    let build_proposal_input = ProposeBlockInput {
        proposal_id: args.proposal_id,
        deadline: args.batcher_deadline,
        retrospective_block_hash,
        block_info: convert_to_sn_api_block_info(&init)?,
        proposal_round: args.proposal_round,
    };
    debug!("Initiating build proposal: {build_proposal_input:?}");
    args.deps.batcher.propose_block(build_proposal_input.clone()).await.map_err(|err| {
        BuildProposalError::Batcher(
            format!("Failed to initiate build proposal {build_proposal_input:?}."),
            err,
        )
    })?;
    Ok(init)
}
```
