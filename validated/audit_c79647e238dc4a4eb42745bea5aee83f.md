### Title
Stale ETH→FRI Conversion Rate Silently Accepted in Block Gas Price Validation Corrupts Committed `l1_gas_price_fri` — (`crates/apollo_l1_gas_price/src/eth_to_strk_oracle.rs`)

---

### Summary

`EthToStrkOracleClient::eth_to_fri_rate()` silently returns the previous quantized-bucket's cached rate when the current query is not yet resolved. Both the proposer path (`initiate_build`) and the validator path (`is_block_info_valid`) call the same `get_l1_prices_in_fri_and_wei` helper, which in turn calls the oracle. Because both sides receive the same stale `Ok(rate)` value, the `within_margin` check in `is_block_info_valid` passes, and the block is committed with `l1_gas_price_fri` / `l1_data_gas_price_fri` derived from an outdated ETH→FRI conversion rate. This is the direct sequencer analog of the external report's "cached price used in a critical function": the validation gate that is supposed to catch wrong prices is neutralised because it reads from the same stale source as the proposer.

---

### Finding Description

**Step 1 – Oracle silently returns a stale rate**

In `EthToStrkOracleClient::eth_to_fri_rate()`, when the async query for the current quantized timestamp is not yet finished, the implementation falls back to the previous bucket's cached value and returns it as a plain `Ok(rate)` — indistinguishable from a fresh result:

```rust
if !handle.is_finished() {
    // If the previous quantized timestamp is in the cache, use it.
    if let Some(rate) = cache.get(&(quantized_timestamp - NUMBER_OF_TIMESTAMPS_BACK)) {
        return Ok(*rate);   // ← stale rate, no error, no flag
    }
    return Err(EthToStrkOracleClientError::QueryNotReadyError(timestamp));
}
``` [1](#0-0) 

The staleness window equals `lag_interval_seconds` (default 60 s). During high ETH/STRK volatility this can represent a material price deviation.

**Step 2 – Stale rate flows into the proposer's `ProposalInit`**

`initiate_build` calls `get_l1_prices_in_fri_and_wei`, which calls `get_l1_prices_in_fri_and_wei_and_conversion_rate`. That function calls `get_eth_to_fri_rate(timestamp)` and, if it returns `Ok(stale_rate)`, uses it to compute `l1_gas_price_fri` and `l1_data_gas_price_fri` for the `ProposalInit`:

```rust
let (l1_prices_fri, l1_prices_wei) = get_l1_prices_in_fri_and_wei(
    args.deps.l1_gas_price_provider.clone(),
    timestamp,
    args.previous_block_info.as_ref(),
    &args.gas_price_params,
).await;
let init = ProposalInit {
    l1_gas_price_fri: l1_prices_fri.l1_gas_price,
    l1_data_gas_price_fri: l1_prices_fri.l1_data_gas_price,
    ...
};
``` [2](#0-1) 

**Step 3 – Validator uses the identical fallback, neutralising the check**

`is_block_info_valid` calls the same `get_l1_prices_in_fri_and_wei` with `init_proposed.timestamp`. Because the oracle is in the same state (query not yet resolved), it returns the same stale rate. The validator therefore computes the same stale FRI prices as the proposer:

```rust
let (l1_gas_prices_fri, _l1_gas_prices_wei) = get_l1_prices_in_fri_and_wei(
    l1_gas_price_provider,
    init_proposed.timestamp,
    block_info_validation.previous_block_info.as_ref(),
    gas_price_params,
).await;
``` [3](#0-2) 

The subsequent `within_margin` check compares the proposer's stale-derived price against the validator's stale-derived price — both are equal, so the check always passes:

```rust
if !(within_margin(l1_gas_price_fri_proposed, l1_gas_price_fri, l1_gas_price_margin_percent)
    && within_margin(...)) {
    return Err(ValidateProposalError::InvalidBlockInfo(...));
}
``` [4](#0-3) 

**Step 4 – Stale prices are committed to the block header**

`convert_to_sn_api_block_info` converts the `ProposalInit` (containing the stale FRI prices) into the `BlockInfo` that is passed to the batcher and ultimately written to storage as the canonical block header:

```rust
let l1_gas_price_fri = NonzeroGasPrice::new(init.l1_gas_price_fri)?;
let l1_data_gas_price_fri = NonzeroGasPrice::new(init.l1_data_gas_price_fri)?;
``` [5](#0-4) 

These values are stored in `StorageBlockHeader.l1_gas_price` and `l1_data_gas_price` and are used for fee charging of every transaction in the block. [6](#0-5) 

---

### Impact Explanation

Every transaction in the committed block has its L1 gas fee charged at a price derived from a stale ETH→FRI conversion rate. If the rate has moved (e.g., ETH appreciated), users are undercharged, creating a protocol loss; if ETH depreciated, users are overcharged. Because the block is committed and irreversible, and because the validator's check is neutralised by the same stale data, there is no on-chain mechanism to detect or correct the error. This matches the allowed impact: **"Critical. Incorrect fee, gas, bouncer, resource accounting, refund, balance, or L1 gas price effect with economic impact."**

---

### Likelihood Explanation

The stale-rate path is triggered at the start of every new quantized time bucket (every `lag_interval_seconds` seconds, default 60 s). During that window the async query for the new bucket is in flight but not yet resolved. Any block proposed in that window — which is a normal, unprivileged operation — will use the stale rate. The test `eth_to_fri_rate_uses_prev_cache_when_query_not_ready` confirms this is a reachable, tested code path. [7](#0-6) 

---

### Recommendation

The validator's `is_block_info_valid` should not silently accept a stale oracle rate. Two complementary fixes:

1. **Oracle: distinguish stale from fresh.** `eth_to_fri_rate` should return a typed result that distinguishes `FreshRate(u128)` from `StaleRate(u128)` so callers can decide whether to accept it.

2. **Validator: require freshness.** In `is_block_info_valid`, if the oracle returns a stale rate, the validator should either reject the proposal or widen the margin only by an explicit, bounded staleness allowance — not silently accept any price that happens to match the same stale value.

3. **Proposer: log and metric.** When the stale fallback is used, emit a distinct metric (beyond the existing `debug!`) so operators can detect and alert on prolonged staleness.

---

### Proof of Concept

```
T=0:   New quantized bucket Q begins. Oracle spawns async query for Q.
T=5s:  Block N proposal starts. Oracle query for Q not yet resolved.
       eth_to_fri_rate(T=5) → Ok(rate_Q-1)   [stale, from bucket Q-1]
       l1_gas_price_fri = fresh_l1_gas_wei * rate_Q-1 / 10^18
       ProposalInit.l1_gas_price_fri = <stale value>

T=6s:  Validator receives ProposalInit.
       eth_to_fri_rate(T=5) → Ok(rate_Q-1)   [same stale rate]
       expected_l1_gas_price_fri = fresh_l1_gas_wei * rate_Q-1 / 10^18
       within_margin(proposed, expected) → true  ← check passes

T=7s:  Block N committed with l1_gas_price_fri derived from rate_Q-1.
       All transactions in block N charged fees at the stale rate.
       If ETH/STRK moved 5% during bucket Q-1→Q, every fee is wrong by 5%.
```

The root cause is at: [8](#0-7) 

flowing through: [9](#0-8) 

and committed via: [2](#0-1)

### Citations

**File:** crates/apollo_l1_gas_price/src/eth_to_strk_oracle.rs (L214-225)
```rust
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
```

**File:** crates/apollo_consensus_orchestrator/src/build_proposal.rs (L156-176)
```rust
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
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L286-292)
```rust
    let (l1_gas_prices_fri, _l1_gas_prices_wei) = get_l1_prices_in_fri_and_wei(
        l1_gas_price_provider,
        init_proposed.timestamp,
        block_info_validation.previous_block_info.as_ref(),
        gas_price_params,
    )
    .await;
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L302-319)
```rust
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

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L299-303)
```rust
    let l1_gas_price_fri = NonzeroGasPrice::new(init.l1_gas_price_fri)?;
    let l1_data_gas_price_fri = NonzeroGasPrice::new(init.l1_data_gas_price_fri)?;
    let l1_gas_price_wei = NonzeroGasPrice::new(init.l1_gas_price_wei)?;
    let l1_data_gas_price_wei = NonzeroGasPrice::new(init.l1_data_gas_price_wei)?;
    let l2_gas_price_fri = NonzeroGasPrice::new(init.l2_gas_price_fri)?;
```

**File:** crates/apollo_storage/src/header.rs (L81-85)
```rust
    pub l1_gas_price: GasPricePerToken,
    /// The L1 data gas price per token.
    pub l1_data_gas_price: GasPricePerToken,
    /// The L2 gas price per token.
    pub l2_gas_price: GasPricePerToken,
```

**File:** crates/apollo_l1_gas_price/src/eth_to_strk_oracle_test.rs (L76-160)
```rust
#[tokio::test]
async fn eth_to_fri_rate_uses_prev_cache_when_query_not_ready() {
    const EXPECTED_RATE: u128 = 123456;
    let expected_rate_hex = format!("0x{EXPECTED_RATE:x}");
    let different_rate = EXPECTED_RATE * 2;
    let different_rate_hex = format!("0x{:x}", different_rate);
    const LAG_INTERVAL_SECONDS: u64 = 60;

    const TIMESTAMP1: u64 = 1234567890;
    const TIMESTAMP2: u64 = TIMESTAMP1 + LAG_INTERVAL_SECONDS;

    let quantized_timestamp1 = (TIMESTAMP1 - LAG_INTERVAL_SECONDS) / LAG_INTERVAL_SECONDS;
    let adjusted_timestamp1 = quantized_timestamp1 * LAG_INTERVAL_SECONDS;
    let quantized_timestamp2 = (TIMESTAMP2 - LAG_INTERVAL_SECONDS) / LAG_INTERVAL_SECONDS;
    let adjusted_timestamp2 = quantized_timestamp2 * LAG_INTERVAL_SECONDS;

    let mut server = mockito::Server::new_async().await;

    // Define a mock response for a GET request with a specific adjusted_timestamp in the path
    let _mock_response1 = server
        .mock("GET", "/") // Match the base path only.
        .match_query(mockito::Matcher::UrlEncoded("timestamp".into(), adjusted_timestamp1.to_string()))
        .with_header("Content-Type", "application/json")
        .with_body(
            json!({
                "price": expected_rate_hex,
                "decimals": 18
            })
            .to_string(),
        )
        .create();
    // Second response (same matcher) returns a different value on the next call.
    let _mock_response2 = server
        .mock("GET", "/")
        .match_query(mockito::Matcher::UrlEncoded(
            "timestamp".into(),
            adjusted_timestamp2.to_string(),
        ))
        .with_header("Content-Type", "application/json")
        .with_body(
            json!({
                "price": different_rate_hex,
                "decimals": 18
            })
            .to_string(),
        )
        .create();

    let url_and_headers = UrlAndHeaders {
        url: Url::parse(&server.url()).unwrap(),
        headers: BTreeMap::new(), // No additional headers needed for this test.
    };
    let url_header_list = Some(vec![url_and_headers.into()]);
    let config = EthToStrkOracleConfig {
        url_header_list,
        lag_interval_seconds: LAG_INTERVAL_SECONDS,
        ..Default::default()
    };
    let client = EthToStrkOracleClient::new(config.clone());

    // First request should fail because the cache is empty.
    assert!(client.eth_to_fri_rate(TIMESTAMP1).await.is_err());
    // Wait for the query to resolve.
    while client.eth_to_fri_rate(TIMESTAMP1).await.is_err() {
        tokio::task::yield_now().await; // Don't block the executor.
    }
    let rate1 = client.eth_to_fri_rate(TIMESTAMP1).await.unwrap();
    assert_eq!(rate1, EXPECTED_RATE);
    // Second request should resolve immediately due to the cache.
    let rate2 = client.eth_to_fri_rate(TIMESTAMP2).await.unwrap();
    assert_eq!(rate2, EXPECTED_RATE);

    // Wait for the query to resolve, and the price to be updated.
    for _ in 0..100 {
        let current_rate = client.eth_to_fri_rate(TIMESTAMP2).await.unwrap();
        if current_rate > EXPECTED_RATE {
            break;
        }
        tokio::time::sleep(Duration::from_millis(1)).await;
    }

    // Third request should already successfully get the query from the server.
    let rate3 = client.eth_to_fri_rate(TIMESTAMP2).await.unwrap();
    assert_eq!(rate3, different_rate);
}
```
