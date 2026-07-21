### Title
Silent Fallback to Incorrect L1 Gas Prices When `max_time_gap_seconds` Staleness Threshold Exceeded Causes Wrong Fee Accounting in Committed Blocks - (File: `crates/apollo_l1_gas_price/src/l1_gas_price_provider.rs`)

---

### Summary

Analogous to the `finalizeGame()` 10-minute threshold that locks user funds when not called in time, `L1GasPriceProvider::get_price_info` enforces a `max_time_gap_seconds` (default 900 s) staleness window. When L1 data ages beyond this threshold — due to L1 chain downtime, scraper restart, or network delay — the function returns `StaleL1GasPricesError`. Unlike the external bug which fails hard, the sequencer silently falls back to stale previous-block prices or the hardcoded constant `DEFAULT_ETH_TO_FRI_RATE = 10^21`. These incorrect prices are embedded in `ProposalInit`, accepted by validators (who apply the same fallback), committed to the block header, and used for every fee calculation in the block, producing an incorrect block commitment with wrong gas-price fields and wrong fee accounting for all transactions.

---

### Finding Description

**Root cause — staleness gate in `get_price_info`:**

```rust
// crates/apollo_l1_gas_price/src/l1_gas_price_provider.rs  line 119
if timestamp.0 > (*last_timestamp + self.config.max_time_gap_seconds) {
    return Err(L1GasPriceProviderError::StaleL1GasPricesError {
        current_timestamp: timestamp.0,
        last_valid_price_timestamp: *last_timestamp,
    });
}
``` [1](#0-0) 

`max_time_gap_seconds` defaults to 900 s in both the code and the production deployment config: [2](#0-1) [3](#0-2) 

**Silent fallback in the orchestrator:**

When `get_price_info` (or the ETH→STRK oracle) returns any error, `get_l1_prices_in_fri_and_wei_and_conversion_rate` in `utils.rs` silently falls through two fallback tiers instead of halting:

1. **Tier 1** — reuse previous block's prices (stale, but not zero).
2. **Tier 2** — use `min_l1_gas_price_wei` (config minimum, e.g. 1 Gwei) with `DEFAULT_ETH_TO_FRI_RATE = 10^21` (hardcoded constant). [4](#0-3) 

`DEFAULT_ETH_TO_FRI_RATE = 10^21` is defined in `crates/apollo_l1_gas_price_types/src/lib.rs`. If the real market rate is, for example, `10^18` (1 ETH ≈ 1000 STRK), the fallback rate is 1000× higher, making every FRI-denominated gas price 1000× inflated.

**Prices flow into the block commitment:**

The fallback prices are used in `initiate_build` to construct `ProposalInit`: [5](#0-4) 

`ProposalInit` carries `l1_gas_price_fri`, `l1_data_gas_price_fri`, `l1_gas_price_wei`, `l1_data_gas_price_wei`: [6](#0-5) 

These fields are converted to `starknet_api::block::BlockInfo` via `convert_to_sn_api_block_info` and passed to the batcher, which commits them to the block header and state diff. [7](#0-6) 

**Validators apply the same fallback — so the wrong block is accepted:**

`is_block_info_valid` in `validate_proposal.rs` calls the identical `get_l1_prices_in_fri_and_wei` helper with the same fallback chain. When both proposer and validator have stale L1 data (same L1 downtime event), both compute the same wrong fallback prices, the `within_margin` check passes, and the proposal is accepted and committed with incorrect gas prices. [8](#0-7) 

---

### Impact Explanation

**Impact: Critical — Incorrect fee, gas, resource accounting, balance, or L1 gas price effect with economic impact.**

Every transaction in every block produced during the staleness window is charged fees computed from the wrong gas price. With Tier-2 fallback (`DEFAULT_ETH_TO_FRI_RATE = 10^21`), FRI-denominated fees are inflated by up to three orders of magnitude relative to the real market rate. These prices are committed to the block header and become the authoritative values for the SNOS, proof inputs, and any downstream fee-token balance updates. The block commitment is cryptographically wrong relative to the true L1 market state.

---

### Likelihood Explanation

The trigger is identical to the one described in the external report: **L1 chain downtime, L1 scraper restart, or network delay exceeding 900 seconds**. The L1 scraper must push consecutive blocks (`add_price_info` enforces strict block-number monotonicity); any gap or restart resets the buffer, making the staleness condition easy to reach after any L1 disruption. The production config confirms `max_time_gap_seconds = 900` with no operator override required. [9](#0-8) 

---

### Recommendation

1. **Do not silently fall back to `DEFAULT_ETH_TO_FRI_RATE`** when L1 data is stale. Either pause block production or surface a hard error that requires operator acknowledgment.
2. **Bound the previous-block-info fallback** with its own staleness limit (e.g., refuse to reuse prices older than N blocks) so that prolonged downtime does not silently propagate stale prices indefinitely.
3. **Separate the staleness check from the fallback path**: return a distinct error for "stale" vs. "missing" so callers can apply different policies.
4. **Add a validator-side staleness guard**: if the validator's own L1 data is stale, it should abstain from voting rather than accepting a proposal whose gas prices it cannot independently verify.

---

### Proof of Concept

```
1. L1 chain experiences downtime for > 900 seconds.
2. L1GasPriceScraper stops pushing new GasPriceData to L1GasPriceProvider.
3. Proposer calls get_price_info(block_timestamp):
     block_timestamp > last_L1_timestamp + 900  →  StaleL1GasPricesError
4. get_l1_prices_in_fri_and_wei_and_conversion_rate falls to Tier-2 fallback:
     eth_to_fri_rate = DEFAULT_ETH_TO_FRI_RATE = 10^21
     l1_gas_price_wei = min_l1_gas_price_wei (e.g. 1e9 wei = 1 Gwei)
     l1_gas_price_fri = 1e9 * 1e21 / 1e18 = 1e12 FRI
   (real market rate ~1e18 → real l1_gas_price_fri ≈ 1e9 FRI → 1000× inflation)
5. ProposalInit is built with l1_gas_price_fri = 1e12 FRI and broadcast.
6. Validators also have stale L1 data; their get_l1_prices_in_fri_and_wei
   returns the same 1e12 FRI fallback; within_margin check passes.
7. Batcher commits block with l1_gas_price_fri = 1e12 FRI in the block header.
8. All transactions in the block are charged 1000× the correct STRK fee.
9. Block commitment, state diff, and SNOS inputs all reflect the wrong price.
```

### Citations

**File:** crates/apollo_l1_gas_price/src/l1_gas_price_provider.rs (L84-103)
```rust
    pub fn add_price_info(&mut self, new_data: GasPriceData) -> L1GasPriceProviderResult<()> {
        // In case the provider has been restarted while the scraper is still running,
        // a NotInitializedError will be returned to the scraper. We expect the scraper to exit with
        // an error, and that infrastructure will restart it, leading to initialization.
        let Some(samples) = &mut self.price_samples_by_block else {
            return Err(L1GasPriceProviderError::NotInitializedError);
        };
        if let Some(data) = samples.back() {
            if new_data.block_number != data.block_number + 1 {
                return Err(L1GasPriceProviderError::UnexpectedBlockNumberError {
                    expected: data.block_number + 1,
                    found: new_data.block_number,
                });
            }
        }
        trace!("Received price sample for L1 block: {:?}", new_data);
        info_every_n_ms!(1_000, "Received price sample for L1 block: {:?}", new_data);
        samples.push(new_data);
        Ok(())
    }
```

**File:** crates/apollo_l1_gas_price/src/l1_gas_price_provider.rs (L118-124)
```rust
        // Check if the prices are stale.
        if timestamp.0 > (*last_timestamp + self.config.max_time_gap_seconds) {
            return Err(L1GasPriceProviderError::StaleL1GasPricesError {
                current_timestamp: timestamp.0,
                last_valid_price_timestamp: *last_timestamp,
            });
        }
```

**File:** crates/apollo_l1_gas_price_provider_config/src/config.rs (L108-118)
```rust
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
```

**File:** crates/apollo_deployments/resources/app_configs/replacer_l1_gas_price_provider_config.json (L1-9)
```json
{
  "l1_gas_price_provider_config.eth_to_strk_oracle_config.lag_interval_seconds": 900,
  "l1_gas_price_provider_config.eth_to_strk_oracle_config.max_cache_size": 100,
  "l1_gas_price_provider_config.eth_to_strk_oracle_config.query_timeout_sec": 10,
  "l1_gas_price_provider_config.lag_margin_seconds": 600,
  "l1_gas_price_provider_config.max_time_gap_seconds": 900,
  "l1_gas_price_provider_config.number_of_blocks_for_mean": 300,
  "l1_gas_price_provider_config.storage_limit": 3000
}
```

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L147-221)
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

    // One or both (oracle/provider) have failed to fetch, or failure in conversion, so we need to
    // try to use the previous block info.
    if let Some(block_info) = previous_block_info {
        let prev_l1_gas_price_wei = block_info.l1_prices_wei.clone();
        let prev_l1_gas_price = block_info.l1_prices_fri.clone();
        // This calculation can fail if gas price is too high, or zero, or if the prices cause the
        // rate to be zero.
        let eth_to_fri_rate = calculate_eth_to_fri_rate(block_info);
        match eth_to_fri_rate {
            Ok(eth_to_fri_rate) => {
                info!(
                    "Using previous block info: wei prices: {:?}, fri prices: {:?}, eth to fri \
                     rate: {:?}",
                    prev_l1_gas_price_wei, prev_l1_gas_price, eth_to_fri_rate
                );
                return (prev_l1_gas_price, prev_l1_gas_price_wei, eth_to_fri_rate);
            }
            Err(error) => {
                warn!(
                    "Error calculating eth to fri rate from previous block info: {:?}: {:?}",
                    block_info, error
                );
                // Do not use previous block info. Prefer the default values instead.
            }
        }
    }

    let default_l1_gas_prices_wei = L1PricesInWei {
        l1_gas_price: gas_price_params.min_l1_gas_price_wei,
        l1_data_gas_price: gas_price_params.min_l1_data_gas_price_wei,
    };
    let default_l1_gas_prices_fri =
        L1PricesInFri::convert_from_wei(&default_l1_gas_prices_wei, DEFAULT_ETH_TO_FRI_RATE)
            .expect("Default values should be convertible between wei and fri.");
    info!(
        "Using default values: fri prices: {:?}, wei prices: {:?}, eth to fri rate: {:?}",
        default_l1_gas_prices_fri, default_l1_gas_prices_wei, DEFAULT_ETH_TO_FRI_RATE
    );
    (default_l1_gas_prices_fri, default_l1_gas_prices_wei, DEFAULT_ETH_TO_FRI_RATE)
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

**File:** crates/apollo_consensus_orchestrator/src/build_proposal.rs (L149-179)
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
```

**File:** crates/apollo_protobuf/src/consensus.rs (L94-125)
```rust
#[derive(Clone, Debug, PartialEq)]
pub struct ProposalInit {
    /// The height of the consensus (block number).
    pub height: BlockNumber,
    /// The current round of the consensus.
    pub round: Round,
    /// The last round that was valid.
    pub valid_round: Option<Round>,
    /// Address of the one who proposed the block in consensus.
    pub proposer: ContractAddress,
    /// Block timestamp.
    pub timestamp: u64,
    /// Address of the one who builds/sequences the block.
    pub builder: ContractAddress,
    /// L1 data availability mode.
    pub l1_da_mode: L1DataAvailabilityMode,
    /// L2 gas price in FRI.
    pub l2_gas_price_fri: GasPrice,
    /// L1 gas price in FRI.
    pub l1_gas_price_fri: GasPrice,
    /// L1 data gas price in FRI.
    pub l1_data_gas_price_fri: GasPrice,
    // Keeping the wei prices for now, to use with L1 transactions.
    /// L1 gas price in WEI.
    pub l1_gas_price_wei: GasPrice,
    /// L1 data gas price in WEI.
    pub l1_data_gas_price_wei: GasPrice,
    /// Starknet protocol version.
    pub starknet_version: starknet_api::block::StarknetVersion,
    /// Version constant commitment.
    pub version_constant_commitment: StarkHash,
}
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L286-320)
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
    Ok(())
```
