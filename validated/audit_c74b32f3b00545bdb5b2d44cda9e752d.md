### Title
Proposed Block Timestamp Has No Lower-Bound Against Current Time, Allowing a Malicious Proposer to Embed Stale L1 Gas Prices in the Block Hash Commitment — (`crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

### Summary

`is_block_info_valid` in `validate_proposal.rs` enforces only a one-sided time window: the proposed timestamp must not exceed `now + block_timestamp_window_seconds` (1 second in production), but there is no symmetric lower-bound check against `now`. The only lower bound is `last_block_timestamp` (the previous block's timestamp), which can be arbitrarily far in the past. Because the L1 gas price lookup inside the same function is keyed on `init_proposed.timestamp`, a malicious proposer can pick any past timestamp within the provider's sliding window, supply matching historical L1 gas prices, pass all validation checks, and commit a block whose hash and fee parameters reflect a stale point in time.

### Finding Description

`is_block_info_valid` reads the validator's local clock once and applies two timestamp checks:

```
last_block_timestamp ≤ init_proposed.timestamp ≤ now + block_timestamp_window_seconds
``` [1](#0-0) 

The upper bound (`now + 1 s` in production) prevents a far-future timestamp. The lower bound is only the previous block's timestamp, which can be many seconds or minutes in the past during slow rounds or after network partitions.

Immediately after the timestamp range check, the validator computes the expected L1 gas prices by passing `init_proposed.timestamp` directly to the L1 gas price provider:

```rust
let (l1_gas_prices_fri, _l1_gas_prices_wei) = get_l1_prices_in_fri_and_wei(
    l1_gas_price_provider,
    init_proposed.timestamp,   // ← uses the attacker-controlled past timestamp
    ...
).await;
``` [2](#0-1) 

`L1GasPriceProvider::get_price_info` performs a reverse scan over its sample window and returns the mean gas price for the L1 blocks whose timestamps fall at or before `timestamp - lag_margin`: [3](#0-2) 

Because the validator derives its *expected* gas price from the same past timestamp the proposer chose, the `within_margin` check will pass as long as the proposer's declared `l1_gas_price_fri` matches the historical price at that past time: [4](#0-3) 

The accepted timestamp and gas prices are then baked into `PartialBlockHashComponents`, which feeds `calculate_block_hash`: [5](#0-4) 

The block hash chains `block_timestamp` as a first-class field: [6](#0-5) 

The resulting `PartialBlockHashComponents` (including the stale timestamp and historical gas prices) is stored and later used to finalize the block hash: [7](#0-6) 

### Impact Explanation

A malicious or compromised proposer can:

1. Set `init_proposed.timestamp = last_block_timestamp` (or any value within the L1 provider's sample window that is in the past).
2. Query the historical L1 gas price for that past timestamp and declare matching `l1_gas_price_fri` / `l1_data_gas_price_fri` values.
3. Pass all validation checks in `is_block_info_valid` because the validator recomputes expected prices using the same past timestamp.
4. Commit a block whose `block_hash` encodes a stale timestamp and whose L1 gas prices reflect a historical (potentially lower or higher) fee market.

All transactions in the block are charged fees based on the embedded L1 gas prices. If the proposer selects a past timestamp where L1 gas was cheaper, users underpay; if more expensive, users overpay. The committed block hash is also wrong relative to the actual wall-clock time, breaking the timestamp monotonicity invariant that downstream provers and verifiers rely on.

This matches the allowed impact: **Critical — Incorrect fee, gas, bouncer, resource accounting, refund, balance, or L1 gas price effect with economic impact.**

### Likelihood Explanation

The trigger requires a malicious or compromised BFT proposer — a privileged but realistic threat (the external report's own mitigation notes the same residual risk for a "malicious/compromised keeper"). The `block_timestamp_window_seconds` is 1 second in production: [8](#0-7) 

This tight future window makes the asymmetry especially pronounced: the proposer is constrained to within 1 second in the future but is unconstrained in the past (bounded only by the previous block's timestamp, which can be 10+ seconds old under normal block times).

### Recommendation

Add a symmetric lower-bound check in `is_block_info_valid`:

```rust
if now > block_info_validation.block_timestamp_window_seconds
    && init_proposed.timestamp < now - block_info_validation.block_timestamp_window_seconds
{
    return Err(ValidateProposalError::InvalidBlockInfo(...));
}
```

This mirrors the existing upper-bound check and ensures the proposed timestamp is within `±block_timestamp_window_seconds` of the validator's local clock, preventing a proposer from selecting a past timestamp to manipulate historical L1 gas price lookups.

### Proof of Concept

1. Validator A is the current proposer for height H.
2. The previous block (H-1) was committed with `timestamp = T_prev` (e.g., 30 seconds ago due to a slow round).
3. Validator A queries `L1GasPriceProvider::get_price_info(BlockTimestamp(T_prev))` and obtains `price_old` (lower than the current price).
4. Validator A constructs `ProposalInit { timestamp: T_prev, l1_gas_price_fri: price_old, ... }`.
5. All other validators call `is_block_info_valid`:
   - `T_prev >= T_prev` ✓ (lower bound passes)
   - `T_prev <= now + 1` ✓ (upper bound passes, since `T_prev < now`)
   - `get_l1_prices_in_fri_and_wei(T_prev)` returns `price_old`; `within_margin(price_old, price_old, ...)` ✓
6. The proposal is accepted. The committed block hash encodes `T_prev` and `price_old`.
7. All transactions in block H are charged fees based on the stale, lower L1 gas price, causing incorrect fee accounting with direct economic impact. [9](#0-8) [10](#0-9) [11](#0-10)

### Citations

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L244-321)
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
    Ok(())
}
```

**File:** crates/apollo_l1_gas_price/src/l1_gas_price_provider.rs (L126-137)
```rust
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
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L223-235)
```rust
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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L245-281)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/block_hash.cairo (L36-36)
```text
        hash_update_single(block_info.block_timestamp);
```

**File:** crates/apollo_batcher/src/commitment_manager/commitment_manager_impl.rs (L503-525)
```rust
                let (previous_block_hash, partial_block_hash_components) =
                    storage_reader.get_parent_hash_and_partial_block_hash_components(height)?;
                let previous_block_hash = previous_block_hash.ok_or_else(|| {
                    CommitmentManagerError::MissingBlockHash(height.prev().expect(
                        "For the genesis block, the block hash is constant and should not be \
                         fetched from storage.",
                    ))
                })?;
                let partial_block_hash_components = partial_block_hash_components
                    .ok_or(CommitmentManagerError::MissingPartialBlockHashComponents(height))?;
                debug!(
                    "Calculating block hash for block {height} with partial block hash \
                     components: {partial_block_hash_components:?}"
                );
                debug!(
                    "Global root: {global_root:?}, previous block hash: {previous_block_hash:?}"
                );
                let block_hash = calculate_block_hash(
                    &partial_block_hash_components,
                    global_root,
                    previous_block_hash,
                )?;
                Ok(FinalBlockCommitment { height, block_hash: Some(block_hash), global_root })
```

**File:** crates/apollo_deployments/resources/app_configs/consensus_manager_config.json (L35-35)
```json
  "consensus_manager_config.context_config.static_config.block_timestamp_window_seconds": 1,
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
