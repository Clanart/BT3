### Title
Unvalidated `l1_gas_price_wei` in `ProposalInit` Allows Proposer to Corrupt `eth_to_fri_rate`, `l2_gas_price_wei`, and Block Hash Commitment - (`File: crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

---

### Summary

`is_block_info_valid` validates the FRI-denominated L1 gas prices (`l1_gas_price_fri`, `l1_data_gas_price_fri`) against the validator's own oracle, but silently accepts the proposer-supplied WEI-denominated prices (`l1_gas_price_wei`, `l1_data_gas_price_wei`) without any independent check. These wei prices are the sequencer's analog to the external report's `unitOfAccount`: they are the reference values used to derive the implicit `eth_to_fri_rate`, which in turn determines `l2_gas_price_wei` and is committed verbatim into the block hash. A malicious proposer can set `l1_gas_price_wei` to any nonzero value, skew the derived conversion rate, corrupt `l2_gas_price_wei`, and cause every validator to commit a block hash that encodes wrong gas prices—affecting fee accounting for ETH-paying transactions and the authoritative block hash stored on-chain.

---

### Finding Description

**Root cause — `is_block_info_valid` discards the validator's own wei prices**

In `validate_proposal.rs`, `is_block_info_valid` calls `get_l1_prices_in_fri_and_wei` to obtain the validator's independent view of both FRI and WEI prices, but immediately discards the WEI result:

```rust
let (l1_gas_prices_fri, _l1_gas_prices_wei) = get_l1_prices_in_fri_and_wei(...)
    .await;
```

Only the FRI prices are then compared against the proposer's values:

```rust
if !(within_margin(l1_gas_price_fri_proposed, l1_gas_price_fri, ...)
    && within_margin(l1_data_gas_price_fri_proposed, l1_data_gas_price_fri, ...))
{
    return Err(ValidateProposalError::InvalidBlockInfo(...));
}
```

`l1_gas_price_wei` and `l1_data_gas_price_wei` from the proposer's `ProposalInit` are never compared to the validator's oracle output. [1](#0-0) 

**Propagation — proposer's wei price drives `eth_to_fri_rate` and `l2_gas_price_wei`**

After `is_block_info_valid` passes, `initiate_validation` calls `convert_to_sn_api_block_info(init)`, which derives the implicit conversion rate entirely from the proposer's own fields:

```rust
let previous_block_info = PreviousBlockInfo::from(init);   // uses init.l1_gas_price_fri / init.l1_gas_price_wei
let eth_to_fri_rate = calculate_eth_to_fri_rate(&previous_block_info)?;
let l2_gas_price_wei = NonzeroGasPrice::new(
    init.l2_gas_price_fri.fri_to_wei(eth_to_fri_rate)?
)?;
```

`calculate_eth_to_fri_rate` computes `l1_gas_price_fri * WEI_PER_ETH / l1_gas_price_wei`. Because `l1_gas_price_wei` is attacker-controlled, the rate—and therefore `l2_gas_price_wei`—is attacker-controlled. [2](#0-1) [3](#0-2) 

**Commitment — all six wei/fri prices enter the block hash**

`gas_prices_to_hash` (Starknet ≥ 0.13.4) hashes all six price fields—including `l1_gas_price.price_in_wei`, `l1_data_gas_price.price_in_wei`, and `l2_gas_price.price_in_wei`—into a single Poseidon digest that is chained into `calculate_block_hash`:

```rust
HashChain::new()
    .chain(&STARKNET_GAS_PRICES0)
    .chain(&l1_gas_price.price_in_wei.0.into())
    .chain(&l1_gas_price.price_in_fri.0.into())
    .chain(&l1_data_gas_price.price_in_wei.0.into())
    .chain(&l1_data_gas_price.price_in_fri.0.into())
    .chain(&l2_gas_price.price_in_wei.0.into())
    .chain(&l2_gas_price.price_in_fri.0.into())
    .get_poseidon_hash()
``` [4](#0-3) 

Because both proposer and validator execute `convert_to_sn_api_block_info` with the same (unvalidated) `l1_gas_price_wei`, they agree on the corrupted `BlockInfo` and produce the same `partial_block_hash`, so `ProposalFinMismatch` is never triggered. The corrupted hash is committed. [5](#0-4) 

**Fee accounting — ETH-paying transactions use the corrupted wei price**

`GasVector::cost` multiplies gas consumed by `gas_prices.l1_gas_price` (the wei price for `FeeType::Eth`). With a manipulated `l1_gas_price_wei`, every V1/V2 transaction in the block is charged the wrong fee. [6](#0-5) 

**Fallback propagation — corrupted wei price poisons the next block's fallback rate**

`previous_block_info_from_block_header` reads `l1_gas_price.price_in_wei` from the committed header and stores it as `PreviousBlockInfo`. When the oracle is unavailable for the next block, `calculate_eth_to_fri_rate` recomputes the rate from this stored value, propagating the corruption. [7](#0-6) [8](#0-7) 

---

### Impact Explanation

A malicious proposer (any BFT validator whose turn it is to propose) can:

1. Set `l1_gas_price_wei` to an arbitrary nonzero value while keeping `l1_gas_price_fri` within the accepted margin.
2. Force all validators to derive a wrong `eth_to_fri_rate` and a wrong `l2_gas_price_wei`.
3. Cause the committed block hash to encode wrong gas prices — a **wrong state/receipt value from blockifier/execution logic** (Critical).
4. Charge wrong fees to all ETH-paying transactions in the block — **incorrect fee/gas/balance effect with economic impact** (Critical).
5. Poison the fallback conversion rate for subsequent blocks when the oracle is unavailable.
6. Cause RPC fee estimation and simulation to return wrong values for the affected block (High).

---

### Likelihood Explanation

Any validator node that wins a proposal round can trigger this without any external dependency. In a BFT network with `n` validators, each validator proposes roughly `1/n` of all blocks. The attack requires no special privilege beyond being the current proposer, and no off-chain coordination. The `within_margin` check on FRI prices does not constrain the WEI prices at all.

---

### Recommendation

1. **Validate wei prices independently.** In `is_block_info_valid`, compare `init_proposed.l1_gas_price_wei` and `init_proposed.l1_data_gas_price_wei` against the validator's own `_l1_gas_prices_wei` (currently discarded) using the same `within_margin` logic applied to FRI prices.

2. **Do not derive `eth_to_fri_rate` from proposer-supplied fields.** `convert_to_sn_api_block_info` should either receive the independently validated wei prices or compute `l2_gas_price_wei` using the validator's own oracle-derived rate rather than back-computing it from the proposer's `l1_gas_price_fri / l1_gas_price_wei` ratio.

3. **Treat wei and fri prices symmetrically.** The comment in `ProposalInit` ("keeping the wei prices for now, to use with L1 transactions") acknowledges their importance; they must receive the same validation rigor as FRI prices.

---

### Proof of Concept

1. Honest validator oracle returns: `l1_gas_price_wei = 10 gwei`, `l1_gas_price_fri = 8000 fri` (rate ≈ 800 STRK/ETH).
2. Malicious proposer sends `ProposalInit` with `l1_gas_price_fri = 8000 fri` (passes `within_margin`) but `l1_gas_price_wei = 1 wei` (not checked).
3. `calculate_eth_to_fri_rate` computes `8000 * 10^18 / 1 = 8 * 10^21` (rate inflated by 10^9).
4. `l2_gas_price_wei = l2_gas_price_fri.fri_to_wei(8e21)` → `l2_gas_price_fri * 10^18 / 8e21` → rounds to 0 → `NonzeroGasPrice::new` returns `Err`, causing `convert_to_sn_api_block_info` to fail and the proposal to be rejected.
5. Alternatively, set `l1_gas_price_wei = 10^15` (1000x inflated): rate becomes `8000 * 10^18 / 10^15 = 8 * 10^6`. `l2_gas_price_wei` is computed as `l2_gas_price_fri * 10^18 / 8e6` — a value 125,000× larger than the honest value. This passes all checks, is committed to the block hash, and is used to charge ETH-paying transactions.
6. `gas_prices_to_hash` hashes the inflated `l2_gas_price_wei` into the block hash. Both proposer and validator agree on this hash (both used the same `ProposalInit`), so `ProposalFinMismatch` is not triggered. The wrong block hash is finalized. [9](#0-8) [10](#0-9) [11](#0-10)

### Citations

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L236-239)
```rust
    if built_block != received_fin.proposal_commitment {
        CONSENSUS_PROPOSAL_FIN_MISMATCH.increment(1);
        return Err(ValidateProposalError::ProposalFinMismatch);
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

**File:** crates/starknet_api/src/execution_resources.rs (L156-186)
```rust
    pub fn cost(&self, gas_prices: &GasPriceVector, tip: Tip) -> Fee {
        let tipped_l2_gas_price =
            gas_prices.l2_gas_price.checked_add(tip.into()).unwrap_or_else(|| {
                panic!(
                    "Tip overflowed: addition of L2 gas price ({}) and tip ({}) resulted in \
                     overflow.",
                    gas_prices.l2_gas_price, tip
                )
            });

        let mut sum = Fee(0);
        for (gas, price, resource) in [
            (self.l1_gas, gas_prices.l1_gas_price, Resource::L1Gas),
            (self.l1_data_gas, gas_prices.l1_data_gas_price, Resource::L1DataGas),
            (self.l2_gas, tipped_l2_gas_price, Resource::L2Gas),
        ] {
            let cost = gas.checked_mul(price.get()).unwrap_or_else(|| {
                panic!(
                    "{resource} cost overflowed: multiplication of gas amount ({gas}) by price \
                     per unit ({price}) resulted in overflow."
                )
            });
            sum = sum.checked_add(cost).unwrap_or_else(|| {
                panic!(
                    "Total cost overflowed: addition of current sum ({sum}) and cost of \
                     {resource} ({cost}) resulted in overflow."
                )
            });
        }
        sum
    }
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L1101-1115)
```rust
fn previous_block_info_from_block_header(
    block_header: &BlockHeaderWithoutHash,
) -> PreviousBlockInfo {
    PreviousBlockInfo {
        timestamp: block_header.timestamp.0,
        l1_prices_wei: L1PricesInWei {
            l1_gas_price: block_header.l1_gas_price.price_in_wei,
            l1_data_gas_price: block_header.l1_data_gas_price.price_in_wei,
        },
        l1_prices_fri: L1PricesInFri {
            l1_gas_price: block_header.l1_gas_price.price_in_fri,
            l1_data_gas_price: block_header.l1_data_gas_price.price_in_fri,
        },
    }
}
```
