### Title
Unvalidated `l1_gas_price_wei`/`l1_data_gas_price_wei` in `ProposalInit` Corrupts Block Hash Commitment and ETH Fee Accounting — (`crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

---

### Summary

During proposal validation, `is_block_info_valid` checks only the FRI-denominated L1 gas prices (`l1_gas_price_fri`, `l1_data_gas_price_fri`) against the local oracle. The WEI-denominated counterparts (`l1_gas_price_wei`, `l1_data_gas_price_wei`) in `ProposalInit` are accepted verbatim from the proposer without any cross-check. Both wei and fri prices are hashed into the block commitment via `gas_prices_to_hash`, and the wei prices additionally drive the `eth_to_fri_rate` used to derive `l2_gas_price_wei`. A malicious proposer can therefore inject arbitrary (but internally consistent) wei prices that pass every validation gate, causing every validator to commit a wrong `PartialBlockHash`, wrong ETH-denominated fees for L1 transactions, and a corrupted `PreviousBlockInfo` that poisons the fallback gas-price path for subsequent blocks.

---

### Finding Description

`ProposalInit` carries two parallel price representations for L1 gas:

```
l1_gas_price_fri  / l1_data_gas_price_fri   ← validated
l1_gas_price_wei  / l1_data_gas_price_wei   ← NOT validated
``` [1](#0-0) 

`is_block_info_valid` calls `get_l1_prices_in_fri_and_wei` and compares only the FRI result against the proposed FRI prices within a percentage margin. The wei prices are never compared against anything: [2](#0-1) 

After validation passes, `initiate_validation` calls `convert_to_sn_api_block_info(init)`, which consumes the proposer-supplied wei prices directly: [3](#0-2) 

Inside `convert_to_sn_api_block_info`, the `eth_to_fri_rate` is derived from the proposer-supplied wei/fri ratio: [4](#0-3) 

This rate is then used to compute `l2_gas_price_wei = l2_gas_price_fri / eth_to_fri_rate`, which is also proposer-controlled.

All three wei prices (`l1_gas_price_wei`, `l1_data_gas_price_wei`, `l2_gas_price_wei`) are then fed into `gas_prices_to_hash` and hashed into the block commitment: [5](#0-4) 

`calculate_block_hash` chains this hash into the final `PartialBlockHash` / `ProposalCommitment`: [6](#0-5) 

The only runtime guards on wei prices are:
- `NonzeroGasPrice::new` — rejects zero
- `calculate_eth_to_fri_rate` — rejects a derived rate of zero

These leave a wide range of manipulable values. For example, with `l1_gas_price_fri = 10^12` (within the allowed margin) and `l1_gas_price_wei = 2 × expected_wei`, the derived rate halves, `l2_gas_price_wei` halves, and all three corrupted wei values are committed into the block hash — while every validator's `is_block_info_valid` returns `Ok(())`.

The corrupted `ProposalInit` is also stored as `PreviousBlockInfo` for the next height: [7](#0-6) 

When the L1 gas price provider is unavailable, the fallback path reuses these corrupted wei prices and recomputes `eth_to_fri_rate` from them, propagating the error to the next block's gas prices: [8](#0-7) 

---

### Impact Explanation

**Wrong block hash commitment.** The `PartialBlockHash` (= `ProposalCommitment`) agreed upon by consensus includes the manipulated wei prices. Once the state root is appended, the canonical block hash stored in storage and used as `parent_hash` for the next block is permanently wrong. This is a wrong block commitment accepted through the normal consensus path.

**Wrong ETH-denominated fee accounting.** L1 transactions pay fees in ETH (wei). With a manipulated `l1_gas_price_wei`, every L1 transaction in the block is charged the wrong ETH fee — an economic impact on every user whose transaction is included.

**Wrong `l2_gas_price_wei`.** Because `l2_gas_price_wei` is derived from the manipulated `eth_to_fri_rate`, the ETH-denominated L2 gas price is also wrong, affecting fee estimation and actual fee deduction for L2 transactions.

**Cascading fallback corruption.** The corrupted wei prices stored in `PreviousBlockInfo` poison the fallback gas-price path for the next block whenever the L1 oracle is unavailable.

---

### Likelihood Explanation

Any validator that wins a proposal slot can exploit this. No special privilege beyond being a legitimate proposer is required. The attacker only needs to craft a `ProposalInit` with wei prices that satisfy the two runtime guards (non-zero, non-zero derived rate) while differing from the honest values. This is trivially achievable: setting `l1_gas_price_wei` to any value in the range `[1, l1_gas_price_fri * WEI_PER_ETH / 1]` that keeps `eth_to_fri_rate > 0` and `l2_gas_price_wei > 0` will pass all checks.

---

### Recommendation

In `is_block_info_valid`, after computing the expected FRI prices, also compute the expected wei prices from the same oracle call and validate the proposed wei prices against them within the same percentage margin used for FRI prices. Concretely, the `_l1_gas_prices_wei` value already returned by `get_l1_prices_in_fri_and_wei` (currently discarded with `_`) should be compared against `init_proposed.l1_gas_price_wei` and `init_proposed.l1_data_gas_price_wei`: [9](#0-8) 

The `_l1_gas_prices_wei` binding should be renamed and used to validate `init_proposed.l1_gas_price_wei` and `init_proposed.l1_data_gas_price_wei` with `within_margin`, mirroring the existing FRI-price check at lines 302–319.

---

### Proof of Concept

1. A malicious proposer wins a proposal slot at height H.
2. It constructs `ProposalInit` with:
   - `l1_gas_price_fri = X` (within the allowed margin of the oracle value)
   - `l1_gas_price_wei = 2 × honest_wei` (double the honest value)
   - `l1_data_gas_price_fri = Y` (within margin)
   - `l1_data_gas_price_wei = 2 × honest_data_wei`
3. `is_block_info_valid` checks only FRI prices → passes.
4. `convert_to_sn_api_block_info` computes `eth_to_fri_rate = X * WEI_PER_ETH / (2 × honest_wei)` — half the honest rate — and `l2_gas_price_wei = l2_gas_price_fri / (halved rate)` — double the honest value. Both are non-zero → no error.
5. The batcher executes the block with these prices and computes `PartialBlockHash` including `l1_gas_price_wei = 2 × honest_wei`, `l1_data_gas_price_wei = 2 × honest_data_wei`, `l2_gas_price_wei = 2 × honest_l2_wei`.
6. All validators run the same `convert_to_sn_api_block_info` on the received `ProposalInit` and reach the same (wrong) `PartialBlockHash` → consensus agrees on a wrong block commitment.
7. L1 transactions in block H are charged double the correct ETH fee.
8. The corrupted wei prices are stored as `PreviousBlockInfo` for height H+1. [10](#0-9) [11](#0-10) [5](#0-4)

### Citations

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

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L99-113)
```rust
impl From<&ProposalInit> for PreviousBlockInfo {
    fn from(init: &ProposalInit) -> Self {
        Self {
            timestamp: init.timestamp,
            l1_prices_wei: L1PricesInWei {
                l1_gas_price: init.l1_gas_price_wei,
                l1_data_gas_price: init.l1_data_gas_price_wei,
            },
            l1_prices_fri: L1PricesInFri {
                l1_gas_price: init.l1_gas_price_fri,
                l1_data_gas_price: init.l1_data_gas_price_fri,
            },
        }
    }
}
```

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

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L489-515)
```rust
fn calculate_eth_to_fri_rate(block_info: &PreviousBlockInfo) -> Result<u128, StarknetApiError> {
    let eth_to_fri_rate = block_info
        .l1_prices_fri
        .l1_gas_price
        .0
        .checked_mul(WEI_PER_ETH)
        .ok_or_else(|| {
            StarknetApiError::GasPriceConversionError(format!(
                "Gas price in Fri should be small enough to multiply by WEI_PER_ETH. Previous \
                 block info: {:?}",
                block_info
            ))
        })?
        .checked_div(block_info.l1_prices_wei.l1_gas_price.0)
        .ok_or_else(|| {
            StarknetApiError::GasPriceConversionError(format!(
                "Gas price in Wei should be non-zero. Previous block info: {:?}",
                block_info
            ))
        })?;
    if eth_to_fri_rate == 0 {
        return Err(StarknetApiError::GasPriceConversionError(format!(
            "Eth to fri rate is zero. Previous block info: {:?}",
            block_info
        )));
    }
    Ok(eth_to_fri_rate)
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
