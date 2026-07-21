### Title
Missing WEI Gas Price Bounds Validation in `is_block_info_valid` Allows Malicious Proposer to Corrupt Block Hash and L1 Handler Fee Accounting — (`File: crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

---

### Summary

`is_block_info_valid` validates the L1 gas prices in FRI (STRK) within a percentage margin, but performs **no validation** on the corresponding WEI (ETH) prices (`l1_gas_price_wei`, `l1_data_gas_price_wei`) that are also carried in `ProposalInit`. These unchecked WEI values are then used verbatim in `convert_to_sn_api_block_info` to derive the `eth_to_fri_rate` and compute `l2_gas_price_wei`, which is embedded in the block hash and used for ETH-denominated fee accounting. A malicious proposer can set the WEI prices to any non-zero value while keeping the FRI prices within the accepted margin, causing every validator to accept and re-execute the block with a wrong conversion rate, a wrong `l2_gas_price_wei`, and wrong ETH gas prices — all of which are committed into the block hash.

---

### Finding Description

**Step 1 — Proposer sends manipulated `ProposalInit`.**

`ProposalInit` carries six gas-price fields:

```
l2_gas_price_fri, l1_gas_price_fri, l1_data_gas_price_fri   ← validated
l1_gas_price_wei, l1_data_gas_price_wei                      ← NOT validated
```

A malicious proposer keeps the three FRI fields within the accepted margin and sets `l1_gas_price_wei` to an arbitrary non-zero value (e.g., `1`).

**Step 2 — `is_block_info_valid` passes.** [1](#0-0) 

The function checks:
- `l2_gas_price_fri` exact equality
- `l1_gas_price_fri` / `l1_data_gas_price_fri` within `l1_gas_price_margin_percent`

It never inspects `l1_gas_price_wei` or `l1_data_gas_price_wei`. The proposal passes validation.

**Step 3 — `convert_to_sn_api_block_info` derives a wrong `eth_to_fri_rate`.** [2](#0-1) 

`PreviousBlockInfo::from(init)` copies both the FRI and WEI prices from the proposer's `init`: [3](#0-2) 

`calculate_eth_to_fri_rate` then computes:

```
eth_to_fri_rate = l1_gas_price_fri * WEI_PER_ETH / l1_gas_price_wei
``` [4](#0-3) 

With `l1_gas_price_wei = 1`, the rate becomes `l1_gas_price_fri × 10^9`, which is orders of magnitude too large.

**Step 4 — Wrong `l2_gas_price_wei` is computed and embedded.**

```rust
let l2_gas_price_wei = NonzeroGasPrice::new(init.l2_gas_price_fri.fri_to_wei(eth_to_fri_rate)?)
``` [5](#0-4) 

`fri_to_wei` computes `l2_gas_price_fri * WEI_PER_ETH / eth_to_fri_rate`. With the inflated rate, `l2_gas_price_wei` collapses toward zero (or causes an error that rejects the proposal — a DoS path). With a deflated rate (large `l1_gas_price_wei`), `l2_gas_price_wei` becomes enormous.

**Step 5 — Wrong WEI prices enter the block hash.**

`PartialBlockHashComponents::new` records `l1_gas_price_per_token()`, which includes both `price_in_fri` and `price_in_wei`: [6](#0-5) 

`calculate_block_hash` chains these into the Poseidon hash: [7](#0-6) 

Every validator re-executes with the same manipulated `BlockInfo`, so all compute the same wrong block hash and reach consensus on it.

**Step 6 — Wrong ETH gas prices affect L1 handler fee accounting.**

`eth_gas_prices.l1_gas_price` is set directly from `init.l1_gas_price_wei`: [8](#0-7) 

L1 handler transactions use `FeeType::Eth`, so their fee checks use this wrong WEI price. Setting `l1_gas_price_wei` to `1` makes the ETH gas price effectively zero, allowing L1 handler transactions with insufficient L1 fees to pass the fee check and be included in the block.

---

### Impact Explanation

| Corrupted value | Effect |
|---|---|
| `l1_gas_price_wei` / `l1_data_gas_price_wei` in `BlockInfo.eth_gas_prices` | Wrong ETH-denominated fee check for L1 handler transactions — transactions with insufficient L1 fees are accepted |
| `eth_to_fri_rate` derived from manipulated WEI price | Wrong `l2_gas_price_wei` committed to block header |
| Block hash | Includes wrong WEI gas prices; the committed block hash does not reflect the true L1 gas price |

This matches **Critical — Incorrect fee, gas, bouncer, resource accounting, refund, balance, or L1 gas price effect with economic impact** and **Critical — Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input** (wrong revert/accept decision for L1 handler transactions).

---

### Likelihood Explanation

Any single validator whose turn it is to propose can trigger this. The proposer role rotates among all validators; no special privilege beyond being a validator is required. The manipulation is invisible to other validators because `is_block_info_valid` never inspects WEI prices. The attack is deterministic and requires no race condition.

---

### Recommendation

In `is_block_info_valid`, after validating the FRI prices, independently compute the expected WEI prices from the validator's own `eth_to_fri_rate` and verify that the proposed WEI prices are within the same percentage margin:

```rust
// After computing l1_gas_prices_fri from the validator's oracle:
let expected_l1_gas_price_wei = l1_gas_price_fri.fri_to_wei(validator_eth_to_fri_rate)?;
let expected_l1_data_gas_price_wei = l1_data_gas_price_fri.fri_to_wei(validator_eth_to_fri_rate)?;

if !(within_margin(init_proposed.l1_gas_price_wei, expected_l1_gas_price_wei, margin)
    && within_margin(init_proposed.l1_data_gas_price_wei, expected_l1_data_gas_price_wei, margin))
{
    return Err(ValidateProposalError::InvalidBlockInfo(...));
}
```

This mirrors the existing FRI-price check and closes the gap that allows the WEI prices — and therefore the `eth_to_fri_rate` and `l2_gas_price_wei` — to be freely manipulated.

---

### Proof of Concept

1. Validator A is the proposer for block N.
2. A constructs `ProposalInit` with:
   - `l1_gas_price_fri = oracle_value` (passes the margin check)
   - `l1_gas_price_wei = 1` (not checked; true value is ~10^9)
3. All other validators call `is_block_info_valid` — passes (WEI not checked).
4. All validators call `convert_to_sn_api_block_info`:
   - `eth_to_fri_rate = oracle_value * 10^9 / 1 = oracle_value * 10^9` (inflated by ~10^9×)
   - `l2_gas_price_wei = l2_gas_price_fri * 10^9 / (oracle_value * 10^9) ≈ l2_gas_price_fri / oracle_value` (deflated)
5. `BlockInfo.eth_gas_prices.l1_gas_price = 1 wei` — L1 handler fee check uses this price.
6. An L1 handler transaction with `max_fee = 1 wei` (normally far below the required fee) passes the fee check and is included in the block.
7. All validators compute the same wrong block hash (containing `l1_gas_price_wei = 1`) and reach BFT consensus on it.
8. The committed block hash and the proof facts derived from it encode the wrong ETH gas price.

### Citations

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L276-319)
```rust
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

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L99-112)
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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L223-236)
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
}
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L265-273)
```rust
            .chain_iter(
                gas_prices_to_hash(
                    &partial_block_hash_components.l1_gas_price,
                    &partial_block_hash_components.l1_data_gas_price,
                    &partial_block_hash_components.l2_gas_price,
                    &block_hash_version,
                )
                .iter(),
            )
```
