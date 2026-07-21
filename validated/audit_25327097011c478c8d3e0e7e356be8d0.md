### Title
Wrong Reference Base in `within_margin` Allows Proposer to Embed L1 Gas Price Beyond Intended Deviation Bound - (File: crates/apollo_consensus_orchestrator/src/validate_proposal.rs)

### Summary

The `within_margin` function in `validate_proposal.rs` computes the allowed deviation margin relative to `number1` (the **proposed** price) instead of `number2` (the locally computed **reference** price). This is the direct sequencer analog of the external oracle bug: the denominator in the deviation check uses the wrong value. The result is an asymmetric validation window — when a proposer inflates the L1 gas price, the effective allowed deviation is ~11.1% instead of the intended 10%, letting a proposer embed a higher-than-permitted gas price into the block info that validators will accept.

### Finding Description

In `is_block_info_valid`, the validator checks whether the proposed L1 gas price is within an acceptable margin of the locally computed reference price:

```rust
// crates/apollo_consensus_orchestrator/src/validate_proposal.rs, lines 302-307
if !(within_margin(l1_gas_price_fri_proposed, l1_gas_price_fri, l1_gas_price_margin_percent)
    && within_margin(
        l1_data_gas_price_fri_proposed,
        l1_data_gas_price_fri,
        l1_gas_price_margin_percent,
    ))
```

`number1 = proposed`, `number2 = reference`. The `within_margin` function is:

```rust
// lines 323-333
fn within_margin(number1: GasPrice, number2: GasPrice, margin_percent: u128) -> bool {
    if number1.0.abs_diff(number2.0) <= GAS_PRICE_ABS_DIFF_MARGIN {
        return true;
    }
    let margin = (number1.0 * margin_percent) / 100;   // ← BUG: uses number1 (proposed), not number2 (reference)
    number1.0.abs_diff(number2.0) <= margin
}
```

The margin is `proposed * margin_percent / 100`. The correct formula is `reference * margin_percent / 100`.

**Exact arithmetic divergence** (with `l1_gas_price_margin_percent = 10`, reference = 1000 fri):

| Formula | Max allowed proposed price | Effective upward deviation |
|---|---|---|
| Buggy (`proposed * 10%`) | `reference / 0.9 ≈ 1111` | ~11.1% |
| Correct (`reference * 10%`) | `reference * 1.1 = 1100` | 10.0% |

Verification: `number1=1111, number2=1000, margin_percent=10` → `margin = 1111*10/100 = 111`, `abs_diff = 111`, `111 ≤ 111` → **passes** (should fail).

Downward case is the mirror: the current formula is more restrictive than intended (allows only ~9.1% below reference instead of 10%), which can cause valid proposals to be incorrectly rejected. [1](#0-0) 

The reference price is computed by `get_l1_prices_in_fri_and_wei`, which applies `apply_fee_transformations` (tip addition, multiplier, min/max clamp) to the L1 oracle data: [2](#0-1) 

The `l1_gas_price_margin_percent` is 10 in all deployed versioned constants: [3](#0-2) 

### Impact Explanation

The L1 gas price embedded in `ProposalInit` flows directly into `convert_to_sn_api_block_info`, which constructs the `BlockInfo` used for all transaction fee computation in the block: [4](#0-3) 

A proposer can set `l1_gas_price_fri` and `l1_data_gas_price_fri` up to ~11.1% above the reference (instead of the intended 10%). Every transaction in that block is charged fees based on this inflated price. The block hash also incorporates these gas prices via `PartialBlockHashComponents`, so the wrong price is committed on-chain.

This matches: **Critical — Incorrect fee/L1 gas price effect with economic impact.**

### Likelihood Explanation

Any validator whose turn it is to propose can exploit this. No special privilege beyond being a proposer is required. The extra deviation is small (~1.1% above the intended cap) but systematic: a proposer can reliably set the gas price at `reference / 0.9` on every block they propose, and all validators will accept it. The existing test `gas_price_fri_out_of_range` only tests a 2× multiplier (well outside the margin), so the off-by-one-percent boundary is not covered. [5](#0-4) 

### Recommendation

Change the margin base from `number1` (proposed) to `number2` (reference):

```rust
fn within_margin(number1: GasPrice, number2: GasPrice, margin_percent: u128) -> bool {
    if number1.0.abs_diff(number2.0) <= GAS_PRICE_ABS_DIFF_MARGIN {
        return true;
    }
    let margin = (number2.0 * margin_percent) / 100;  // use reference, not proposed
    number1.0.abs_diff(number2.0) <= margin
}
```

This makes the allowed window symmetric and anchored to the reference price, matching the intended semantics: `|proposed - reference| ≤ reference * margin_percent / 100`.

### Proof of Concept

```
reference  = 1000 fri  (locally computed by validator)
margin     = 10%
proposed   = 1111 fri  (set by malicious proposer)

Buggy check:
  margin = 1111 * 10 / 100 = 111
  abs_diff = |1111 - 1000| = 111
  111 <= 111  →  PASSES  (proposal accepted)

Correct check:
  margin = 1000 * 10 / 100 = 100
  abs_diff = |1111 - 1000| = 111
  111 <= 100  →  FAILS   (proposal rejected)
```

The proposer embeds `l1_gas_price_fri = 1111` instead of the maximum intended `1100`. Every transaction in the block is charged fees at 1111 fri/gas instead of 1100 fri/gas — a ~1% overcharge that is committed into the block hash and accepted by all validators.

### Citations

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L302-319)
```rust
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

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L272-285)
```rust
pub(crate) fn apply_fee_transformations(
    price_info: &mut PriceInfo,
    gas_price_params: &GasPriceParams,
) {
    price_info.base_fee_per_gas = price_info
        .base_fee_per_gas
        .saturating_add(gas_price_params.l1_gas_tip_wei)
        .clamp(gas_price_params.min_l1_gas_price_wei, gas_price_params.max_l1_gas_price_wei);

    price_info.blob_fee = GasPrice(
        (gas_price_params.l1_data_gas_price_multiplier * price_info.blob_fee.0).to_integer(),
    )
    .clamp(gas_price_params.min_l1_data_gas_price_wei, gas_price_params.max_l1_data_gas_price_wei);
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

**File:** crates/apollo_consensus_orchestrator/resources/orchestrator_versioned_constants_0_14_1.json (L1-7)
```json
{
    "gas_price_max_change_denominator": 48,
    "gas_target": 4000000000,
    "max_block_size": 5000000000,
    "min_gas_price": "0x1dcd65000",
    "l1_gas_price_margin_percent": 10
}
```
