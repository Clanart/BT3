### Title
Missing `l1_gas_price_wei` / `l1_data_gas_price_wei` Validation in `is_block_info_valid` Allows Proposer to Inject Arbitrary Wei Prices into Block Hash — (`File: crates/apollo_consensus_orchestrator/src/validate_proposal.rs`)

---

### Summary

`is_block_info_valid` validates the FRI-denominated L1 gas prices from a received `ProposalInit` but silently discards the computed expected WEI prices without comparing them to the proposer-supplied values. Because `l1_gas_price_wei` and `l1_data_gas_price_wei` flow directly into `convert_to_sn_api_block_info` → `BlockInfo` → `PartialBlockHashComponents` → `calculate_block_hash`, a malicious proposer can set arbitrary WEI prices that validators will accept, causing every validator to execute and commit a block whose block hash encodes the attacker-chosen WEI prices.

---

### Finding Description

**Analog to the external report.** The external bug: `authorize` checks `discriminator` and `mint_recipient` but not `destination_domain`, so an attacker can route funds to an unintended destination. The sequencer analog: `is_block_info_valid` checks `l1_gas_price_fri` and `l1_data_gas_price_fri` (within a margin) but not `l1_gas_price_wei` and `l1_data_gas_price_wei`, so a proposer can inject arbitrary WEI prices that flow into the block hash.

**Root cause — `is_block_info_valid`:**

```rust
// crates/apollo_consensus_orchestrator/src/validate_proposal.rs  lines 286-319
let (l1_gas_prices_fri, _l1_gas_prices_wei) = get_l1_prices_in_fri_and_wei(   // ← wei computed …
    l1_gas_price_provider,
    init_proposed.timestamp,
    block_info_validation.previous_block_info.as_ref(),
    gas_price_params,
)
.await;
// … but _l1_gas_prices_wei is NEVER compared to init_proposed.l1_gas_price_wei
//                                                  or init_proposed.l1_data_gas_price_wei
if !(within_margin(l1_gas_price_fri_proposed, l1_gas_price_fri, …)
    && within_margin(l1_data_gas_price_fri_proposed, l1_data_gas_price_fri, …))
{
    return Err(…);   // only FRI prices are gated
}
Ok(())
``` [1](#0-0) 

The validator then passes the unvalidated `init` directly to `convert_to_sn_api_block_info`:

```rust
// validate_proposal.rs  line 361
block_info: convert_to_sn_api_block_info(init)?,
``` [2](#0-1) 

`convert_to_sn_api_block_info` copies the proposer-supplied WEI prices verbatim into `BlockInfo`:

```rust
// crates/apollo_consensus_orchestrator/src/utils.rs  lines 301-302
let l1_gas_price_wei = NonzeroGasPrice::new(init.l1_gas_price_wei)?;
let l1_data_gas_price_wei = NonzeroGasPrice::new(init.l1_data_gas_price_wei)?;
``` [3](#0-2) 

`PartialBlockHashComponents::new` then reads both WEI and FRI prices from `BlockInfo`:

```rust
// crates/starknet_api/src/block_hash/block_hash_calculator.rs  lines 228-229
l1_gas_price: block_info.gas_prices.l1_gas_price_per_token(),
l1_data_gas_price: block_info.gas_prices.l1_data_gas_price_per_token(),
``` [4](#0-3) 

`calculate_block_hash` chains the full `GasPricePerToken` (both WEI and FRI) into the Poseidon hash:

```rust
// block_hash_calculator.rs  lines 265-273
.chain_iter(
    gas_prices_to_hash(
        &partial_block_hash_components.l1_gas_price,
        &partial_block_hash_components.l1_data_gas_price,
        &partial_block_hash_components.l2_gas_price,
        &block_hash_version,
    )
    .iter(),
)
``` [5](#0-4) 

`ProposalInit` carries both price families over the P2P wire:

```rust
// crates/apollo_protobuf/src/consensus.rs  lines 118-120
pub l1_gas_price_wei: GasPrice,
pub l1_data_gas_price_wei: GasPrice,
``` [6](#0-5) 

---

### Impact Explanation

A Byzantine proposer broadcasts a `ProposalInit` whose `l1_gas_price_wei` / `l1_data_gas_price_wei` are set to any non-zero value (the only check in `convert_to_sn_api_block_info` is `NonzeroGasPrice::new`). Every honest validator runs `is_block_info_valid`, passes the FRI-price check, then hands the tampered `BlockInfo` to the batcher. The batcher executes the block and the committer derives `PartialBlockHashComponents` from that `BlockInfo`. The resulting `calculate_block_hash` output encodes the attacker-chosen WEI prices. This wrong block hash is then:

1. **Stored in the block hash contract** (a wrong authoritative storage value used by future `GetBlockHash` syscalls and proof-facts validation).
2. **Used as `previous_block_hash` for the next block**, propagating the error forward.
3. **Committed to `apollo_storage`** as the canonical block hash for that height.
4. **Used in ETH-denominated fee accounting** — `eth_gas_prices` in `BlockInfo` drives L1-handler fee checks and resource-bound validation, so fees charged to users are computed against the attacker-chosen WEI price.

Matches: *Critical — Wrong state/storage value from blockifier/syscall/execution logic for accepted input* and *Critical — Incorrect fee/gas/resource accounting with economic impact*.

---

### Likelihood Explanation

Any single Byzantine validator that is elected proposer can trigger this. The `ProposalInit` is a network message with no signature over its individual fields; the only gate is `is_block_info_valid`. The FRI prices must stay within the configured margin (default `l1_gas_price_margin_percent`), but the WEI prices are completely unconstrained beyond being non-zero. No privilege beyond being a consensus participant is required.

---

### Recommendation

In `is_block_info_valid`, compare the validator's locally computed WEI prices against the proposer-supplied values using the same `within_margin` check already applied to FRI prices. The expected WEI prices are already computed but discarded (`_l1_gas_prices_wei`); they should be retained and compared:

```rust
let (l1_gas_prices_fri, l1_gas_prices_wei) = get_l1_prices_in_fri_and_wei(...).await;

// existing FRI checks …

// ADD: WEI price checks
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

Additionally, `starknet_version` (also included in the block hash but absent from `is_block_info_valid`) should be validated against the locally known expected version.

---

### Proof of Concept

1. Attacker controls a proposer slot at height `H`.
2. Attacker constructs `ProposalInit` with valid FRI prices (within margin) but sets `l1_gas_price_wei = u128::MAX / 2` and `l1_data_gas_price_wei = u128::MAX / 2`.
3. Attacker broadcasts the proposal. Every honest validator calls `is_block_info_valid`:
   - FRI prices pass the `within_margin` check ✓
   - WEI prices are never checked ✓ (the `_l1_gas_prices_wei` binding is discarded)
4. `initiate_validation` calls `convert_to_sn_api_block_info(init)`, which sets `eth_gas_prices.l1_gas_price = NonzeroGasPrice(u128::MAX/2)`.
5. The batcher executes the block; `BlockExecutionArtifacts` builds `PartialBlockHashComponents` with the attacker's WEI prices.
6. `calculate_block_hash` produces a hash `H_wrong` that encodes `u128::MAX/2` as the WEI gas price.
7. `H_wrong` is stored in the block hash contract at key `H` and used as `previous_block_hash` for block `H+1`.
8. All future `GetBlockHash(H)` syscalls return `H_wrong`; any client-side proof referencing block `H` will fail to verify against the true chain state.

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

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L350-362)
```rust
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
```

**File:** crates/apollo_consensus_orchestrator/src/utils.rs (L287-333)
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

**File:** crates/apollo_protobuf/src/consensus.rs (L94-125)
```rust
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
}
```
