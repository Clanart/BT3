### Title
Unvalidated `l1_gas_price_wei` in `ProposalInit` Allows Malicious Proposer to Corrupt `l2_gas_price_wei` in Block Hash Commitment and ETH-Denominated Fee Calculations - (File: `crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

---

### Summary

`is_block_info_valid` validates `l1_gas_price_fri` and `l1_data_gas_price_fri` against the oracle but silently discards the computed `_l1_gas_prices_wei` without any cross-check. A malicious proposer can freely set `l1_gas_price_wei` to an arbitrary value. `convert_to_sn_api_block_info` then derives `eth_to_fri_rate` from the ratio of the unvalidated `l1_gas_price_wei` to the validated `l1_gas_price_fri`, and uses that stale/wrong rate to derive `l2_gas_price_wei`. Both `l1_gas_price_wei` and the corrupted `l2_gas_price_wei` are chained into the block hash via `gas_prices_to_hash`, and `l2_gas_price_wei` is also used for ETH-denominated L2 gas fee calculations in the blockifier. Because both proposer and validator derive `l2_gas_price_wei` from the same `ProposalInit`, they agree on the wrong commitment, and the block is accepted and stored with a corrupted block hash.

---

### Finding Description

**Analog mapping.** The external report describes: *prime rate updated by oracle → sub-rates (base, optimal, max) not recalculated → stale derived values used in borrow-rate formula*. The sequencer analog is: *`l1_gas_price_fri` validated against oracle → `l1_gas_price_wei` not validated → wrong `eth_to_fri_rate` derived → wrong `l2_gas_price_wei` propagated into block hash and fee engine*.

**Root cause — missing wei validation.**

`is_block_info_valid` fetches the oracle-computed wei prices but throws them away:

```rust
let (l1_gas_prices_fri, _l1_gas_prices_wei) = get_l1_prices_in_fri_and_wei(...)
    .await;
// Only fri prices are range-checked; _l1_gas_prices_wei is never compared.
if !(within_margin(l1_gas_price_fri_proposed, l1_gas_price_fri, ...)
    && within_margin(l1_data_gas_price_fri_proposed, l1_data_gas_price_fri, ...))
``` [1](#0-0) 

`l1_gas_price_wei` and `l1_data_gas_price_wei` from the proposer's `ProposalInit` are never compared against the oracle.

**Stale-rate derivation — the analog to "sub-rates not recalculated".**

`convert_to_sn_api_block_info` derives `eth_to_fri_rate` from the proposer-supplied (unvalidated) `l1_gas_price_wei`:

```rust
let previous_block_info = PreviousBlockInfo::from(init); // uses init.l1_gas_price_wei
let eth_to_fri_rate = calculate_eth_to_fri_rate(&previous_block_info)?;
// eth_to_fri_rate = l1_gas_price_fri * WEI_PER_ETH / l1_gas_price_wei

let l2_gas_price_wei =
    NonzeroGasPrice::new(init.l2_gas_price_fri.fri_to_wei(eth_to_fri_rate)?)?;
``` [2](#0-1) 

`calculate_eth_to_fri_rate` computes `l1_gas_price_fri * WEI_PER_ETH / l1_gas_price_wei`. If `l1_gas_price_wei` is doubled, `eth_to_fri_rate` is halved, and `l2_gas_price_wei` is doubled. [3](#0-2) 

**Propagation into block hash.**

`PartialBlockHashComponents` is built from the corrupted `BlockInfo`:

```rust
l2_gas_price: block_info.gas_prices.l2_gas_price_per_token(),
// price_in_wei = eth_gas_prices.l2_gas_price  ← corrupted
``` [4](#0-3) 

`gas_prices_to_hash` (for Starknet ≥ 0.13.3) chains all six price fields — including `l2_gas_price.price_in_wei` — into a single Poseidon hash that enters `calculate_block_hash`: [5](#0-4) 

`BlockExecutionArtifacts::new` calls `PartialBlockHashComponents::new` with the corrupted `BlockInfo`, so the `PartialBlockHash` / `ProposalCommitment` is wrong from the moment the block is built: [6](#0-5) 

**Why both sides agree on the wrong value.**

The validator calls `convert_to_sn_api_block_info(init)` with the same `ProposalInit` the proposer sent, so both derive the same corrupted `l2_gas_price_wei`. The `ProposalFinMismatch` check therefore passes, and the block is committed with the wrong block hash. [7](#0-6) 

---

### Impact Explanation

- **Wrong block hash commitment.** `l1_gas_price_wei` (directly) and `l2_gas_price_wei` (derived) are both chained into the Poseidon block hash. A manipulated `l1_gas_price_wei` corrupts both fields, producing a `PartialBlockHash` / `ProposalCommitment` that diverges from what an honest node would compute from oracle data. This is a wrong commitment accepted through normal consensus flow.
- **Wrong ETH-denominated L2 gas fee.** `BlockInfo.gas_prices.eth_gas_prices.l2_gas_price` is set to the corrupted `l2_gas_price_wei`. Every transaction in the block that pays fees in ETH has its L2 gas cost computed against this wrong price, causing incorrect fee charges — an economic impact on users.
- **Scope match.** Fits "Critical. Wrong state … or revert result from blockifier/syscall/execution logic for accepted input" (wrong fee) and "Critical. Incorrect fee, gas … with economic impact."

---

### Likelihood Explanation

Any consensus participant that becomes a proposer can trigger this. No special privilege beyond being a validator/proposer is required. The manipulation is a single-field change in `ProposalInit` (`l1_gas_price_wei`) that passes all existing checks. Likelihood is **Medium** — requires a malicious proposer, but the attack surface is open every round.

---

### Recommendation

In `is_block_info_valid`, validate the proposed wei prices against the oracle-computed wei prices with the same margin check already applied to fri prices:

```diff
- let (l1_gas_prices_fri, _l1_gas_prices_wei) = get_l1_prices_in_fri_and_wei(...).await;
+ let (l1_gas_prices_fri, l1_gas_prices_wei) = get_l1_prices_in_fri_and_wei(...).await;

  if !(within_margin(l1_gas_price_fri_proposed, l1_gas_prices_fri.l1_gas_price, margin)
      && within_margin(l1_data_gas_price_fri_proposed, l1_gas_prices_fri.l1_data_gas_price, margin)
+     && within_margin(init_proposed.l1_gas_price_wei, l1_gas_prices_wei.l1_gas_price, margin)
+     && within_margin(init_proposed.l1_data_gas_price_wei, l1_gas_prices_wei.l1_data_gas_price, margin))
```

This ensures that `eth_to_fri_rate` — and therefore `l2_gas_price_wei` — is derived from a wei price that is consistent with the oracle, closing the analog to the "sub-rates not recalculated" bug in the external report.

---

### Proof of Concept

1. Malicious proposer constructs `ProposalInit` with:
   - `l1_gas_price_fri` = oracle value (passes `within_margin` check)
   - `l1_gas_price_wei` = `2 × oracle_l1_gas_price_wei` (never checked)
2. Validator calls `is_block_info_valid` → passes (only fri prices are range-checked; `_l1_gas_prices_wei` is discarded).
3. Validator calls `convert_to_sn_api_block_info(init)`:
   - `eth_to_fri_rate = l1_gas_price_fri × WEI_PER_ETH / (2 × oracle_wei) = oracle_rate / 2`
   - `l2_gas_price_wei = l2_gas_price_fri × WEI_PER_ETH / (oracle_rate / 2) = 2 × correct_l2_gas_price_wei`
4. Batcher executes block with `BlockInfo` containing `l2_gas_price_wei = 2 × correct`. All ETH-denominated L2 gas fees are doubled.
5. `PartialBlockHashComponents.l2_gas_price.price_in_wei = 2 × correct` → `gas_prices_to_hash` produces wrong Poseidon hash → wrong `PartialBlockHash` → wrong `ProposalCommitment`.
6. Both proposer and validator computed the same wrong commitment → `ProposalFinMismatch` check passes → block committed with corrupted block hash.

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

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L355-370)
```rust
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

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L303-314)
```rust
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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L423-434)
```rust
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
```

**File:** crates/apollo_batcher/src/block_builder.rs (L160-182)
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
        let l2_gas_used = execution_data.l2_gas_used();
        Self {
            execution_data,
            commitment_state_diff,
            compressed_state_diff,
            bouncer_weights,
            l2_gas_used,
            casm_hash_computation_data_sierra_gas,
            casm_hash_computation_data_proving_gas,
            compiled_class_hashes_for_migration,
            final_n_executed_txs,
            partial_block_hash_components,
        }
```
