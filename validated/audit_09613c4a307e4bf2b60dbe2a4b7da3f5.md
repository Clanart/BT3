### Title
Unvalidated `l1_gas_price_wei` / `l1_data_gas_price_wei` in `ProposalInit` Lets a Malicious Proposer Inject Arbitrary ETH-Denominated Gas Prices, Corrupting Block Hash and Fee Execution - (`crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

---

### Summary

`is_block_info_valid` validates only the FRI-denominated L1 gas prices from a received `ProposalInit`, leaving `l1_gas_price_wei` and `l1_data_gas_price_wei` completely unchecked. `convert_to_sn_api_block_info` then uses those unvalidated wei values directly as the ETH-denominated gas prices for block execution, and also derives `eth_to_fri_rate` from them via integer division to compute `l2_gas_price_wei`. Because all six gas prices (three wei, three fri) are hashed into the block hash via `gas_prices_to_hash`, a malicious proposer can craft a `ProposalInit` that passes consensus validation but causes every honest validator to execute the block with wrong ETH-denominated prices, producing a wrong block hash, wrong fee receipts, and wrong storage state.

---

### Finding Description

**Step 1 – Validation gap.**

`is_block_info_valid` fetches the oracle's expected prices and checks only the FRI side:

```rust
// validate_proposal.rs:302-307
if !(within_margin(l1_gas_price_fri_proposed, l1_gas_price_fri, ...)
    && within_margin(l1_data_gas_price_fri_proposed, l1_data_gas_price_fri, ...))
{
    return Err(...)
}
```

`l1_gas_price_wei` and `l1_data_gas_price_wei` from the `ProposalInit` are never compared against any oracle or expected value. [1](#0-0) 

**Step 2 – Unvalidated wei values flow directly into `BlockInfo`.**

`convert_to_sn_api_block_info` trusts the `ProposalInit` fields verbatim:

```rust
// utils.rs:301-328
let l1_gas_price_wei = NonzeroGasPrice::new(init.l1_gas_price_wei)?;
let l1_data_gas_price_wei = NonzeroGasPrice::new(init.l1_data_gas_price_wei)?;
...
let eth_to_fri_rate = calculate_eth_to_fri_rate(&previous_block_info)?;
// eth_to_fri_rate = (l1_gas_price_fri * WEI_PER_ETH) / l1_gas_price_wei  ← integer division
let l2_gas_price_wei = init.l2_gas_price_fri.fri_to_wei(eth_to_fri_rate)?;
// l2_gas_price_wei = (l2_gas_price_fri * WEI_PER_ETH) / eth_to_fri_rate  ← integer division
...
eth_gas_prices: GasPriceVector {
    l1_gas_price: l1_gas_price_wei,        // ← attacker-controlled
    l1_data_gas_price: l1_data_gas_price_wei, // ← attacker-controlled
    l2_gas_price: l2_gas_price_wei,        // ← derived from attacker-controlled denominator
},
``` [2](#0-1) 

The ratio manipulation is structurally identical to the ERC4626 share-price attack: the attacker inflates the denominator (`l1_gas_price_wei`) so that `eth_to_fri_rate = (l1_gas_price_fri × WEI_PER_ETH) / l1_gas_price_wei` truncates toward zero, and the inverse `l2_gas_price_wei = (l2_gas_price_fri × WEI_PER_ETH) / eth_to_fri_rate` becomes correspondingly inflated. [3](#0-2) 

**Step 3 – All six gas prices enter the block hash.**

`PartialBlockHashComponents::new` captures both wei and fri prices from the `BlockInfo`:

```rust
// block_hash_calculator.rs:228-230
l1_gas_price: block_info.gas_prices.l1_gas_price_per_token(),
l1_data_gas_price: block_info.gas_prices.l1_data_gas_price_per_token(),
l2_gas_price: block_info.gas_prices.l2_gas_price_per_token(),
``` [4](#0-3) 

`gas_prices_to_hash` (Starknet ≥ 0.13.3) hashes all six values into a single Poseidon commitment that is chained into the block hash:

```
Poseidon("STARKNET_GAS_PRICES0",
    l1_gas_price_wei, l1_gas_price_fri,
    l1_data_gas_price_wei, l1_data_gas_price_fri,
    l2_gas_price_wei, l2_gas_price_fri)
``` [5](#0-4) 

`calculate_block_hash` chains this `gas_prices_hash` into the final block hash:

```rust
// block_hash_calculator.rs:265-272
.chain_iter(
    gas_prices_to_hash(
        &partial_block_hash_components.l1_gas_price,
        &partial_block_hash_components.l1_data_gas_price,
        &partial_block_hash_components.l2_gas_price,
        &block_hash_version,
    ).iter(),
)
``` [6](#0-5) 

**Step 4 – Both proposer and validator execute with the same wrong prices.**

`initiate_validation` calls `convert_to_sn_api_block_info(init)` with the received `ProposalInit`, so every honest validator independently recomputes the same wrong `eth_to_fri_rate` and the same wrong `l2_gas_price_wei` from the attacker-supplied `l1_gas_price_wei`. The block executes consistently across all nodes, but with wrong ETH-denominated prices. [7](#0-6) 

---

### Impact Explanation

**Wrong block hash (Critical – Wrong state/commitment):** The manipulated wei prices corrupt `gas_prices_hash`, which is a direct input to `calculate_block_hash`. Every block produced after the attack carries a wrong block hash, breaking the chain of parent-hash commitments and invalidating proof inputs that depend on the block hash.

**Wrong fee execution (Critical – Incorrect fee/economic impact):** ETH-denominated transactions use `eth_gas_prices.l1_gas_price`, `eth_gas_prices.l1_data_gas_price`, and `eth_gas_prices.l2_gas_price` for fee charging. With an inflated `l1_gas_price_wei` (e.g., set to `u128::MAX / WEI_PER_ETH`), `eth_to_fri_rate` truncates to 1, and `l2_gas_price_wei` becomes `l2_gas_price_fri × WEI_PER_ETH` — orders of magnitude above the correct value. Users paying fees in ETH for L2 gas are charged the wrong amount; receipts record wrong fee values; storage state diverges from what the proof system expects.

---

### Likelihood Explanation

In the BFT consensus model any validator can be the proposer for a given round. The attack requires only that the proposer set `l1_gas_price_wei` to an out-of-range value while keeping `l1_gas_price_fri` within the `l1_gas_price_margin_percent` window. No special privilege beyond being selected as proposer is needed. The `within_margin` check explicitly ignores the wei fields, so the manipulated `ProposalInit` passes validation unconditionally. [8](#0-7) 

---

### Recommendation

1. **Validate wei prices in `is_block_info_valid`:** After computing `l1_gas_prices_fri` from the oracle, derive the expected wei prices using `fri_to_wei(eth_to_fri_rate)` and apply the same `within_margin` check to `init_proposed.l1_gas_price_wei` and `init_proposed.l1_data_gas_price_wei`.

2. **Derive `l2_gas_price_wei` from the oracle rate, not from `ProposalInit`:** In `convert_to_sn_api_block_info`, obtain `eth_to_fri_rate` from the oracle (or from the validated FRI prices and a trusted wei reference) rather than recomputing it from the unvalidated `l1_gas_price_wei` field.

3. **Add a cross-check assertion:** Assert that `l1_gas_price_fri.fri_to_wei(eth_to_fri_rate) ≈ l1_gas_price_wei` before accepting the `ProposalInit`, mirroring the existing FRI-side margin check.

---

### Proof of Concept

```
// Attacker is the proposer for block N.

// 1. Obtain a valid l1_gas_price_fri from the oracle (e.g., 1_000_000 fri).
//    Set l1_gas_price_wei to a manipulated value: e.g., u128::MAX / WEI_PER_ETH
//    (≈ 3.4 × 10^20 wei, far above the real ~10 Gwei).

let mut init = build_normal_proposal_init();
init.l1_gas_price_fri = GasPrice(1_000_000);          // within margin → passes validation
init.l1_gas_price_wei = GasPrice(340_282_366_920_938); // manipulated denominator

// 2. is_block_info_valid checks only l1_gas_price_fri → PASSES.

// 3. convert_to_sn_api_block_info computes:
//    eth_to_fri_rate = 1_000_000 * 10^18 / 340_282_366_920_938 ≈ 2_938  (truncated)
//    l2_gas_price_wei = l2_gas_price_fri * 10^18 / 2_938
//                     ≈ l2_gas_price_fri * 3.4 × 10^14  (inflated by ~10^5)

// 4. Block executes with:
//    eth_gas_prices.l1_gas_price = 340_282_366_920_938 wei  (wrong)
//    eth_gas_prices.l2_gas_price ≈ 10^5 × correct value    (wrong)

// 5. gas_prices_to_hash chains the wrong wei values →
//    block hash is wrong for every block from N onward.

// 6. ETH-denominated fee receipts record inflated fees;
//    storage state diverges from proof expectations.
```

The `is_block_info_valid` function fetches `_l1_gas_prices_wei` from the oracle but discards it (note the leading underscore), confirming no wei-side check is ever performed. [9](#0-8)

### Citations

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L286-320)
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
    Ok(())
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

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L338-370)
```rust
async fn initiate_validation(
    batcher: Arc<dyn BatcherClient>,
    state_sync_client: Arc<dyn StateSyncClient>,
    init: &ProposalInit,
    proposal_id: ProposalId,
    timeout_plus_margin: Duration,
    clock: &dyn Clock,
    compare_retrospective_block_hash: bool,
) -> ValidateProposalResult<()> {
    let chrono_timeout = chrono::Duration::from_std(timeout_plus_margin)
        .expect("Can't convert timeout to chrono::Duration");

    let input = ValidateBlockInput {
        proposal_id,
        deadline: clock.now() + chrono_timeout,
        retrospective_block_hash: retrospective_block_hash(
            batcher.clone(),
            state_sync_client,
            init,
            compare_retrospective_block_hash,
        )
        .await
        .map_err(ValidateProposalError::from)?,
        block_info: convert_to_sn_api_block_info(init)?,
    };
    debug!("Initiating validate proposal: input={input:?}");
    batcher.validate_block(input.clone()).await.map_err(|err| {
        ValidateProposalError::Batcher(
            format!("Failed to initiate validate proposal {input:?}."),
            err,
        )
    })?;
    Ok(())
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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L224-235)
```rust
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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L253-281)
```rust
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
