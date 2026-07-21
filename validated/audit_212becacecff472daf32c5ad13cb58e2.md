### Title
Unbounded Single-Call `eth_getLogs` in `fetch_events` Causes Perpetual Initialization Loop, Freezing L1→L2 Message Admission — (`crates/apollo_l1_provider/src/l1_scraper.rs`)

### Summary

The `L1Scraper::fetch_events` method issues a single, unchunked `eth_getLogs` call spanning the entire startup-rewind window (up to ~2700 blocks in production). When the aggregate log payload for that range exceeds the Ethereum node's response-size limit (~10 MB), the call returns an `EthereumBaseLayerError`, which is wrapped as `L1ScraperError::BaseLayerError`. The outer `retry_until_base_layer_success` loop retries `initialize` indefinitely — but each retry re-fetches the *same* (and growing) range, so the call fails every time. The `L1Provider` is never initialized, and all valid L1 handler transactions are permanently excluded from block proposals.

### Finding Description

**Root cause — `fetch_events` issues one unbounded `eth_getLogs` call:** [1](#0-0) 

```rust
let scraping_result = self
    .base_layer
    .events(scraping_start_number..=latest_l1_block.number, &self.tracked_event_identifiers)
    .await;
```

`EthereumBaseLayerContract::events` passes the full range to a single `get_logs` call with no chunking: [2](#0-1) 

Any error (size-limit, timeout) is propagated as `L1ScraperError::BaseLayerError`.

**Retry loop never escapes — range grows on every attempt:** [3](#0-2) 

`retry_until_base_layer_success` retries `initialize` on every `BaseLayerError`. Inside `initialize`, `fetch_events` is called again; `latest_l1_block.number` is re-queried each time, so the range `scraping_start_number..=latest_l1_block.number` grows monotonically. The call never succeeds.

**The developers already acknowledged the batch-sending gap but left it unimplemented:** [4](#0-3) 

```rust
// If this gets too high, send in batches.
let initialize_result =
    self.l1_provider_client.initialize(historic_l2_height, events).await;
```

**Production startup-rewind window:** [5](#0-4) 

`startup_rewind_time_seconds = 21600` (6 h). With `l1_block_time_seconds = 12` and the 50 % safety margin applied in `fetch_start_block`, the scraper rewinds ≈ 2700 L1 blocks on every restart. [6](#0-5) 

### Impact Explanation

`L1Provider::initialize` is never called successfully, so `ProviderState` stays `Uninitialized`. The batcher's `ProposeTransactionProvider` cannot obtain L1 handler transactions from the provider, meaning every valid `LogMessageToL2` event deposited on L1 is silently dropped from all future block proposals. This matches **High — Mempool/

### Citations

**File:** crates/apollo_l1_provider/src/l1_scraper.rs (L143-151)
```rust
        let blocks_in_interval = self.config.startup_rewind_time_seconds.as_secs()
            / self.config.l1_block_time_seconds.as_secs();
        debug!("Blocks in interval: {blocks_in_interval}");

        // Add 50% safety margin.
        let safe_blocks_in_interval = blocks_in_interval + blocks_in_interval / 2;
        debug!("Safe blocks in interval: {safe_blocks_in_interval}");

        let l1_block_number_rewind = latest_l1_block_number.saturating_sub(safe_blocks_in_interval);
```

**File:** crates/apollo_l1_provider/src/l1_scraper.rs (L217-219)
```rust
        // If this gets too high, send in batches.
        let initialize_result =
            self.l1_provider_client.initialize(historic_l2_height, events).await;
```

**File:** crates/apollo_l1_provider/src/l1_scraper.rs (L293-297)
```rust
        let scraping_start_number = scrape_from_this_l1_block.number + 1;
        let scraping_result = self
            .base_layer
            .events(scraping_start_number..=latest_l1_block.number, &self.tracked_event_identifiers)
            .await;
```

**File:** crates/apollo_l1_provider/src/l1_scraper.rs (L378-392)
```rust
        loop {
            match func(self).await {
                Err(L1ScraperError::BaseLayerError(e)) => {
                    warn!("Error while {description}: {e}");
                    L1_MESSAGE_SCRAPER_BASELAYER_ERROR_COUNT.increment(1);
                    // TODO(guyn): consider using a different interval here? Maybe doesn't really
                    // matter.
                    sleep(self.config.polling_interval_seconds).await;
                    continue;
                }
                // Return a non-base layer error or success.
                e => return e,
            }
        }
    }
```

**File:** crates/papyrus_base_layer/src/ethereum_base_layer_contract.rs (L161-170)
```rust
        let filter = EthEventFilter::new()
            .select(block_range.clone())
            .events(event_types_to_filter)
            .address(immutable_self.config.starknet_contract_address);

        let matching_logs = tokio::time::timeout(
            immutable_self.config.timeout_millis,
            immutable_self.contract.provider().get_logs(&filter),
        )
        .await??;
```

**File:** crates/apollo_deployments/resources/app_configs/l1_scraper_config.json (L1-5)
```json
{
  "l1_scraper_config.finality": 10,
  "l1_scraper_config.polling_interval_seconds": 30,
  "l1_scraper_config.startup_rewind_time_seconds": 21600
}
```
