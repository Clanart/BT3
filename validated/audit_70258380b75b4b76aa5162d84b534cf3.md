### Title
Unvalidated L1/L2 Gas Price Wei Values in Consensus Proposal Allow Wrong Block Hash Commitment — (File: `crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

---

### Summary

The consensus validator checks only FRI-denominated gas prices when validating a proposer's `ProposalInit`, but both WEI and FRI gas prices are committed into the block hash via `gas_prices_to_hash`. A malicious proposer can set arbitrary WEI gas prices that pass validation, causing the committed block hash to include wrong gas price values and causing wrong fee accounting for L1 handler transactions.

---

### Finding Description

In `is_block_info_valid` in `validate_proposal.rs`, the validator fetches both WEI and FRI gas prices from its oracle but explicitly discards the WEI prices:

```rust
let (l1_gas_prices_fri, _l1_gas_prices_wei) = get_l1_prices_in_fri_and_wei(
    l1_gas_price_provider,
    init_proposed.timestamp,
    block_info_validation.previous_block_info.as_ref(),
    gas_price_params,
)
.await;
```

Only FRI prices are then validated with a margin check:

```rust
if !(within_margin(l1_gas_price_fri_proposed, l1_gas_price_fri, l1_gas_price_margin_percent)
    && within_margin(
        l1_data_gas_price_fri_proposed,
        l1_data_gas_price_fri,
        l1_gas_price_margin_percent,
    ))
``` [1](#0-0) 

The unvalidated WEI prices (`l1_gas_price_wei`, `l1_data_gas_price_wei`) from `ProposalInit` flow through:

1. `convert_to_sn_api_block_info(init)` → `BlockInfo.gas_prices` → used for transaction execution and fee validation
2. `PartialBlockHashComponents::new(&block_info, header_commitments)` → `PartialBlockHash` → `ProposalCommitment`
3. `gas_prices_to_hash()` → committed into the final block hash [2](#0-1) [3](#0-2) 

For Starknet version ≥ 0.13.4, `gas_prices_to_hash` commits all six gas price values (including all three WEI prices) into a single Poseidon hash that becomes part of the block hash:

```rust
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
``` [4](#0-3) 

For older versions, four values including WEI prices are committed directly into the block hash chain. In both cases, the WEI prices are part of the committed block hash.

The `ProposalInit` struct carries both WEI and FRI prices as independent fields: [5](#0-4) 

The `l2_gas_price_fri` is validated by exact equality, but `l2_gas_price_wei` is also unvalidated and committed into the block hash for v0.13.4+.

---

### Impact Explanation

A malicious proposer can set `l1_gas_price_wei` and `l1_data_gas_price_wei` to arbitrary values while keeping FRI prices within the validation margin. This causes:

1. **Wrong block hash commitment**: The block hash commits wrong WEI gas prices, producing a `PartialBlockHash` (= `ProposalCommitment`) that doesn't reflect the true L1 gas prices. The final block hash committed to L1 is wrong.
2. **Wrong fee accounting for L1 handler transactions**: L1 handler transactions are validated against the wrong WEI prices via `BlockInfo.gas_prices`, causing incorrect fee accounting with economic impact.
3. **Wrong RPC fee estimation**: The RPC `estimateFee` and `simulateTransactions` for the pending block use the pending block's WEI gas prices. With wrong WEI prices, these return authoritative-looking wrong fee values for L1 handler transactions.
4. **Wrong `PartialBlockHashComponents` in storage**: The stored partial block hash components have wrong WEI prices, which propagate to the final block hash. [6](#0-5) 

---

### Likelihood Explanation

Requires a malicious validator to be selected as proposer in a multi-validator setup. The attack is straightforward: set WEI prices to an extreme value while keeping FRI prices within the normal margin. The validator's `is_block_info_valid` will accept the proposal because it only checks FRI prices. The `within_margin` function is a percentage check on FRI values only; WEI values are never compared. [7](#0-6) 

---

### Recommendation

In `is_block_info_valid`, also validate the WEI gas prices against the validator's own oracle reading. Since WEI and FRI prices are related by the ETH/STRK rate, the validator can compute the expected WEI prices from its own oracle (already fetched but discarded as `_l1_gas_prices_wei`) and apply the same `within_margin` check. The variable `_l1_gas_prices_wei` should be renamed and used for validation.

---

### Proof of Concept

1. Malicious proposer is selected for block N.
2. Proposer sets `l1_gas_price_fri` = correct value (within `l1_gas_price_margin_percent` of validator's oracle).
3. Proposer sets `l1_gas_price_wei` = 100× the correct value.
4. Validator's `is_block_info_valid` only checks FRI prices → proposal passes validation.
5. Block is executed with inflated WEI prices via `convert_to_sn_api_block_info(init)`.
6. `PartialBlockHashComponents::new(&block_info, ...)` captures the inflated WEI price.
7. `gas_prices_to_hash` commits the inflated WEI price into the block hash.
8. `ProposalCommitment` (= `PartialBlockHash`) is computed from the inflated WEI price; consensus agrees on this wrong commitment.
9. Final block hash committed to L1 includes the inflated WEI price.
10. RPC `estimateFee` for L1 handler transactions in the pending block returns 100× the correct fee.

This is the sequencer analog of the oracle arbitrage: just as the DeFi pool's price is sensitive to oracle updates and can be exploited by front-running, the sequencer's block hash commitment is sensitive to the gas price oracle, and a malicious proposer can commit wrong WEI prices that bypass the FRI-only validation check, producing a wrong block hash commitment and wrong fee estimation.

### Citations

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

**File:** crates/apollo_batcher/src/block_builder.rs (L160-169)
```rust
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

**File:** crates/apollo_rpc_execution/src/lib.rs (L331-399)
```rust
) -> ExecutionResult<BlockContext> {
    let (
        block_number,
        block_timestamp,
        l1_gas_price,
        l1_data_gas_price,
        l2_gas_price,
        sequencer_address,
        l1_da_mode,
    ) = match maybe_pending_data {
        Some(pending_data) => (
            block_context_number.unchecked_next(),
            pending_data.timestamp,
            pending_data.l1_gas_price,
            pending_data.l1_data_gas_price,
            pending_data.l2_gas_price,
            pending_data.sequencer,
            pending_data.l1_da_mode,
        ),
        None => {
            let header = storage_reader
                .begin_ro_txn()?
                .get_block_header(block_context_number)?
                .expect("Should have block header.")
                .block_header_without_hash;
            (
                header.block_number,
                header.timestamp,
                header.l1_gas_price,
                header.l1_data_gas_price,
                header.l2_gas_price,
                header.sequencer,
                header.l1_da_mode,
            )
        }
    };
    let ten_blocks_ago = get_10_blocks_ago(&block_context_number, cached_state)?;

    let use_kzg_da = if override_kzg_da_to_false { false } else { l1_da_mode.is_use_kzg_da() };
    let starknet_version = storage_reader
        .begin_ro_txn()?
        .get_starknet_version(block_number)?
        .unwrap_or(StarknetVersion::LATEST);
    let block_info = BlockInfo {
        block_timestamp,
        sequencer_address: sequencer_address.0,
        use_kzg_da,
        block_number,
        // TODO(yair): What to do about blocks pre 0.13.1 where the data gas price were 0?
        gas_prices: GasPrices {
            eth_gas_prices: GasPriceVector {
                l1_gas_price: NonzeroGasPrice::new(l1_gas_price.price_in_wei)
                    .unwrap_or(NonzeroGasPrice::MIN),
                l1_data_gas_price: NonzeroGasPrice::new(l1_data_gas_price.price_in_wei)
                    .unwrap_or(NonzeroGasPrice::MIN),
                l2_gas_price: NonzeroGasPrice::new(l2_gas_price.price_in_wei)
                    .unwrap_or(NonzeroGasPrice::MIN),
            },
            strk_gas_prices: GasPriceVector {
                l1_gas_price: NonzeroGasPrice::new(l1_gas_price.price_in_fri)
                    .unwrap_or(NonzeroGasPrice::MIN),
                l1_data_gas_price: NonzeroGasPrice::new(l1_data_gas_price.price_in_fri)
                    .unwrap_or(NonzeroGasPrice::MIN),
                l2_gas_price: NonzeroGasPrice::new(l2_gas_price.price_in_fri)
                    .unwrap_or(NonzeroGasPrice::MIN),
            },
        },
        starknet_version,
    };
```
