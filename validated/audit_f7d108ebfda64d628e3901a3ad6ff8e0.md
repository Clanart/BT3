### Title
Unvalidated Wei Gas Prices in `ProposalInit` Allow Byzantine Proposer to Corrupt `l2_gas_price_wei`, Block Hash Gas-Price Commitment, and ETH-Denominated Fee Calculations — (`crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

---

### Summary

`is_block_info_valid` validates only the FRI-denominated L1 gas prices from a received `ProposalInit` against the locally computed oracle values, but silently discards the computed wei prices and never validates `l1_gas_price_wei` / `l1_data_gas_price_wei`. Those unvalidated wei values are then used verbatim in `convert_to_sn_api_block_info` to derive the `eth_to_fri_rate` and, from it, `l2_gas_price_wei`. The resulting `BlockInfo` — with its corrupted wei prices — is passed to the batcher for execution and feeds directly into `PartialBlockHashComponents`, which is hashed into the block commitment accepted by consensus.

---

### Finding Description

**Root cause — `validate_proposal.rs`, `is_block_info_valid`**

```rust
let (l1_gas_prices_fri, _l1_gas_prices_wei) = get_l1_prices_in_fri_and_wei(
    l1_gas_price_provider,
    init_proposed.timestamp,
    block_info_validation.previous_block_info.as_ref(),
    gas_price_params,
)
.await;
// ...
if !(within_margin(l1_gas_price_fri_proposed, l1_gas_price_fri, ...)
    && within_margin(l1_data_gas_price_fri_proposed, l1_data_gas_price_fri, ...))
{
    return Err(ValidateProposalError::InvalidBlockInfo(...));
}
``` [1](#0-0) 

The validator fetches `_l1_gas_prices_wei` (note the underscore — it is intentionally unused) and only checks the FRI prices within a margin. `init_proposed.l1_gas_price_wei` and `init_proposed.l1_data_gas_price_wei` are never compared against the oracle-derived wei values.

**Propagation — `utils.rs`, `convert_to_sn_api_block_info`**

```rust
let previous_block_info = PreviousBlockInfo::from(init);
let eth_to_fri_rate = calculate_eth_to_fri_rate(&previous_block_info)?;
// eth_to_fri_rate = l1_gas_price_fri * WEI_PER_ETH / l1_gas_price_wei  (proposer-controlled)

let l2_gas_price_wei = NonzeroGasPrice::new(
    init.l2_gas_price_fri.fri_to_wei(eth_to_fri_rate)?
)?;
``` [2](#0-1) 

`calculate_eth_to_fri_rate` divides `l1_gas_price_fri * WEI_PER_ETH` by `l1_gas_price_wei`. Because `l1_gas_price_wei` is proposer-supplied and unvalidated, the rate — and therefore `l2_gas_price_wei` — can be set to any value the proposer chooses. [3](#0-2) 

The corrupted `BlockInfo` is then used in two critical paths:

1. **Block hash commitment** — `PartialBlockHashComponents::new` copies all three gas prices (including `l2_gas_price_wei`) into the components that are Poseidon-hashed into the block commitment: [4](#0-3) [5](#0-4) 

2. **ETH-denominated fee execution** — `eth_gas_prices.l1_gas_price`, `eth_gas_prices.l1_data_gas_price`, and `eth_gas_prices.l2_gas_price` are all taken from the corrupted `BlockInfo` and used by `get_fee_by_gas_vector` for `FeeType::Eth` transactions: [6](#0-5) 

---

### Impact Explanation

A Byzantine proposer sets `l1_gas_price_wei` to an arbitrary value (e.g., 1) while keeping `l1_gas_price_fri` within the allowed margin. This passes `is_block_info_valid` but causes:

- `eth_to_fri_rate` to be inflated by orders of magnitude.
- `l2_gas_price_wei` to be deflated to near-zero (or inflated to near-`u128::MAX`).
- `l1_gas_price_wei` / `l1_data_gas_price_wei` themselves to be wrong in `BlockInfo`.

Consequences:
1. **Wrong block hash** — the gas-price sub-hash in `gas_prices_to_hash` encodes the corrupted wei values; the resulting `PartialBlockHash` / final block hash diverges from the correct value.
2. **Wrong ETH-denominated fees** — any transaction paying fees in ETH (legacy `FeeType::Eth`) has its L1 gas, L1 data gas, and L2 gas costs computed against the manipulated wei prices, enabling under- or over-payment.
3. **Wrong `l2_gas_price_wei` stored in block header** — propagates to state sync, P2P header serialization, and any downstream consumer of `BlockHeaderWithoutHash.l2_gas_price.price_in_wei`. [7](#0-6) 

---

### Likelihood Explanation

Any validator node acting as proposer can craft a `ProposalInit` with an arbitrary `l1_gas_price_wei`. The FRI-price margin check is the only gate, and it does not constrain the wei field at all. No special privilege beyond being a scheduled proposer is required. The attack is deterministic and requires no brute-force.

---

### Recommendation

In `is_block_info_valid`, validate the proposed wei prices against the locally computed oracle values with the same margin logic applied to FRI prices:

```rust
let (l1_gas_prices_fri, l1_gas_prices_wei) = get_l1_prices_in_fri_and_wei(...).await;

if !(within_margin(l1_gas_price_fri_proposed,  l1_gas_prices_fri.l1_gas_price,      margin)
  && within_margin(l1_data_gas_price_fri_proposed, l1_gas_prices_fri.l1_data_gas_price, margin)
  && within_margin(init_proposed.l1_gas_price_wei,  l1_gas_prices_wei.l1_gas_price,     margin)
  && within_margin(init_proposed.l1_data_gas_price_wei, l1_gas_prices_wei.l1_data_gas_price, margin))
{
    return Err(ValidateProposalError::InvalidBlockInfo(...));
}
``` [1](#0-0) 

---

### Proof of Concept

1. Proposer constructs `ProposalInit` with:
   - `l1_gas_price_fri` = oracle value (passes margin check)
   - `l1_gas_price_wei` = 1 (instead of, say, 10^9)
2. Validator calls `is_block_info_valid`: FRI check passes; wei is never checked.
3. `convert_to_sn_api_block_info` computes:
   - `eth_to_fri_rate = l1_gas_price_fri * 10^18 / 1` ≈ `10^27` (instead of `10^18`)
   - `l2_gas_price_wei = l2_gas_price_fri * 10^18 / 10^27` = `l2_gas_price_fri / 10^9` ≈ 0 → `NonzeroGasPrice::new` returns `Err(ZeroGasPrice)`, aborting the proposal.
4. Alternatively, with `l1_gas_price_wei` = `10^18` (100× the real value):
   - `eth_to_fri_rate` is 100× too small
   - `l2_gas_price_wei` is 100× too large
   - Block hash encodes the inflated `l2_gas_price_wei`; ETH-denominated L2-gas fees are 100× too high [8](#0-7) [9](#0-8)

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

**File:** crates/blockifier/src/fee/fee_utils.rs (L139-146)
```rust
pub fn get_fee_by_gas_vector(
    block_info: &BlockInfo,
    gas_vector: GasVector,
    fee_type: &FeeType,
    tip: Tip,
) -> Fee {
    gas_vector.cost(block_info.gas_prices.gas_price_vector(fee_type), tip)
}
```

**File:** crates/apollo_protobuf/src/converters/header.rs (L161-171)
```rust
        let l2_gas_price = GasPricePerToken {
            price_in_fri: u128::from(
                value.l2_gas_price_fri.ok_or(missing("SignedBlockHeader::l2_gas_price_fri"))?,
            )
            .into(),

            price_in_wei: u128::from(
                value.l2_gas_price_wei.ok_or(missing("SignedBlockHeader::l2_gas_price_wei"))?,
            )
            .into(),
        };
```
