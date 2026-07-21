### Title
Unbounded Stale ETH→STRK Rate Fallback Silently Encodes Wrong Gas Prices into Block Commitments — (`crates/apollo_consensus_orchestrator/src/utils.rs`)

---

### Summary

When the ETH→STRK oracle or L1 gas price scraper fails, `get_l1_prices_in_fri_and_wei_and_conversion_rate` falls back to the previous committed block's prices with **no limit on how many consecutive blocks may use this stale data**. Because the validator calls the identical fallback path with the same `previous_block_info`, both proposer and validator converge on the same stale value, the 10 % `within_margin` check trivially passes, and blocks carrying wrong `l1_gas_price_fri` / `l1_data_gas_price_fri` are committed. Those prices are then hashed into the block commitment via `gas_prices_to_hash`, producing a permanently wrong block hash and incorrect fee accounting for every transaction in the affected blocks.

---

### Finding Description

**Root cause — `utils.rs` fallback path**

`get_l1_prices_in_fri_and_wei_and_conversion_rate` has three ordered paths:

1. Live oracle + scraper → correct prices.
2. Either fails → **reuse `previous_block_info.l1_prices_fri` / `l1_prices_wei` verbatim**, with no counter, no age check, no cap on consecutive reuse.
3. No previous block → hardcoded `DEFAULT_ETH_TO_FRI_RATE` (10²¹) + `min_l1_gas_price_wei`. [1](#0-0) 

`previous_block_info` is updated only when `decision_reached` commits a block. If the oracle is down for N consecutive rounds, all N proposals are built with the prices frozen at the last successful block.

**Validator accepts the stale price**

`is_block_info_valid` in `validate_proposal.rs` calls the same `get_l1_prices_in_fri_and_wei` with the same `previous_block_info`: [2](#0-1) 

When the oracle is also down for the validator, it computes the identical stale value. `within_margin(stale, stale, 10)` evaluates to `abs_diff = 0 ≤ margin`, so the check always passes: [3](#0-2) 

The 10 % margin (`l1_gas_price_margin_percent`) is defined in versioned constants: [4](#0-3) 

**Wrong prices enter the block hash**

`PartialBlockHashComponents` captures `l1_gas_price`, `l1_data_gas_price`, and `l2_gas_price` directly from `BlockInfo`: [5](#0-4) 

`calculate_block_hash` chains these through `gas_prices_to_hash` into the Poseidon hash: [6](#0-5) 

The Cairo OS block-hash function also consumes `gas_prices_hash` as an explicit input: [7](#0-6) 

**Natural trigger — `StaleL1GasPricesError`**

The L1 gas price provider already detects staleness and returns an error when the scraper falls behind by more than `max_time_gap_seconds` (default 900 s): [8](#0-7) 

This error is the exact condition that activates the fallback. No attacker action is required; a routine scraper lag or ETH→STRK API outage is sufficient.

**ETH→STRK oracle has no staleness guard in the fallback**

The oracle caches results by quantized timestamp but has no mechanism to signal "my cached rate is N hours old": [9](#0-8) 

When the API is unreachable, every call returns `QueryNotReadyError` or a request error, driving the orchestrator into the `previous_block_info` path indefinitely.

---

### Impact Explanation

| Corrupted field | Where it appears | Downstream effect |
|---|---|---|
| `l1_gas_price_fri` (stale) | `BlockHeaderWithoutHash`, `PartialBlockHashComponents` | Wrong STRK fee charged to every transaction in the block |
| `l1_data_gas_price_fri` (stale) | Same | Wrong blob/DA fee charged |
| `gas_prices_hash` | `calculate_block_hash` Poseidon chain | Block hash encodes wrong prices; proof inputs derived from this hash are wrong |

If ETH appreciates while the oracle is down, users pay too little in STRK for L1 gas (protocol loss). If ETH depreciates, users overpay. Both outcomes are encoded permanently in the committed block hash and in the SNOS `gas_prices_hash` input, satisfying the "Incorrect fee, gas, resource accounting with economic impact" (Critical) and "Wrong state/receipt/revert result from execution logic" (Critical) impact categories.

---

### Likelihood Explanation

The `max_time_gap_seconds` default is 900 s. Any scraper restart, L1 RPC hiccup, or ETH→STRK API outage lasting more than 15 minutes activates the fallback. The deployment config confirms these are production-grade timeouts: [10](#0-9) 

The existing test `oracle_fails_on_second_block` explicitly demonstrates that the system continues building and committing blocks when the oracle fails, using previous block prices: [11](#0-10) 

---

### Recommendation

1. **Add a consecutive-fallback counter.** Track how many blocks in a row have used the stale fallback. After a configurable threshold (e.g., 3 blocks), refuse to build or validate proposals until the oracle recovers.
2. **Timestamp-gate the fallback.** Reject `previous_block_info` prices if `current_timestamp - previous_block_info.timestamp > max_price_staleness_seconds`.
3. **Separate oracle staleness from oracle absence.** The ETH→STRK oracle should expose a "last successful fetch" timestamp so the orchestrator can decide whether the cached rate is too old to trust.

---

### Proof of Concept

```
1. L1 gas price scraper falls behind by > 900 s → get_price_info returns StaleL1GasPricesError.
2. ETH→STRK oracle API is unreachable → get_eth_to_fri_rate returns QueryNotReadyError.
3. get_l1_prices_in_fri_and_wei_and_conversion_rate (utils.rs:185-198) returns
   previous_block_info.l1_prices_fri (e.g., frozen at ETH = $2000, rate = R₀).
4. ETH market price moves to $3000 (rate should be R₁ = 1.5 × R₀).
5. Proposer builds ProposalInit with l1_gas_price_fri = P_stale (computed from R₀).
6. Validator calls is_block_info_valid → same fallback → same P_stale.
7. within_margin(P_stale, P_stale, 10) = true → proposal accepted.
8. decision_reached commits the block; PartialBlockHashComponents.l1_gas_price encodes P_stale.
9. calculate_block_hash chains gas_prices_to_hash(P_stale, ...) → wrong block hash H_wrong.
10. Every transaction in the block is charged fees based on P_stale, not P_correct.
    Users underpay by ~33 % in STRK for L1 gas costs.
11. This repeats for every subsequent block until the oracle recovers, with no automatic halt.
```

### Citations

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L183-208)
```rust
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

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L323-333)
```rust
fn within_margin(number1: GasPrice, number2: GasPrice, margin_percent: u128) -> bool {
    // For small numbers (e.g., less than 10 wei, if margin is 10%), even an off-by-one
    // error might be bigger than the margin, even if it is just a rounding error.
    // We make an exception for such mismatch, and don't bother checking percentages
    // if the difference in price is only one wei.
    if number1.0.abs_diff(number2.0) <= GAS_PRICE_ABS_DIFF_MARGIN {
        return true;
    }
    let margin = (number1.0 * margin_percent) / 100;
    number1.0.abs_diff(number2.0) <= margin
}
```

**File:** crates/apollo_consensus_orchestrator/resources/orchestrator_versioned_constants_0_14_2.json (L1-7)
```json
{
    "gas_price_max_change_denominator": 48,
    "gas_target": 1500000000,
    "max_block_size": 5800000000,
    "min_gas_price": "0x1dcd65000",
    "l1_gas_price_margin_percent": 10
}
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L212-235)
```rust
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/block_hash.cairo (L31-50)
```text
    with hash_state {
        hash_update_single(BLOCK_HASH_VERSION);
        hash_update_single(block_info.block_number);
        hash_update_single(state_root);
        hash_update_single(block_info.sequencer_address);
        hash_update_single(block_info.block_timestamp);
        hash_update_single(header_commitments.packed_lengths);
        hash_update_single(header_commitments.state_diff_commitment);
        hash_update_single(header_commitments.transaction_commitment);
        hash_update_single(header_commitments.event_commitment);
        hash_update_single(header_commitments.receipt_commitment);
        hash_update_single(gas_prices_hash);
        hash_update_single(starknet_version);
        hash_update_single(0);
        hash_update_single(previous_block_hash);
    }

    let block_hash = hash_finalize(hash_state=hash_state);
    return block_hash;
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

**File:** crates/apollo_l1_gas_price/src/eth_to_strk_oracle.rs (L196-253)
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
        let result = handle.now_or_never().expect("Handle must be finished if we got here");
        let rate = match result {
            Ok(Ok(rate)) => rate,
            Ok(Err(e)) => {
                warn!("Query returned an error for timestamp {timestamp}: {e:?}");
                // Must remove failed query from the cache, to avoid re-polling it.
                queries.pop(&quantized_timestamp);
                return Err(e);
            }
            Err(e) => {
                warn!("Query failed to join handle for timestamp {timestamp}: {e:?}");
                ETH_TO_STRK_ERROR_COUNT.increment(1);
                // Must remove failed query from the cache, to avoid re-polling it.
                queries.pop(&quantized_timestamp);
                return Err(EthToStrkOracleClientError::JoinError(e.to_string()));
            }
        };

        // Make sure to cache the result.
        cache.put(quantized_timestamp, rate);
        // We don't need to come back to this query since we have the result in cache.
        queries.pop(&quantized_timestamp);
        debug!("Caching conversion rate for timestamp {timestamp}, with rate {rate}");
        Ok(rate)
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

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context_test.rs (L908-1005)
```rust
#[rstest]
#[case::l1_price_oracle_failure(true)]
#[case::eth_to_strk_rate_oracle_failure(false)]
#[tokio::test]
async fn oracle_fails_on_second_block(#[case] l1_oracle_failure: bool) {
    let (mut deps, mut network) = create_test_and_network_deps();
    // Validate block number 0, call decision_reached to save the previous block info (block 0), and
    // attempt to build_proposal on block number 1.
    deps.setup_deps_for_validate(SetupDepsArgs::default());
    deps.setup_deps_for_build(SetupDepsArgs { start_block_number: HEIGHT_1, ..Default::default() });

    // set up batcher decision_reached
    deps.batcher.expect_decision_reached().times(1).return_once(|_| {
        Ok(DecisionReachedResponse {
            state_diff: ThinStateDiff::default(),
            central_objects: CentralObjects::default(),
        })
    });

    // required for decision reached flow
    deps.state_sync_client.expect_add_new_block().times(1).return_once(|_| Ok(()));
    deps.cende_ambassador.expect_prepare_blob_for_next_height().times(1).return_once(|_| Ok(()));

    // set the oracle to succeed on first block and fail on second
    if l1_oracle_failure {
        let mut l1_prices_oracle_client = MockL1GasPriceProviderClient::new();
        l1_prices_oracle_client.expect_get_eth_to_fri_rate().returning(|_| Ok(ETH_TO_FRI_RATE));
        l1_prices_oracle_client.expect_get_price_info().times(1).return_const(Ok(PriceInfo {
            base_fee_per_gas: GasPrice(TEMP_ETH_GAS_FEE_IN_WEI),
            blob_fee: GasPrice(TEMP_ETH_BLOB_GAS_FEE_IN_WEI),
        }));
        l1_prices_oracle_client.expect_get_price_info().times(1).return_const(Err(
            L1GasPriceClientError::L1GasPriceProviderError(
                // random error, these parameters don't mean anything
                L1GasPriceProviderError::UnexpectedBlockNumberError { expected: 0, found: 1 },
            ),
        ));
        deps.l1_gas_price_provider = l1_prices_oracle_client;
    } else {
        let mut l1_prices_oracle_client = MockL1GasPriceProviderClient::new();
        // Make sure the L1 gas price always returns with good values.
        l1_prices_oracle_client.expect_get_price_info().returning(|_| {
            Ok(PriceInfo {
                base_fee_per_gas: GasPrice(TEMP_ETH_GAS_FEE_IN_WEI),
                blob_fee: GasPrice(TEMP_ETH_BLOB_GAS_FEE_IN_WEI),
            })
        });
        // Set the eth_to_fri_rate to succeed on first block and fail on second.
        l1_prices_oracle_client
            .expect_get_eth_to_fri_rate()
            .times(1)
            .return_once(|_| Ok(ETH_TO_FRI_RATE));
        // Set the eth_to_fri_rate to fail on second block.
        l1_prices_oracle_client.expect_get_eth_to_fri_rate().times(1).return_once(|_| {
            Err(L1GasPriceClientError::EthToStrkOracleClientError(
                EthToStrkOracleClientError::MissingFieldError("".to_string(), "".to_string()),
            ))
        });
        deps.l1_gas_price_provider = l1_prices_oracle_client;
    }

    let mut context = deps.build_context();

    // Validate block number 0.

    // Initialize the context for a specific height, starting with round 0.
    context.set_height_and_round(HEIGHT_0, ROUND_0).await.unwrap();

    let content_receiver = send_proposal_to_validator_context(&mut context).await;
    let fin_receiver = context
        .validate_proposal(proposal_init(HEIGHT_0, ROUND_0), TIMEOUT, content_receiver)
        .await;
    let proposal_commitment = fin_receiver.await.unwrap();
    assert_eq!(proposal_commitment, TEST_PROPOSAL_COMMITMENT);

    // Decision reached

    context.decision_reached(HEIGHT_0, ROUND_0, proposal_commitment, false).await.unwrap();

    // Build proposal for block number 1.
    let build_param = BuildParam { height: HEIGHT_1, ..Default::default() };

    let fin_receiver = context.build_proposal(build_param, TIMEOUT).await.unwrap();

    let (_, mut receiver) = network.outbound_proposal_receiver.next().await.unwrap();

    let part = receiver.next().await.unwrap();
    let ProposalPart::Init(info) = part else {
        panic!("Expected ProposalPart::Init");
    };
    assert_eq!(info.height, HEIGHT_1);

    let previous_init = proposal_init(HEIGHT_0, ROUND_0);

    assert_eq!(info.l1_gas_price_wei, previous_init.l1_gas_price_wei);
    assert_eq!(info.l1_data_gas_price_wei, previous_init.l1_data_gas_price_wei);
    assert_eq!(info.l1_gas_price_fri, previous_init.l1_gas_price_fri);
    assert_eq!(info.l1_data_gas_price_fri, previous_init.l1_data_gas_price_fri);
```
