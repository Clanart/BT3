### Title
Missing Validation of `l1_gas_price_wei`/`l1_data_gas_price_wei` in `is_block_info_valid` Allows Malicious Proposer to Corrupt ETH-to-FRI Conversion Rate, `l2_gas_price_wei`, Fee Accounting, and Block Hash — (`crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

---

### Summary

`is_block_info_valid` validates the proposed `l1_gas_price_fri` and `l1_data_gas_price_fri` within a configurable percentage margin, but it computes the expected WEI prices and immediately discards them (`_l1_gas_prices_wei`). The proposed `l1_gas_price_wei` and `l1_data_gas_price_wei` fields are never range-checked against the locally observed L1 prices. A malicious proposer can therefore set these WEI fields to arbitrary values. Because `convert_to_sn_api_block_info` derives the ETH-to-FRI conversion rate directly from the ratio `l1_gas_price_fri / l1_gas_price_wei`, a tampered WEI price silently corrupts the rate, the derived `l2_gas_price_wei`, every ETH-denominated fee calculation in the executed block, and the gas-price fields committed to the block hash.

---

### Finding Description

**Root cause — `is_block_info_valid` discards the computed WEI prices without comparing them to the proposal:**

```rust
// validate_proposal.rs  line 286-292
let (l1_gas_prices_fri, _l1_gas_prices_wei) = get_l1_prices_in_fri_and_wei(
    l1_gas_price_provider,
    init_proposed.timestamp,
    block_info_validation.previous_block_info.as_ref(),
    gas_price_params,
)
.await;
```

The underscore-prefixed binding `_l1_gas_prices_wei` is never used again. Only the FRI prices are checked:

```rust
// validate_proposal.rs  lines 302-318
if !(within_margin(l1_gas_price_fri_proposed, l1_gas_price_fri, l1_gas_price_margin_percent)
    && within_margin(
        l1_data_gas_price_fri_proposed,
        l1_data_gas_price_fri,
        l1_gas_price_margin_percent,
    ))
{
    return Err(ValidateProposalError::InvalidBlockInfo(...));
}
``` [1](#0-0) 

**Downstream corruption — `convert_to_sn_api_block_info` derives the ETH-to-FRI rate from the unchecked WEI field:**

```rust
// utils.rs  lines 304-314
let previous_block_info = PreviousBlockInfo::from(init);   // uses init.l1_gas_price_wei
let eth_to_fri_rate = calculate_eth_to_fri_rate(&previous_block_info)?;
let l2_gas_price_wei =
    NonzeroGasPrice::new(init.l2_gas_price_fri.fri_to_wei(eth_to_fri_rate)?)?;
``` [2](#0-1) 

`calculate_eth_to_fri_rate` computes `l1_gas_price_fri * WEI_PER_ETH / l1_gas_price_wei`:

```rust
// utils.rs  lines 489-515
let eth_to_fri_rate = block_info
    .l1_prices_fri.l1_gas_price.0
    .checked_mul(WEI_PER_ETH)...?
    .checked_div(block_info.l1_prices_wei.l1_gas_price.0)...?;
``` [3](#0-2) 

If `l1_gas_price_wei` is attacker-controlled, the derived `eth_to_fri_rate` is wrong, and so is every value that depends on it: `l2_gas_price_wei`, `eth_gas_prices.l1_gas_price`, and `eth_gas_prices.l1_data_gas_price` in the `BlockInfo` passed to the batcher.

**`ProposalInit` carries both WEI and FRI fields; the design comment acknowledges they should be independently verifiable:**

```rust
// consensus.rs  lines 89-93
/// This struct differs from `BlockInfo` in `starknet_api` because we send L1 gas prices in ETH
/// and include the ETH to STRK conversion rate. This allows for more informative validations,
/// as we can distinguish whether an issue comes from the L1 price reading or the conversion
/// rate instead of comparing after multiplication.
``` [4](#0-3) 

The design intent is that both representations are validated; the implementation only validates one.

---

### Impact Explanation

| Corrupted value | How it propagates |
|---|---|
| `l1_gas_price_wei` (wrong) | Stored verbatim in `BlockInfo.eth_gas_prices.l1_gas_price`; used for ETH-denominated L1 gas fee calculations |
| `eth_to_fri_rate` (wrong) | Derived from the tampered WEI price; used to compute `l2_gas_price_wei` |
| `l2_gas_price_wei` (wrong) | Stored in `BlockInfo.eth_gas_prices.l2_gas_price`; used for ETH-denominated L2 gas fee calculations |
| Fee token balance storage | ETH-denominated fees charged at wrong rate → wrong storage diffs → wrong state root |
| Block hash | `l1_gas_price_wei` and `l1_data_gas_price_wei` are chained into `calculate_block_hash` via `PartialBlockHashComponents`; a tampered WEI price produces a wrong committed block hash |

Matching impacts:
- **Critical** — Incorrect fee, gas, resource accounting, balance, or L1 gas price effect with economic impact.
- **Critical** — Wrong state (fee token balances) from blockifier execution logic for accepted input.

---

### Likelihood Explanation

Requires a malicious consensus proposer (a validator whose turn it is to propose). In Tendermint-style consensus any validator can be a proposer in a given round. The check is purely local to each validating node; no on-chain enforcement prevents a proposer from setting `l1_gas_price_wei` to an arbitrary non-zero value while keeping `l1_gas_price_fri` within the accepted margin. Other validators will accept the proposal because `is_block_info_valid` never compares the proposed WEI prices to locally observed values.

---

### Recommendation

In `is_block_info_valid`, use the already-computed `_l1_gas_prices_wei` (rename to `l1_gas_prices_wei`) and add a symmetric `within_margin` check for both WEI prices, mirroring the existing FRI check:

```rust
let (l1_gas_prices_fri, l1_gas_prices_wei) = get_l1_prices_in_fri_and_wei(...).await;

// existing FRI checks ...

if !(within_margin(init_proposed.l1_gas_price_wei,
                   l1_gas_prices_wei.l1_gas_price,
                   l1_gas_price_margin_percent)
    && within_margin(init_proposed.l1_data_gas_price_wei,
                     l1_gas_prices_wei.l1_data_gas_price,
                     l1_gas_price_margin_percent))
{
    return Err(ValidateProposalError::InvalidBlockInfo(...));
}
```

This closes the gap between the design intent (independent validation of both representations) and the implementation.

---

### Proof of Concept

1. Malicious proposer constructs a `ProposalInit` for height H with:
   - `l1_gas_price_fri = V` (within the accepted margin of the locally observed FRI price)
   - `l1_gas_price_wei = 1` (minimum non-zero; far below the actual ~10 Gwei baseline)
2. `is_block_info_valid` on every validating node:
   - Computes expected FRI prices → `V` is within margin → **passes**
   - Computes expected WEI prices → discards them (`_l1_gas_prices_wei`) → **no check**
3. `convert_to_sn_api_block_info` derives `eth_to_fri_rate = V * 10^18 / 1 = V * 10^18` (≈ 10^27 for typical FRI prices), which is orders of magnitude larger than the true rate.
4. `l2_gas_price_wei = l2_gas_price_fri * 10^18 / (V * 10^18) = l2_gas_price_fri / V`. For typical values where `l2_gas_price_fri > V`, this yields a non-zero but drastically underpriced `l2_gas_price_wei`.
5. The batcher executes the block with this `BlockInfo`. All ETH-denominated L2 gas fees are charged at a fraction of the correct rate. Fee token balance storage diffs are wrong. The committed state root is wrong. The block hash chains the tampered `l1_gas_price_wei = 1`, producing a wrong committed block hash. [5](#0-4) [6](#0-5)

### Citations

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L244-320)
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

**File:** crates/apollo_protobuf/src/consensus.rs (L89-124)
```rust
/// This message must be sent first when proposing a new block.
/// This struct differs from `BlockInfo` in `starknet_api` because we send L1 gas prices in ETH and
/// include the ETH to STRK conversion rate. This allows for more informative validations, as we can
/// distinguish whether an issue comes from the L1 price reading or the conversion rate instead of
/// comparing after multiplication.
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
```
