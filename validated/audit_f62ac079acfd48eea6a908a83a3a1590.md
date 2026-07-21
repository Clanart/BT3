### Title
Zero ETH→STRK Oracle Rate Bypasses Fallback and Silently Produces Zero FRI Gas Prices, Halting Block Production — (`crates/apollo_l1_gas_price/src/eth_to_strk_oracle.rs`)

---

### Summary

`resolve_query` accepts a parsed rate of `0` from the oracle API without validation. Because `GasPrice::wei_to_fri(0)` returns `Ok(GasPrice(0))` instead of an error, a zero rate is treated as a successful conversion, bypasses all fallback paths, and propagates zero FRI gas prices into `ProposalInit`. Every subsequent call to `convert_to_sn_api_block_info` then fails with `ZeroGasPrice`, halting both block building and block validation.

---

### Finding Description

**Step 1 — Zero rate accepted by `resolve_query`**

`resolve_query` parses the `"price"` field from the oracle JSON response and returns it directly as `Ok(rate)`. There is no check for `rate == 0`. [1](#0-0) 

If the oracle returns `{"price": "0x0", "decimals": 18}`, the function returns `Ok(0)`. The value is then cached in `cached_prices` and returned to callers as a valid rate. [2](#0-1) 

**Step 2 — `wei_to_fri(0)` succeeds, fallback is never triggered**

In `get_l1_prices_in_fri_and_wei_and_conversion_rate`, when both `eth_to_fri_rate` and `price_info` are `Ok`, the code calls `L1PricesInFri::convert_from_wei(&prices_in_wei, eth_to_fri_rate)`. [3](#0-2) 

`convert_from_wei` calls `wei.l1_gas_price.wei_to_fri(eth_to_fri_rate)`. When `eth_to_fri_rate = 0`:

```
self * 0 = 0   →   0 / WEI_PER_ETH = 0   →   Ok(GasPrice(0))
``` [4](#0-3) 

`wei_to_fri(0)` does **not** return an error (unlike `fri_to_wei(0)`, which correctly returns `Err("FRI to ETH rate must be non-zero")`). The conversion succeeds with `GasPrice(0)`, so `l1_gas_prices_fri_result = Ok(...)` and the function returns immediately with zero FRI prices and `eth_to_fri_rate = 0`. The fallback to previous block info or `DEFAULT_ETH_TO_FRI_RATE` is never reached. [5](#0-4) 

**Step 3 — Zero prices flow into `ProposalInit`**

The proposer builds a `ProposalInit` with `l1_gas_price_fri = GasPrice(0)` and `l1_data_gas_price_fri = GasPrice(0)`. [6](#0-5) 

**Step 4 — `convert_to_sn_api_block_info` fails on both proposer and validator**

`convert_to_sn_api_block_info` calls `NonzeroGasPrice::new(init.l1_gas_price_fri)`, which returns `Err(ZeroGasPrice)` for a zero price. This is called during both block building (`initiate_build`) and block validation (`initiate_validation`). [7](#0-6) [8](#0-7) 

**Step 5 — Gas price margin check passes for zero**

The `is_block_info_valid` check in the validator calls `within_margin(GasPrice(0), GasPrice(0), margin)`. Since `abs_diff(0, 0) = 0 ≤ GAS_PRICE_ABS_DIFF_MARGIN`, this passes, so the validator does not reject the proposal at the margin check — it only fails later at `convert_to_sn_api_block_info`. [9](#0-8) 

**Step 6 — Fallback `calculate_eth_to_fri_rate` does check for zero, but is never reached**

The fallback path does correctly guard against zero: [10](#0-9) 

But because `wei_to_fri(0)` succeeds, the primary path returns before the fallback is ever attempted.

---

### Impact Explanation

When the ETH→STRK oracle returns a rate of zero (due to oracle malfunction, misconfiguration, or a market event), the sequencer cannot build or validate any block. Both the proposer and all validators fail at `convert_to_sn_api_block_info` with `ZeroGasPrice`. The sequencer halts entirely. This maps to **Critical — Incorrect fee, gas, bouncer, resource accounting, refund, balance, or L1 gas price effect with economic impact**, because zero FRI gas prices, if they reached execution, would make all STRK-denominated fees zero; and the immediate effect is a complete liveness failure of the sequencer.

---

### Likelihood Explanation

The ETH→STRK oracle is an external HTTP API. A zero rate can occur due to:
- Oracle service bug or misconfiguration returning `"0x0"`
- Network partition causing a stale/corrupt response
- A deliberate attack on the oracle data source

The `resolve_query` function has no guard against this. The zero rate is cached and reused for subsequent blocks within the same quantized timestamp window, extending the outage. [11](#0-10) 

---

### Recommendation

1. **In `resolve_query`**: add an explicit zero check after parsing:
   ```rust
   if rate == 0 {
       return Err(EthToStrkOracleClientError::ParseError(
           "ETH to STRK rate must be non-zero".to_string()
       ));
   }
   ``` [1](#0-0) 

2. **In `GasPrice::wei_to_fri`**: mirror the guard in `fri_to_wei` — return an error when `eth_to_fri_rate == 0`:
   ```rust
   if eth_to_fri_rate == 0 {
       return Err(StarknetApiError::GasPriceConversionError(
           "ETH to FRI rate must be non-zero".to_string()
       ));
   }
   ``` [4](#0-3) 

   This ensures the fallback path in `get_l1_prices_in_fri_and_wei_and_conversion_rate` is triggered when the oracle returns zero, rather than silently propagating zero prices.

---

### Proof of Concept

1. Configure the ETH→STRK oracle mock to return `{"price": "0x0", "decimals": 18}`.
2. Call `EthToStrkOracleClient::eth_to_fri_rate(timestamp)` — it returns `Ok(0)`.
3. Call `get_l1_prices_in_fri_and_wei_and_conversion_rate` with this client — it returns `(L1PricesInFri { l1_gas_price: GasPrice(0), l1_data_gas_price: GasPrice(0) }, ..., 0)` without entering the fallback branch.
4. Build a `ProposalInit` with these prices (`l1_gas_price_fri = GasPrice(0)`).
5. Call `convert_to_sn_api_block_info(&init)` — it returns `Err(ZeroGasPrice)`.
6. Both `build_proposal` and `validate_proposal` fail; no block is produced or accepted.

The existing test `fri_to_wei_errors_on_conversion_rate_zero` confirms `fri_to_wei(0)` errors, but there is no corresponding test for `wei_to_fri(0)`, confirming the asymmetry is untested. [12](#0-11)

### Citations

**File:** crates/apollo_l1_gas_price/src/eth_to_strk_oracle.rs (L166-167)
```rust
    let rate = u128::from_str_radix(price.trim_start_matches("0x"), 16)
        .expect("Failed to parse price as u128");
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

**File:** crates/apollo_l1_gas_price/src/eth_to_strk_oracle.rs (L247-252)
```rust
        // Make sure to cache the result.
        cache.put(quantized_timestamp, rate);
        // We don't need to come back to this query since we have the result in cache.
        queries.pop(&quantized_timestamp);
        debug!("Caching conversion rate for timestamp {timestamp}, with rate {rate}");
        Ok(rate)
```

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L158-181)
```rust
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

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L290-305)
```rust
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
```

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L509-514)
```rust
    if eth_to_fri_rate == 0 {
        return Err(StarknetApiError::GasPriceConversionError(format!(
            "Eth to fri rate is zero. Previous block info: {:?}",
            block_info
        )));
    }
```

**File:** crates/starknet_api/src/block.rs (L424-437)
```rust
    pub fn wei_to_fri(self, eth_to_fri_rate: u128) -> Result<GasPrice, StarknetApiError> {
        // We use integer division since wei * eth_to_fri_rate is expected to be high enough to not
        // cause too much precision loss.
        Ok(self
            .checked_mul_u128(eth_to_fri_rate)
            .ok_or_else(|| {
                StarknetApiError::GasPriceConversionError(format!(
                    "Gas price is too high: {:?}, eth to fri rate: {:?}",
                    self, eth_to_fri_rate
                ))
            })?
            .checked_div(WEI_PER_ETH)
            .expect("WEI_PER_ETH must be non-zero"))
    }
```

**File:** crates/starknet_api/src/block.rs (L438-452)
```rust
    pub fn fri_to_wei(self, eth_to_fri_rate: u128) -> Result<GasPrice, StarknetApiError> {
        self.checked_mul_u128(WEI_PER_ETH)
            .ok_or_else(|| {
                StarknetApiError::GasPriceConversionError(format!(
                    "Gas price is too high: {:?}, eth to fri rate: {:?}",
                    self, eth_to_fri_rate
                ))
            })?
            .checked_div(eth_to_fri_rate)
            .ok_or_else(|| {
                StarknetApiError::GasPriceConversionError(
                    "FRI to ETH rate must be non-zero".to_string(),
                )
            })
    }
```

**File:** crates/starknet_api/src/block.rs (L524-528)
```rust
    pub fn new(price: GasPrice) -> Result<Self, StarknetApiError> {
        if price.0 == 0 {
            return Err(StarknetApiError::ZeroGasPrice);
        }
        Ok(Self(price))
```

**File:** crates/apollo_consensus_orchestrator/src/utils_test.rs (L43-50)
```rust
        l2_gas_price_fri: args.l2_gas_price,
        l1_gas_price_wei: l1_prices_wei.l1_gas_price,
        l1_data_gas_price_wei: l1_prices_wei.l1_data_gas_price,
        l1_gas_price_fri: l1_prices_fri.l1_gas_price,
        l1_data_gas_price_fri: l1_prices_fri.l1_data_gas_price,
        starknet_version: starknet_api::block::StarknetVersion::LATEST,
        version_constant_commitment: Default::default(),
    }
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

**File:** crates/apollo_protobuf/src/consensus_test.rs (L41-49)
```rust
#[test]
fn fri_to_wei_errors_on_conversion_rate_zero() {
    assert!(
        GasPrice(5).fri_to_wei(0)
            == Err(StarknetApiError::GasPriceConversionError(
                "FRI to ETH rate must be non-zero".to_string()
            ))
    );
}
```
