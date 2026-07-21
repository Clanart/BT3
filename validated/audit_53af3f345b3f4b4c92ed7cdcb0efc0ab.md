### Title
Unchecked `l1_gas_price_wei` / `l1_data_gas_price_wei` in `ProposalInit` lets a malicious proposer set arbitrary ETH gas prices, corrupting ETH fee accounting and the ETH/STRK conversion rate for the block - (`crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

---

### Summary

`is_block_info_valid` validates the FRI-denominated L1 gas prices from a received `ProposalInit` against the local oracle, but silently discards the oracle's WEI prices and never compares them against the proposer-supplied `l1_gas_price_wei` / `l1_data_gas_price_wei`. Those two unchecked fields flow directly into `convert_to_sn_api_block_info`, where they become the `eth_gas_prices` used by the blockifier for every ETH-denominated fee calculation in the block. A malicious proposer can therefore set them to any value while keeping the FRI prices within the accepted margin, causing wrong ETH fees, wrong ETH fee-token balances, wrong receipts, and a corrupted ETH/STRK conversion rate that poisons the next block's fallback path.

---

### Finding Description

**Root cause — oracle WEI prices are fetched but thrown away during validation.**

In `is_block_info_valid`:

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

The underscore-prefixed `_l1_gas_prices_wei` is never used again. The function then validates only the FRI prices:

```rust
// validate_proposal.rs  lines 302-318
if !(within_margin(l1_gas_price_fri_proposed, l1_gas_price_fri, ...)
    && within_margin(l1_data_gas_price_fri_proposed, l1_data_gas_price_fri, ...))
{
    return Err(ValidateProposalError::InvalidBlockInfo(...));
}
```

`init_proposed.l1_gas_price_wei` and `init_proposed.l1_data_gas_price_wei` are never compared against anything.

**Propagation — unchecked WEI prices become the block's ETH gas prices.**

After `is_block_info_valid` returns `Ok`, `initiate_validation` calls `convert_to_sn_api_block_info(init)`:

```rust
// utils.rs  lines 301-302, 325-328
let l1_gas_price_wei    = NonzeroGasPrice::new(init.l1_gas_price_wei)?;
let l1_data_gas_price_wei = NonzeroGasPrice::new(init.l1_data_gas_price_wei)?;
...
eth_gas_prices: GasPriceVector {
    l1_gas_price:      l1_gas_price_wei,
    l1_data_gas_price: l1_data_gas_price_wei,
    l2_gas_price:      l2_gas_price_wei,   // derived from these via eth_to_fri_rate
},
```

The resulting `BlockInfo` is passed to the batcher via `ValidateBlockInput`, which uses it for all execution. Every ETH-denominated fee is computed as:

```rust
// fee_utils.rs  line 145
gas_vector.cost(block_info.gas_prices.gas_price_vector(&FeeType::Eth), tip)
```

**Secondary corruption — ETH/STRK rate for the next block.**

`convert_to_sn_api_block_info` also calls:

```rust
// utils.rs  lines 304-305
let previous_block_info = PreviousBlockInfo::from(init);   // uses init.l1_gas_price_wei
let eth_to_fri_rate = calculate_eth_to_fri_rate(&previous_block_info)?;
```

`calculate_eth_to_fri_rate` divides `l1_gas_price_fri * WEI_PER_ETH` by `l1_gas_price_wei`. A manipulated WEI price produces a wrong rate, which is then stored as `previous_block_info` after `decision_reached` and used as the oracle fallback for the next block.

**Contrast with the proposer's own build path.**

During `build_proposal`, `initiate_build` calls `get_l1_prices_in_fri_and_wei` and uses both the FRI and WEI values it returns, with `apply_fee_transformations` clamping the WEI prices to `[min_l1_gas_price_wei, max_l1_gas_price_wei]`. The validator path applies no equivalent clamp or comparison to the received WEI prices.

---

### Impact Explanation

**Critical — Incorrect fee / balance / L1 gas price effect with economic impact; wrong state and receipt from execution logic.**

- Every ETH-paying transaction in the block is charged a fee computed from the manipulated WEI price. Setting `l1_gas_price_wei = u128::MAX` causes the ETH fee to saturate, draining the sender's ETH balance to zero; setting it to 1 causes near-zero ETH fees, letting transactions execute for free.
- The resulting ETH fee-token balance changes are committed to state, producing wrong storage values and wrong receipts that are included in the block commitment.
- The corrupted `eth_to_fri_rate` derived from the manipulated WEI price is stored as `previous_block_info` and used as the oracle fallback for the next block, propagating the error.

---

### Likelihood Explanation

Any validator that wins a proposer slot can trigger this. No special privilege beyond being selected as proposer is required. The FRI prices must stay within `l1_gas_price_margin_percent` of the local oracle reading (a percentage-based window), but the WEI prices are completely unconstrained. The attack is therefore reachable by any single malicious validator in the committee.

---

### Recommendation

In `is_block_info_valid`, after fetching the oracle's WEI prices, validate the proposed WEI prices against them with the same `within_margin` check already applied to the FRI prices:

```rust
let (l1_gas_prices_fri, l1_gas_prices_wei) = get_l1_prices_in_fri_and_wei(...).await;

// existing FRI checks ...

// Add WEI checks:
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

Alternatively, derive the WEI prices from the validated FRI prices and the oracle's ETH/STRK rate inside `convert_to_sn_api_block_info`, ignoring the proposer-supplied WEI values entirely.

---

### Proof of Concept

1. A malicious validator is selected as proposer for block N.
2. It calls `build_proposal` normally, obtaining valid FRI prices from the oracle.
3. Before broadcasting the `ProposalInit`, it overwrites `l1_gas_price_wei = u128::MAX` and `l1_data_gas_price_wei = u128::MAX` while keeping `l1_gas_price_fri` and `l1_data_gas_price_fri` within the accepted margin.
4. Validators receive the `ProposalInit` and call `is_block_info_valid`.
5. `get_l1_prices_in_fri_and_wei` returns the correct oracle WEI prices, but they are bound to `_l1_gas_prices_wei` and never used.
6. The FRI `within_margin` checks pass; `is_block_info_valid` returns `Ok`.
7. `convert_to_sn_api_block_info` reads `init.l1_gas_price_wei = u128::MAX` and places it in `eth_gas_prices.l1_gas_price`.
8. The batcher executes all ETH-paying transactions using `l1_gas_price = u128::MAX`; every such transaction's fee saturates, draining sender ETH balances.
9. The block is committed with wrong ETH balances, wrong receipts, and a corrupted `eth_to_fri_rate` stored in `previous_block_info` for block N+1.

**Key files and lines:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L286-292)
```rust
    let (l1_gas_prices_fri, _l1_gas_prices_wei) = get_l1_prices_in_fri_and_wei(
        l1_gas_price_provider,
        init_proposed.timestamp,
        block_info_validation.previous_block_info.as_ref(),
        gas_price_params,
    )
    .await;
```

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

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L299-333)
```rust
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

**File:** crates/starknet_api/src/block.rs (L601-626)
```rust
// TODO(Arni): Remove derive of Default. Gas prices should always be set.
#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
pub struct GasPrices {
    pub eth_gas_prices: GasPriceVector,  // In wei.
    pub strk_gas_prices: GasPriceVector, // In fri.
}

impl GasPrices {
    pub fn l1_gas_price(&self, fee_type: &FeeType) -> NonzeroGasPrice {
        self.gas_price_vector(fee_type).l1_gas_price
    }

    pub fn l1_data_gas_price(&self, fee_type: &FeeType) -> NonzeroGasPrice {
        self.gas_price_vector(fee_type).l1_data_gas_price
    }

    pub fn l2_gas_price(&self, fee_type: &FeeType) -> NonzeroGasPrice {
        self.gas_price_vector(fee_type).l2_gas_price
    }

    pub fn gas_price_vector(&self, fee_type: &FeeType) -> &GasPriceVector {
        match fee_type {
            FeeType::Strk => &self.strk_gas_prices,
            FeeType::Eth => &self.eth_gas_prices,
        }
    }
```

**File:** crates/blockifier/src/fee/fee_utils.rs (L138-146)
```rust
/// Converts the gas vector to a fee.
pub fn get_fee_by_gas_vector(
    block_info: &BlockInfo,
    gas_vector: GasVector,
    fee_type: &FeeType,
    tip: Tip,
) -> Fee {
    gas_vector.cost(block_info.gas_prices.gas_price_vector(fee_type), tip)
}
```
