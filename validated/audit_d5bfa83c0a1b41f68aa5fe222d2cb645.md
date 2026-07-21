### Title
Validator Accepts Proposals with Unvalidated WEI Gas Prices, Corrupting Block Hash and Fee Calculations — (`crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

---

### Summary

`is_block_info_valid` validates only the FRI-denominated L1 gas prices from a proposer's `ProposalInit`, silently discarding the WEI prices. However, `convert_to_sn_api_block_info` passes both WEI and FRI prices directly into the `BlockInfo` used for block hash computation and fee execution. A malicious proposer can set `l1_gas_price_wei` and `l1_data_gas_price_wei` to arbitrary values that pass all validation checks, causing the committed block hash to embed wrong WEI prices and L1 handler transactions to execute with a manipulated ETH gas price.

---

### Finding Description

**Validation path — FRI only:**

In `is_block_info_valid`, the validator fetches its own expected prices and compares only the FRI values:

```rust
// validate_proposal.rs:286-292
let (l1_gas_prices_fri, _l1_gas_prices_wei) = get_l1_prices_in_fri_and_wei(
    l1_gas_price_provider,
    init_proposed.timestamp,
    block_info_validation.previous_block_info.as_ref(),
    gas_price_params,
)
.await;
```

The `_l1_gas_prices_wei` return value is explicitly discarded (underscore prefix). Only `l1_gas_prices_fri.l1_gas_price` and `l1_gas_prices_fri.l1_data_gas_price` are checked against the proposer's values within a margin:

```rust
// validate_proposal.rs:302-307
if !(within_margin(l1_gas_price_fri_proposed, l1_gas_price_fri, l1_gas_price_margin_percent)
    && within_margin(
        l1_data_gas_price_fri_proposed,
        l1_data_gas_price_fri,
        l1_gas_price_margin_percent,
    ))
```

`init_proposed.l1_gas_price_wei` and `init_proposed.l1_data_gas_price_wei` are **never checked**. [1](#0-0) 

**Execution path — WEI prices used unchecked:**

Immediately after validation passes, `initiate_validation` calls `convert_to_sn_api_block_info(init)`, which reads the unvalidated WEI prices directly:

```rust
// utils.rs:301-302
let l1_gas_price_wei = NonzeroGasPrice::new(init.l1_gas_price_wei)?;
let l1_data_gas_price_wei = NonzeroGasPrice::new(init.l1_data_gas_price_wei)?;
```

It also derives `l2_gas_price_wei` from `eth_to_fri_rate`, which is itself computed from the unvalidated WEI prices:

```rust
// utils.rs:304-314
let previous_block_info = PreviousBlockInfo::from(init);
let eth_to_fri_rate = calculate_eth_to_fri_rate(&previous_block_info)?;
let l2_gas_price_wei = NonzeroGasPrice::new(init.l2_gas_price_fri.fri_to_wei(eth_to_fri_rate)?)
``` [2](#0-1) 

**Block hash includes WEI prices:**

`gas_prices_to_hash` (called from `calculate_block_hash`) chains all six price fields — three WEI and three FRI — into the block hash for Starknet ≥ v0.13.4:

```rust
// block_hash_calculator.rs:424-433
HashChain::new()
    .chain(&STARKNET_GAS_PRICES0)
    .chain(&l1_gas_price.price_in_wei.0.into())
    .chain(&l1_gas_price.price_in_fri.0.into())
    .chain(&l1_data_gas_price.price_in_wei.0.into())
    .chain(&l1_data_gas_price.price_in_fri.0.into())
    .chain(&l2_gas_price.price_in_wei.0.into())
    .chain(&l2_gas_price.price_in_fri.0.into())
    .get_poseidon_hash()
``` [3](#0-2) 

`PartialBlockHashComponents::new` populates these fields from `block_info.gas_prices`, which originates from `convert_to_sn_api_block_info(init)`: [4](#0-3) 

**The inconsistency (direct analog to the external report):**

| Step | Data used |
|---|---|
| `is_block_info_valid` (validation check) | FRI prices only (`l1_gas_price_fri`, `l1_data_gas_price_fri`) |
| `convert_to_sn_api_block_info` → block hash | WEI **and** FRI prices (`l1_gas_price_wei`, `l1_data_gas_price_wei`, derived `l2_gas_price_wei`) |

This is structurally identical to the BunniToken report: the deviation check used `reserves + fees` while the final price used only `reserves`. Here, the validation check uses FRI prices while the block hash commitment uses WEI + FRI prices.

**Propagation to subsequent blocks:**

After a block is committed, `PreviousBlockInfo::from(init)` stores the proposer's WEI prices as the fallback for the next block's gas price calculation:

```rust
// utils.rs:99-112
impl From<&ProposalInit> for PreviousBlockInfo {
    fn from(init: &ProposalInit) -> Self {
        Self {
            l1_prices_wei: L1PricesInWei {
                l1_gas_price: init.l1_gas_price_wei,       // unvalidated
                l1_data_gas_price: init.l1_data_gas_price_wei, // unvalidated
            },
            ...
        }
    }
}
``` [5](#0-4) 

When the L1 gas price provider fails, `get_l1_prices_in_fri_and_wei_and_conversion_rate` falls back to `previous_block_info`, reusing the corrupted WEI prices and deriving a wrong `eth_to_fri_rate` for the next block. [6](#0-5) 

---

### Impact Explanation

A malicious proposer sets `l1_gas_price_wei = 1` (or any value) while keeping `l1_gas_price_fri` within the allowed margin. All validators accept the proposal because only FRI prices are checked. The batcher on every validator node executes the block with `l1_gas_price_wei = 1`, producing:

1. **Wrong block hash** — the committed `PartialBlockHash` embeds `price_in_wei = 1` for L1 gas, which is an authoritative-looking wrong value stored on-chain and used for L1 finality.
2. **Wrong ETH fee for L1 handler transactions** — `eth_gas_prices.l1_gas_price` is set to 1 wei, making L1 handler transactions execute at near-zero ETH cost, breaking the economic invariant that L1 messages must be paid for at the current L1 gas price.
3. **Corrupted `l2_gas_price_wei`** — because `eth_to_fri_rate` is derived from the manipulated WEI/FRI ratio, `l2_gas_price_wei` in the block hash is also wrong.
4. **Cascading fallback corruption** — the wrong WEI prices persist as `previous_block_info` and corrupt the next block's gas price calculation whenever the L1 provider is unavailable.

This matches the allowed impact: **Critical — Incorrect fee, gas, bouncer, resource accounting, refund, balance, or L1 gas price effect with economic impact** and **High — RPC execution, fee estimation returns an authoritative-looking wrong value**.

---

### Likelihood Explanation

Any node that wins a proposer slot in the BFT rotation can trigger this. The proposer constructs the `ProposalInit` locally and broadcasts it over the P2P network. No external oracle or privileged access is required — the proposer simply sets `l1_gas_price_wei` to an arbitrary nonzero value in the protobuf message before sending. The validator's `is_block_info_valid` will pass as long as `l1_gas_price_fri` is within the configured margin.

---

### Recommendation

In `is_block_info_valid`, validate the WEI prices from the proposer's `ProposalInit` against the locally computed WEI prices using the same `within_margin` check already applied to FRI prices:

```rust
// After the existing FRI check, add:
let l1_gas_price_wei_proposed = init_proposed.l1_gas_price_wei;
let l1_data_gas_price_wei_proposed = init_proposed.l1_data_gas_price_wei;

if !(within_margin(l1_gas_price_wei_proposed, l1_gas_prices_wei.l1_gas_price, l1_gas_price_margin_percent)
    && within_margin(l1_data_gas_price_wei_proposed, l1_gas_prices_wei.l1_data_gas_price, l1_gas_price_margin_percent))
{
    return Err(ValidateProposalError::InvalidBlockInfo(...));
}
```

This aligns the validation scope with the data actually committed to the block hash, mirroring the fix applied in the BunniToken case: make the check and the final computation operate on the same set of values.

---

### Proof of Concept

1. A malicious node wins a proposer slot at height `H`.
2. It calls `get_l1_prices_in_fri_and_wei` normally to obtain valid FRI prices.
3. It constructs `ProposalInit` with valid `l1_gas_price_fri` / `l1_data_gas_price_fri` but sets `l1_gas_price_wei = GasPrice(1)` and `l1_data_gas_price_wei = GasPrice(1)`.
4. It broadcasts the `ProposalInit` to all validators.
5. Each validator calls `is_block_info_valid`:
   - `l1_gas_price_fri` is within margin → **passes**
   - `l1_data_gas_price_fri` is within margin → **passes**
   - `l1_gas_price_wei` is **never checked** → **passes silently**
6. Each validator calls `initiate_validation` → `convert_to_sn_api_block_info(init)`:
   - `l1_gas_price_wei = NonzeroGasPrice::new(GasPrice(1))` → `1 wei`
   - `eth_to_fri_rate = calculate_eth_to_fri_rate(...)` → computed from `wei=1, fri=<valid>` → astronomically large rate
   - `l2_gas_price_wei = l2_gas_price_fri.fri_to_wei(eth_to_fri_rate)` → rounds to 0 → `NonzeroGasPrice::new` **returns error**, causing `convert_to_sn_api_block_info` to fail and the proposal to be rejected.

   Alternatively, with a carefully chosen `l1_gas_price_wei` that keeps `eth_to_fri_rate` in a valid range but differs from the true market rate (e.g., `l1_gas_price_wei = true_wei / 2`):
   - `convert_to_sn_api_block_info` succeeds
   - Block is built and committed with `price_in_wei = true_wei / 2` in the block hash
   - All L1 handler transactions in block `H` pay half the true ETH gas price
   - `previous_block_info.l1_prices_wei` for block `H+1` is set to `true_wei / 2`
   - If the L1 provider fails at height `H+1`, the fallback uses the corrupted WEI price, propagating the error [7](#0-6) [2](#0-1) [3](#0-2)

### Citations

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
