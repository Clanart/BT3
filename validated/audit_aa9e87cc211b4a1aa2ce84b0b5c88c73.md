### Title
Unvalidated `l1_gas_price_wei` in `ProposalInit` Enables Wrong `l2_gas_price_wei` Derivation and Corrupted Block Hash Gas Price Commitment — (`crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

---

### Summary

`is_block_info_valid` validates only the FRI-denominated L1 gas prices in a received `ProposalInit`, but never validates the WEI-denominated prices (`l1_gas_price_wei`, `l1_data_gas_price_wei`). Because `l2_gas_price_wei` is not carried in `ProposalInit` at all and is instead *derived* from `l2_gas_price_fri` using an `eth_to_fri_rate` that is itself computed from the unvalidated `l1_gas_price_wei`, a malicious proposer can inject an arbitrary WEI price and cause every downstream consumer — fee execution, block hash commitment, and RPC estimation — to operate on a wrong gas price scale.

---

### Finding Description

**Root cause — missing WEI price validation**

`is_block_info_valid` in `validate_proposal.rs` checks:
- `l2_gas_price_fri` (exact equality)
- `l1_gas_price_fri` and `l1_data_gas_price_fri` (within a percentage margin)

It explicitly discards the locally-computed WEI prices:

```rust
let (l1_gas_prices_fri, _l1_gas_prices_wei) = get_l1_prices_in_fri_and_wei(...).await;
``` [1](#0-0) 

The `l1_gas_price_wei` and `l1_data_gas_price_wei` fields of the incoming `ProposalInit` are never compared against the locally-computed WEI prices.

**Propagation — wrong `eth_to_fri_rate` and derived `l2_gas_price_wei`**

`convert_to_sn_api_block_info` (called immediately after validation passes) derives the `eth_to_fri_rate` from the *proposer-supplied* `l1_gas_price_fri` and `l1_gas_price_wei`:

```rust
let eth_to_fri_rate = calculate_eth_to_fri_rate(&previous_block_info)?;
// = l1_gas_price_fri * WEI_PER_ETH / l1_gas_price_wei
```

It then derives `l2_gas_price_wei` from that rate:

```rust
let l2_gas_price_wei = NonzeroGasPrice::new(
    init.l2_gas_price_fri.fri_to_wei(eth_to_fri_rate)?
)?;
``` [2](#0-1) 

Substituting: `l2_gas_price_wei = l2_gas_price_fri × l1_gas_price_wei / l1_gas_price_fri`. If `l1_gas_price_wei` is manipulated (e.g., set to 1 instead of 10⁹), `l2_gas_price_wei` is off by the same factor.

**Block hash corruption**

For Starknet ≥ V0_13_4, `gas_prices_to_hash` hashes all six gas price fields — including `l1_gas_price.price_in_wei` (taken directly from the proposer-supplied value) and `l2_gas_price.price_in_wei` (the wrongly derived value):

```rust
HashChain::new()
    .chain(&l1_gas_price.price_in_wei.0.into())
    .chain(&l1_gas_price.price_in_fri.0.into())
    ...
    .chain(&l2_gas_price.price_in_wei.0.into())
    .chain(&l2_gas_price.price_in_fri.0.into())
    .get_poseidon_hash()
``` [3](#0-2) 

Both proposer and validator call `convert_to_sn_api_block_info` with the same `ProposalInit`, so they agree on the wrong hash. The SNOS receives the wrong values via hints and produces a proof that verifies against the wrong commitment.

**Fee execution corruption**

The `BlockInfo` passed to the batcher carries `eth_gas_prices.l2_gas_price = l2_gas_price_wei`. This is used directly in `get_fee_by_gas_vector` for `FeeType::Eth` transactions: [4](#0-3) 

A manipulated `l2_gas_price_wei` causes every ETH-denominated L2 gas fee in the block to be computed at the wrong scale.

---

### Impact Explanation

| Corrupted value | Effect |
|---|---|
| `l1_gas_price_wei` in block hash | Block hash commits to a wrong L1 gas price in WEI |
| `l2_gas_price_wei` in block hash | Block hash commits to a wrong L2 gas price in WEI |
| `eth_gas_prices.l2_gas_price` in `BlockInfo` | All ETH-denominated L2 gas fees in the block are computed at the wrong scale |
| `starknet_estimateFee` / `starknet_simulateTransactions` | RPC returns wrong fee estimates for ETH-paying transactions |

This matches:
- **Critical**: Incorrect fee/gas/resource accounting with economic impact
- **Critical**: Wrong state/receipt/revert result from blockifier execution logic
- **High**: RPC fee estimation returns an authoritative-looking wrong value

---

### Likelihood Explanation

Any consensus validator that becomes a proposer (a normal, rotating role in Tendermint-based consensus) can trigger this without any special privilege. The `ProposalInit` is a P2P message; the WEI fields are accepted verbatim. No existing check in `is_block_info_valid` or `initiate_validation` catches an out-of-range `l1_gas_price_wei`. [5](#0-4) 

---

### Recommendation

In `is_block_info_valid`, after computing `(l1_gas_prices_fri, l1_gas_prices_wei)`, validate the WEI prices from the `ProposalInit` against the locally-computed WEI prices using the same `within_margin` check already applied to FRI prices:

```rust
let (l1_gas_prices_fri, l1_gas_prices_wei) = get_l1_prices_in_fri_and_wei(...).await;
// existing FRI checks ...
// add:
if !(within_margin(init_proposed.l1_gas_price_wei, l1_gas_prices_wei.l1_gas_price, margin)
    && within_margin(init_proposed.l1_data_gas_price_wei, l1_gas_prices_wei.l1_data_gas_price, margin))
{
    return Err(ValidateProposalError::InvalidBlockInfo(...));
}
```

This ensures the WEI prices used to derive `eth_to_fri_rate` and `l2_gas_price_wei` are consistent with the validator's own L1 price observation, preventing scale manipulation.

---

### Proof of Concept

1. Validator A is selected as proposer for block N.
2. A constructs a `ProposalInit` with:
   - `l1_gas_price_fri = X` (valid, within margin of the real price)
   - `l1_gas_price_wei = 1` (manipulated; real value is ~10⁹)
3. Validator B receives the `ProposalInit` and calls `is_block_info_valid`:
   - FRI price check passes (X is within margin).
   - WEI price is never checked. Validation returns `Ok(())`.
4. B calls `convert_to_sn_api_block_info(init)`:
   - `eth_to_fri_rate = X * 10^18 / 1 = X * 10^18` (should be ~X * 10^9)
   - `l2_gas_price_wei = l2_gas_price_fri * 10^18 / (X * 10^18) = l2_gas_price_fri / X` (should be ~`l2_gas_price_fri / X * 10^9`)
   - `l2_gas_price_wei` is ~10⁹× too small.
5. Batcher executes the block with this `BlockInfo`. All ETH-denominated L2 gas fees are ~10⁹× undercharged.
6. `gas_prices_to_hash` hashes `l1_gas_price_wei = 1` and the wrong `l2_gas_price_wei` into the block hash.
7. Both proposer and validator agree on this wrong block hash; the block is committed. [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L304-314)
```rust
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

**File:** crates/starknet_api/src/block.rs (L424-452)
```rust
    pub fn wei_to_fri(self, eth_to_fri_rate: u128) -> Result<GasPrice, StarknetApiError> {
        // We use integer division since wei * eth_to_fri_rate is expected to be high enough to not
        // cause too much precision loss.
        Ok(self
            .checked_mul_u128(eth_to_fri_rate)
            .ok_or_else(|| {
                StarknetApiError::GasPriceConversionError(format!(
                    "Gas price is too high: {:?}, eth to fri rate: {:?}",
                    self, eth_to_fri_rate
                ))
            })?
            .checked_div(WEI_PER_ETH)
            .expect("WEI_PER_ETH must be non-zero"))
    }
    pub fn fri_to_wei(self, eth_to_fri_rate: u128) -> Result<GasPrice, StarknetApiError> {
        self.checked_mul_u128(WEI_PER_ETH)
            .ok_or_else(|| {
                StarknetApiError::GasPriceConversionError(format!(
                    "Gas price is too high: {:?}, eth to fri rate: {:?}",
                    self, eth_to_fri_rate
                ))
            })?
            .checked_div(eth_to_fri_rate)
            .ok_or_else(|| {
                StarknetApiError::GasPriceConversionError(
                    "FRI to ETH rate must be non-zero".to_string(),
                )
            })
    }
```
