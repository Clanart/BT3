### Title
Validator Accepts Stale L1 Gas Prices via Proposer-Controlled Timestamp in `is_block_info_valid` — (File: `crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

---

### Summary

In `is_block_info_valid`, the validator uses the proposer-supplied `init_proposed.timestamp` to look up L1 gas prices for validation. The lower bound for the timestamp is `last_block_timestamp` (an event-driven value — when the last block was produced), not a schedule-driven reference like `now - tolerance`. A proposer can therefore set a timestamp early in the allowed window (equal to `last_block_timestamp`) to anchor the gas-price lookup to an earlier L1 period. Because the validator uses the same proposer-supplied timestamp for its own lookup, the `within_margin` check always passes, and the block is accepted with stale L1 gas prices committed into the block hash.

---

### Finding Description

`is_block_info_valid` enforces two timestamp bounds:

```
init_proposed.timestamp >= last_block_timestamp   // lower: event-driven
init_proposed.timestamp <= now + block_timestamp_window_seconds  // upper: schedule-driven
```

It then fetches L1 gas prices keyed on the **proposer-supplied** timestamp:

```rust
let (l1_gas_prices_fri, _l1_gas_prices_wei) = get_l1_prices_in_fri_and_wei(
    l1_gas_price_provider,
    init_proposed.timestamp,          // ← proposer-controlled
    block_info_validation.previous_block_info.as_ref(),
    gas_price_params,
)
.await;
```

The subsequent `within_margin` check verifies only that the proposed prices are consistent with the prices fetched at `init_proposed.timestamp`. Because both the proposer and the validator query the same timestamp, the check is tautologically satisfied for any timestamp the proposer chooses within the allowed window.

The root cause is the asymmetry between the two bounds: the upper bound is schedule-driven (`now + 1 s`), but the lower bound is event-driven (`last_block_timestamp`). When block production is slow (network hiccups, high round counts, etc.), `last_block_timestamp` can lag `now` by tens of seconds to minutes. The proposer can exploit this gap to anchor the gas-price lookup to a period of lower L1 fees.

This is the direct sequencer analog of M-14: M-14 uses `lastRepaidTimestamp` (when the borrower last acted) instead of the payment due date (a schedule-driven reference). Here, the gas-price validation uses `init_proposed.timestamp` (when the proposer chose to timestamp the block) instead of the validator's own `now`.

The corrupted values flow directly into the block hash:

- `PartialBlockHashComponents::l1_gas_price` / `l1_data_gas_price` are set from `init_proposed.l1_gas_price_fri` / `init_proposed.l1_data_gas_price_fri`.
- `PartialBlockHashComponents::new` is called in `BlockExecutionArtifacts::new`, which feeds `calculate_block_hash`.
- The resulting `PartialBlockHash` becomes the `ProposalCommitment` that consensus finalises. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

---

### Impact Explanation

`l1_gas_price_fri` and `l1_data_gas_price_fri` are committed into `PartialBlockHashComponents` and ultimately into the finalised block hash via `calculate_block_hash`. A proposer who sets `init_proposed.timestamp = last_block_timestamp` causes every transaction in the block to be charged fees based on stale L1 gas prices rather than current ones. This is a direct incorrect-fee / incorrect-L1-gas-price effect with economic impact: users underpay (or overpay) fees, and the committed block hash encodes the wrong price state. [5](#0-4) [6](#0-5) 

---

### Likelihood Explanation

The trigger requires being the designated proposer for a block. In the multi-validator Tendermint-style consensus the sequencer implements, the proposer role rotates; any registered validator can become the proposer for a given height/round. The exploitation window equals `now − last_block_timestamp`, which grows whenever block production is delayed (high round counts, network partitions). With `lag_margin_seconds = 60 s` and `number_of_blocks_for_mean = 300` blocks in the L1 gas price provider, a 30–60 second gap between blocks is sufficient to select a meaningfully different gas-price window. [7](#0-6) [8](#0-7) 

---

### Recommendation

Replace the event-driven lower bound with a schedule-driven one. The validator should look up gas prices at `now` (or `now − tolerance`) rather than at the proposer-supplied timestamp:

```rust
// Instead of:
get_l1_prices_in_fri_and_wei(provider, init_proposed.timestamp, ...)

// Use:
get_l1_prices_in_fri_and_wei(provider, clock.unix_now(), ...)
```

Additionally, tighten the lower timestamp bound to be symmetric with the upper bound:

```rust
if init_proposed.timestamp < now.saturating_sub(block_timestamp_window_seconds) {
    return Err(InvalidBlockInfo(...));
}
```

This mirrors the fix recommended for M-14: replace the event-driven reference (`lastRepaidTimestamp` / `last_block_timestamp`) with a schedule-driven one (`payment_due_date` / `now − tolerance`).

---

### Proof of Concept

1. Last block produced at time `T`; current time `now = T + 60` (60-second gap due to a slow round).
2. L1 gas prices at time `T − 60` (the lag-adjusted window for `timestamp = T`) are 40 % lower than at `now − 60` (the lag-adjusted window for `timestamp = now`).
3. Proposer sets `init_proposed.timestamp = T` (passes lower bound: `T >= T`; passes upper bound: `T <= (T+60)+1`).
4. Proposer computes `l1_gas_price_fri` from `get_price_info(BlockTimestamp(T))` — the cheaper historical window.
5. Validator calls `is_block_info_valid`:
   - Timestamp bounds pass.
   - Validator calls `get_l1_prices_in_fri_and_wei(provider, T, ...)` — same timestamp, same cheaper prices.
   - `within_margin` passes trivially.
6. Block is finalised with `l1_gas_price_fri` 40 % below current market rate, committed into the block hash.
7. All transactions in the block pay fees at the stale, artificially low L1 gas price. [9](#0-8) [10](#0-9)

### Citations

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L244-319)
```rust
#[instrument(level = "warn", skip_all, fields(?block_info_validation, ?init_proposed))]
async fn is_block_info_valid(
    block_info_validation: &BlockInfoValidation,
    init_proposed: &ProposalInit,
    clock: &dyn Clock,
    l1_gas_price_provider: Arc<dyn L1GasPriceProviderClient>,
    gas_price_params: &GasPriceParams,
) -> ValidateProposalResult<()> {
    let now: u64 = clock.unix_now();
    let last_block_timestamp =
        block_info_validation.previous_block_info.as_ref().map_or(0, |info| info.timestamp);
    if init_proposed.timestamp < last_block_timestamp {
        return Err(ValidateProposalError::InvalidBlockInfo(
            init_proposed.clone(),
            block_info_validation.clone(),
            format!(
                "Timestamp is too old: last_block_timestamp={}, proposed={}",
                last_block_timestamp, init_proposed.timestamp
            ),
        ));
    }
    if init_proposed.timestamp > now + block_info_validation.block_timestamp_window_seconds {
        return Err(ValidateProposalError::InvalidBlockInfo(
            init_proposed.clone(),
            block_info_validation.clone(),
            format!(
                "Timestamp is in the future: now={}, block_timestamp_window_seconds={}, \
                 proposed={}",
                now, block_info_validation.block_timestamp_window_seconds, init_proposed.timestamp
            ),
        ));
    }
    if !(init_proposed.height == block_info_validation.height
        && init_proposed.l1_da_mode == block_info_validation.l1_da_mode
        && init_proposed.l2_gas_price_fri == block_info_validation.l2_gas_price_fri)
    {
        return Err(ValidateProposalError::InvalidBlockInfo(
            init_proposed.clone(),
            block_info_validation.clone(),
            "Block info validation failed".to_string(),
        ));
    }
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

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L135-150)
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
```

**File:** crates/apollo_batcher/src/block_builder.rs (L155-183)
```rust
    ) -> Self {
        let l1_da_mode = L1DataAvailabilityMode::from_use_kzg_da(block_info.use_kzg_da);
        let transactions_data =
            prepare_txs_hashing_data(&execution_data.execution_infos_and_signatures);
        // TODO(Ayelet): Remove the clones.
        let (header_commitments, measurements) = calculate_block_commitments(
            &transactions_data,
            ThinStateDiff::from(commitment_state_diff.clone()),
            l1_da_mode,
            &block_info.starknet_version,
        )
        .await;
        record_and_log_block_commitment_measurements(block_info.block_number, measurements);
        let partial_block_hash_components =
            PartialBlockHashComponents::new(&block_info, header_commitments);
        let l2_gas_used = execution_data.l2_gas_used();
        Self {
            execution_data,
            commitment_state_diff,
            compressed_state_diff,
            bouncer_weights,
            l2_gas_used,
            casm_hash_computation_data_sierra_gas,
            casm_hash_computation_data_proving_gas,
            compiled_class_hashes_for_migration,
            final_n_executed_txs,
            partial_block_hash_components,
        }
    }
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L209-235)
```rust
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
/// All information required to calculate a block hash except for the state root and the parent
/// block hash.
pub struct PartialBlockHashComponents {
    pub header_commitments: BlockHeaderCommitments,
    pub block_number: BlockNumber,
    pub l1_gas_price: GasPricePerToken,
    pub l1_data_gas_price: GasPricePerToken,
    pub l2_gas_price: GasPricePerToken,
    pub sequencer: SequencerContractAddress,
    pub timestamp: BlockTimestamp,
    pub starknet_version: StarknetVersion,
}

impl PartialBlockHashComponents {
    pub fn new(block_info: &BlockInfo, header_commitments: BlockHeaderCommitments) -> Self {
        Self {
            header_commitments,
            block_number: block_info.block_number,
            l1_gas_price: block_info.gas_prices.l1_gas_price_per_token(),
            l1_data_gas_price: block_info.gas_prices.l1_data_gas_price_per_token(),
            l2_gas_price: block_info.gas_prices.l2_gas_price_per_token(),
            sequencer: SequencerContractAddress(block_info.sequencer_address),
            timestamp: block_info.block_timestamp,
            starknet_version: block_info.starknet_version,
        }
    }
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L245-282)
```rust
pub fn calculate_block_hash(
    partial_block_hash_components: &PartialBlockHashComponents,
    state_root: GlobalRoot,
    previous_block_hash: BlockHash,
) -> StarknetApiResult<BlockHash> {
    let block_hash_version: BlockHashVersion =
        partial_block_hash_components.starknet_version.try_into()?;
    let block_commitments = &partial_block_hash_components.header_commitments;
    Ok(BlockHash(
        HashChain::new()
            .chain(&block_hash_version.clone().into())
            .chain(&partial_block_hash_components.block_number.0.into())
            .chain(&state_root.0)
            .chain(&partial_block_hash_components.sequencer.0)
            .chain(&partial_block_hash_components.timestamp.0.into())
            .chain(&block_commitments.concatenated_counts)
            .chain(&block_commitments.state_diff_commitment.0.0)
            .chain(&block_commitments.transaction_commitment.0)
            .chain(&block_commitments.event_commitment.0)
            .chain(&block_commitments.receipt_commitment.0)
            .chain_iter(
                gas_prices_to_hash(
                    &partial_block_hash_components.l1_gas_price,
                    &partial_block_hash_components.l1_data_gas_price,
                    &partial_block_hash_components.l2_gas_price,
                    &block_hash_version,
                )
                .iter(),
            )
            .chain(
                &Felt::try_from(&partial_block_hash_components.starknet_version)
                    .expect("Expect ASCII version"),
            )
            .chain(&Felt::ZERO)
            .chain(&previous_block_hash.0)
            .get_poseidon_hash(),
    ))
}
```

**File:** crates/apollo_l1_gas_price/src/l1_gas_price_provider.rs (L105-185)
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

        // This index is for the last block in the mean (inclusive).
        let index_last_timestamp_rev = samples.iter().rev().position(|data| {
            data.timestamp <= timestamp.saturating_sub(&self.config.lag_margin_seconds.as_secs())
        });

        // Could not find a block with the requested timestamp and lag.
        let Some(last_index_rev) = index_last_timestamp_rev else {
            return Err(L1GasPriceProviderError::MissingDataError {
                timestamp: timestamp.0,
                lag: self.config.lag_margin_seconds.as_secs(),
            });
        };
        // Convert the index to the forward direction.
        // `last_index` should be one past the final entry we will include in our calculation.
        // The index returned from `position` is guaranteed to be less than `len()`,
        // so `last_index` is guaranteed to be >= 1.
        let last_index = samples.len() - last_index_rev;

        let num_blocks = usize::try_from(self.config.number_of_blocks_for_mean)
            .expect("number_of_blocks_for_mean is too large to fit into a usize");

        let first_index = if last_index >= num_blocks {
            last_index - num_blocks
        } else {
            warn!(
                "Not enough history to calculate the mean gas price. Using blocks {}-{}, \
                 inclusive.",
                samples[0].block_number,
                samples[last_index - 1].block_number,
            );
            L1_GAS_PRICE_PROVIDER_INSUFFICIENT_HISTORY.increment(1);
            0
        };
        debug_assert!(first_index < last_index, "error calculating indices");
        let actual_number_of_blocks = last_index - first_index;

        // Go over all elements between `first_index` and `last_index` (non-inclusive).
        let price_info_summed: PriceInfo = samples
            .iter()
            .skip(first_index)
            .take(actual_number_of_blocks)
            .map(|data| &data.price_info)
            .sum();
        let actual_number_of_blocks =
            u128::try_from(actual_number_of_blocks).expect("Cannot convert to u128");
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
    }
```

**File:** crates/apollo_node/resources/config_schema.json (L2947-2966)
```json
  "l1_gas_price_provider_config.lag_margin_seconds": {
    "description": "Difference between the time of the block from L1 used to calculate the gas price and the time of the L2 block this price is used in",
    "privacy": "Public",
    "value": 60
  },
  "l1_gas_price_provider_config.max_time_gap_seconds": {
    "description": "Maximum valid time gap between the requested timestamp and the last price sample in seconds",
    "privacy": "Public",
    "value": 900
  },
  "l1_gas_price_provider_config.number_of_blocks_for_mean": {
    "description": "Number of blocks to use for the mean gas price calculation",
    "privacy": "Public",
    "value": 300
  },
  "l1_gas_price_provider_config.storage_limit": {
    "description": "Maximum number of L1 blocks to keep cached",
    "privacy": "Public",
    "value": 3000
  },
```
