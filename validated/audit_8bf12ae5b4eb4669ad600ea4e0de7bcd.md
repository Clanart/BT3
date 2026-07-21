### Title
Unvalidated `l1_gas_price_wei` / `l1_data_gas_price_wei` in `ProposalInit` Allows Proposer to Commit a Wrong Block Hash — (`crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

---

### Summary

`is_block_info_valid` validates only the FRI-denominated L1 gas prices against the oracle, but silently discards the oracle-derived WEI prices and never checks the WEI prices carried in the incoming `ProposalInit`. Because `gas_prices_to_hash` (Starknet ≥ 0.13.4) hashes **all six** gas-price fields — three wei/fri pairs — into the block hash, a malicious proposer can inject arbitrary `l1_gas_price_wei` / `l1_data_gas_price_wei` values that pass every validation gate yet produce a permanently wrong `PartialBlockHash` / final block hash.

---

### Finding Description

**Step 1 — Validation gap in `is_block_info_valid`**

`is_block_info_valid` calls `get_l1_prices_in_fri_and_wei`, which returns both FRI and WEI prices, but immediately discards the WEI half:

```rust
let (l1_gas_prices_fri, _l1_gas_prices_wei) = get_l1_prices_in_fri_and_wei(…).await;
```

It then checks only the FRI prices within a percentage margin:

```rust
if !(within_margin(l1_gas_price_fri_proposed, l1_gas_price_fri, …)
    && within_margin(l1_data_gas_price_fri_proposed, l1_data_gas_price_fri, …))
{ return Err(InvalidBlockInfo(…)); }
```

`l1_gas_price_wei` and `l1_data_gas_price_wei` from the incoming `ProposalInit` are **never compared** against the oracle-derived WEI values. [1](#0-0) 

**Step 2 — WEI prices flow unmodified into the block hash**

`ProposalInit` carries both WEI fields:

```rust
pub l1_gas_price_wei: GasPrice,
pub l1_data_gas_price_wei: GasPrice,
``` [2](#0-1) 

`initiate_build` / `initiate_validation` both call `convert_to_sn_api_block_info(&init)`, which passes the raw `ProposalInit` WEI values into the `BlockInfo` handed to the batcher. The batcher then calls `PartialBlockHashComponents::new(&block_info, header_commitments)`, which copies `block_info.gas_prices.l1_gas_price_per_token()` (both wei and fri) into the commitment struct. [3](#0-2) [4](#0-3) 

**Step 3 — `gas_prices_to_hash` commits all six values**

For Starknet ≥ 0.13.4 (the current production version), `gas_prices_to_hash` hashes all three wei/fri pairs into a single Poseidon felt that enters the block hash:

```rust
HashChain::new()
    .chain(&STARKNET_GAS_PRICES0)
    .chain(&l1_gas_price.price_in_wei.0.into())   // ← attacker-controlled
    .chain(&l1_gas_price.price_in_fri.0.into())
    .chain(&l1_data_gas_price.price_in_wei.0.into()) // ← attacker-controlled
    .chain(&l1_data_gas_price.price_in_fri.0.into())
    .chain(&l2_gas_price.price_in_wei.0.into())
    .chain(&l2_gas_price.price_in_fri.0.into())
    .get_poseidon_hash()
``` [5](#0-4) 

**Step 4 — Both proposer and validator converge on the wrong hash**

Because the validator feeds the same `ProposalInit` WEI values into `validate_block`, the batcher on the validator side computes the identical (wrong) `PartialBlockHash`. The `ProposalFinMismatch` check therefore passes, and the wrong commitment is accepted and stored. [6](#0-5) 

---

### Impact Explanation

The `PartialBlockHash` (and, once the global root is available, the final `BlockHash`) is permanently committed to storage with WEI gas prices that were never verified against the L1 oracle. This breaks the block-hash commitment invariant: the hash no longer faithfully represents the actual L1 gas prices at the time of the block. Downstream effects include:

- **Wrong block hash stored and propagated** — every consumer of `starknet_getBlockWithTxHashes` / `starknet_getBlockWithReceipts` receives an authoritative-looking but incorrect hash.
- **Wrong fee accounting for ETH-denominated (L1-handler) transactions** — the `eth_gas_prices` vector inside `BlockInfo` is derived from the WEI fields; manipulating them changes the ETH fee charged to L1→L2 message senders.
- **Proof / SNOS input mismatch** — the SNOS `get_block_hashes` hint guesses `gas_prices_hash` and checks consistency; a committed hash built from wrong WEI prices will diverge from any independently computed proof input.

Matches: *Critical — Wrong state/receipt/commitment from blockifier/execution logic for accepted input* and *Critical — Incorrect fee/gas/resource accounting with economic impact*.

---

### Likelihood Explanation

Any consensus proposer node can craft a `ProposalInit` with arbitrary WEI prices while keeping FRI prices within the accepted margin. In a decentralized sequencer, every validator is eligible to propose. The attack requires no special privilege beyond being selected as the round proposer, and no on-chain transaction or key compromise is needed.

---

### Recommendation

In `is_block_info_valid`, compare the proposed WEI prices against the oracle-derived WEI prices (already computed but discarded via `_l1_gas_prices_wei`) using the same `within_margin` check applied to FRI prices:

```rust
let (l1_gas_prices_fri, l1_gas_prices_wei) = get_l1_prices_in_fri_and_wei(…).await;
// existing FRI checks …
if !(within_margin(init_proposed.l1_gas_price_wei,     l1_gas_prices_wei.l1_gas_price,     margin)
  && within_margin(init_proposed.l1_data_gas_price_wei, l1_gas_prices_wei.l1_data_gas_price, margin))
{
    return Err(ValidateProposalError::InvalidBlockInfo(…));
}
```

This closes the gap between what is validated and what is committed to the block hash. [7](#0-6) 

---

### Proof of Concept

1. A proposer node constructs a `ProposalInit` for height H with:
   - `l1_gas_price_fri` = oracle value (passes `within_margin`)
   - `l1_data_gas_price_fri` = oracle value (passes `within_margin`)
   - `l1_gas_price_wei` = `0` (or any value inconsistent with the FRI price and the real ETH/STRK rate)
   - `l1_data_gas_price_wei` = `0`

2. The proposer broadcasts this `ProposalInit` as the first `ProposalPart`.

3. Every validator calls `is_block_info_valid`:
   - Timestamp check: passes.
   - `l2_gas_price_fri` exact match: passes.
   - FRI margin check: passes (FRI prices are correct).
   - WEI prices: **not checked** (`_l1_gas_prices_wei` is discarded).
   - Result: `Ok(())`.

4. `initiate_validation` calls `convert_to_sn_api_block_info(&init)`, producing a `BlockInfo` with `eth_gas_prices.l1_gas_price = 0`.

5. The batcher executes the block and calls `BlockExecutionArtifacts::new`, which calls `calculate_block_commitments` and then `PartialBlockHashComponents::new(&block_info, …)`. The `l1_gas_price.price_in_wei` field is `0`.

6. `gas_prices_to_hash` hashes `0` for `l1_gas_price.price_in_wei`, producing a different `gas_prices_hash` than the oracle-correct value.

7. `calculate_block_hash` chains this wrong `gas_prices_hash` into the final block hash.

8. Both proposer and validator arrive at the same wrong `PartialBlockHash`; `ProposalFinMismatch` does not fire; the block is committed with a permanently incorrect block hash and zero ETH gas price for fee accounting. [8](#0-7) [9](#0-8)

### Citations

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L236-241)
```rust
    if built_block != received_fin.proposal_commitment {
        CONSENSUS_PROPOSAL_FIN_MISMATCH.increment(1);
        return Err(ValidateProposalError::ProposalFinMismatch);
    }

    Ok(built_block)
```

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

**File:** crates/apollo_protobuf/src/consensus.rs (L117-120)
```rust
    /// L1 gas price in WEI.
    pub l1_gas_price_wei: GasPrice,
    /// L1 data gas price in WEI.
    pub l1_data_gas_price_wei: GasPrice,
```

**File:** crates/apollo_batcher/src/block_builder.rs (L143-183)
```rust
    pub async fn new(
        BlockExecutionSummary {
            state_diff: commitment_state_diff,
            compressed_state_diff,
            bouncer_weights,
            casm_hash_computation_data_sierra_gas,
            casm_hash_computation_data_proving_gas,
            compiled_class_hashes_for_migration,
            block_info,
        }: BlockExecutionSummary,
        execution_data: BlockTransactionExecutionData,
        final_n_executed_txs: usize,
    ) -> Self {
        let l1_da_mode = L1DataAvailabilityMode::from_use_kzg_da(block_info.use_kzg_da);
        let transactions_data =
            prepare_txs_hashing_data(&execution_data.execution_infos_and_signatures);
        // TODO(Ayelet): Remove the clones.
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
    }
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
