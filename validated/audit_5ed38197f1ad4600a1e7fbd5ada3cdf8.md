### Title
Single ETH-to-STRK Oracle Source Accepted Without Cross-Validation Causes Wrong Gas Prices Committed into Block Hash - (File: `crates/apollo_l1_gas_price/src/eth_to_strk_oracle.rs`)

### Summary
`EthToStrkOracleClient` uses a first-success strategy across its `url_header_list` with no cross-validation and no bounds check on the returned rate. A single compromised oracle endpoint causes all validators sharing that endpoint to derive the same wrong `eth_to_fri_rate`, which propagates into `l1_gas_price_fri` / `l1_data_gas_price_fri` committed in the block header and block hash. Because `is_block_info_valid()` validates the proposed price against the validator's own oracle-derived price, the 10% margin check provides zero protection when all validators query the same compromised source.

### Finding Description

**Step 1 – First-success oracle strategy with no bounds check.**

`spawn_query()` iterates `url_header_list` and returns on the first successful HTTP response: [1](#0-0) 

`resolve_query()` validates only JSON structure and `decimals == 18`; the `rate` value itself is accepted as any `u128` with no range check: [2](#0-1) 

**Step 2 – Rate flows into block-committed gas prices.**

`get_l1_prices_in_fri_and_wei_and_conversion_rate()` uses the oracle rate to convert L1 wei prices to fri prices that are placed in `ProposalInit`: [3](#0-2) 

**Step 3 – Fri prices are hashed into the block hash.**

`gas_prices_to_hash()` chains `l1_gas_price.price_in_fri` and `l1_data_gas_price.price_in_fri` into the Poseidon block hash: [4](#0-3) 

**Step 4 – The margin check is circular when all validators share the same oracle.**

`is_block_info_valid()` computes the validator's reference price from the same `l1_gas_price_provider` (which calls the same oracle), then checks `within_margin(proposed, local, 10%)`: [5](#0-4) 

When all validators use the same oracle URL (the production deployment configures a single endpoint), `local == proposed` (both derived from the same compromised source), so `within_margin` returns `true` trivially and the corrupted price is accepted.

**Step 5 – Production deployment confirms single-oracle configuration.** [6](#0-5) 

The `EthToStrkOracleConfig` default also ships with a single URL entry: [7](#0-6) 

### Impact Explanation

Wrong `l1_gas_price_fri` / `l1_data_gas_price_fri` committed into the block header produce:

1. **Wrong block hash** — `gas_prices_to_hash()` output is part of `calculate_block_hash()`, so every block produced under the compromised oracle carries an incorrect commitment.
2. **Wrong fee accounting** — the blockifier uses these prices via `BlockInfo.gas_prices` to charge transaction fees; all transactions in the block pay incorrect amounts.
3. **Wrong RPC fee estimation** — `tx_execution_output_to_fee_estimation()` reads `block_context.block_info().gas_prices`, returning authoritative-looking wrong values to callers.

Matches: **Critical. Incorrect fee, gas, bouncer, resource accounting, refund, balance, or L1 gas price effect with economic impact.**

### Likelihood Explanation

All validators in a typical deployment point to the same oracle service. A single DNS hijack, API-key compromise, or supply-chain attack on that service simultaneously corrupts every validator's reference price. Because the margin check is self-referential (validator checks against its own oracle-derived value), no honest validator can detect or reject the manipulated price.

### Recommendation

1. **Aggregate, don't fail-over**: query all URLs in `url_header_list` and use the median rate rather than the first successful response.
2. **Add rate-change bounds**: reject any oracle response that deviates more than a configurable percentage (e.g., 20%) from the most recently cached rate.
3. **Cross-check against on-chain data**: compare the oracle rate against a secondary on-chain reference (e.g., a Starknet-native TWAP) before committing it to a block.

### Proof of Concept

1. Attacker compromises the oracle endpoint (e.g., via DNS hijacking) used by all validators.
2. Oracle returns `{"price": "<true_rate * 1.09 in hex>", "decimals": 18}` — 9% above the true rate, within the 10% margin.
3. Every validator's `EthToStrkOracleClient::eth_to_fri_rate()` caches the inflated rate.
4. Proposer builds a block: `l1_gas_price_fri = l1_gas_price_wei * inflated_rate / WEI_PER_ETH`.
5. Each validator calls `is_block_info_valid()` → `get_l1_prices_in_fri_and_wei()` → same compromised oracle → same inflated price → `within_margin(inflated, inflated, 10)` = `true` (diff = 0).
6. Block is accepted; `gas_prices_to_hash()` commits the inflated fri prices into the block hash.
7. All transactions in the block are charged ~9% excess fees; the surplus accrues to the sequencer, constituting direct economic extraction.

### Citations

**File:** crates/apollo_l1_gas_price/src/eth_to_strk_oracle.rs (L113-135)
```rust
            for (i, url_and_headers) in
                url_header_list.iter().cycle().skip(initial_index).take(list_len).enumerate()
            {
                let UrlAndHeaderMap { mut url, headers } = url_and_headers.clone();
                url.query_pairs_mut().append_pair("timestamp", &adjusted_timestamp.to_string());
                let result = tokio::time::timeout(Duration::from_secs(query_timeout_sec), async {
                    let response = client
                        .get(url.clone())
                        .headers(headers.peek_secret().clone())
                        .send()
                        .await?;
                    let body = response.text().await?;
                    let rate = resolve_query(body)?;
                    Ok::<_, EthToStrkOracleClientError>(rate)
                })
                .await;

                match result {
                    Ok(Ok(rate)) => {
                        let idx = (i + initial_index) % list_len;
                        index_clone.store(idx, Ordering::SeqCst);
                        debug!("Resolved query to {url} with rate {rate}");
                        return Ok(rate);
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

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L147-174)
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
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L417-443)
```rust
pub fn gas_prices_to_hash(
    l1_gas_price: &GasPricePerToken,
    l1_data_gas_price: &GasPricePerToken,
    l2_gas_price: &GasPricePerToken,
    block_hash_version: &BlockHashVersion,
) -> Vec<Felt> {
    if block_hash_version >= &BlockHashVersion::V0_13_4 {
        vec![
            HashChain::new()
                .chain(&STARKNET_GAS_PRICES0)
                .chain(&l1_gas_price.price_in_wei.0.into())
                .chain(&l1_gas_price.price_in_fri.0.into())
                .chain(&l1_data_gas_price.price_in_wei.0.into())
                .chain(&l1_data_gas_price.price_in_fri.0.into())
                .chain(&l2_gas_price.price_in_wei.0.into())
                .chain(&l2_gas_price.price_in_fri.0.into())
                .get_poseidon_hash(),
        ]
    } else {
        vec![
            l1_gas_price.price_in_wei.0.into(),
            l1_gas_price.price_in_fri.0.into(),
            l1_data_gas_price.price_in_wei.0.into(),
            l1_data_gas_price.price_in_fri.0.into(),
        ]
    }
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

**File:** crates/apollo_l1_gas_price_provider_config/src/config.rs (L76-91)
```rust
impl Default for EthToStrkOracleConfig {
    fn default() -> Self {
        Self {
            url_header_list: Some(vec![
                UrlAndHeaders {
                    url: Url::parse("https://api.example.com/api").expect("Invalid URL"),
                    headers: BTreeMap::new(),
                }
                .into(),
            ]),
            lag_interval_seconds: 1,
            max_cache_size: 100,
            query_timeout_sec: 10,
        }
    }
}
```
