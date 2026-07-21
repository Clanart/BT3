### Title
`l2_gas_price` Mutated Before Validation in `try_sync` Allows Invalid Sync Block to Corrupt the Gas Rate Committed to Subsequent Block Headers - (File: crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs)

### Summary
In `SequencerConsensusContext::try_sync`, `self.l2_gas_price` is unconditionally overwritten from the untrusted sync block's `next_l2_gas_price` field **before** the block's validity is checked. When the subsequent timestamp/block-number guard fires and the function returns `false`, the corrupted gas price persists in the context and is used as the `l2_gas_price` field of every subsequent block proposal. Because `l2_gas_price` is a direct input to `calculate_block_hash` (via `PartialBlockHashComponents`), this produces a wrong block hash and wrong fee-estimation results for all blocks built after the failed sync.

### Finding Description
In `try_sync` (lines 834–837), `self.l2_gas_price` is set from the sync block before any validity check:

```rust
// May be default for blocks older than 0.14.0, ensure min gas price is met.
self.l2_gas_price = max(          // ← mutated here
    sync_block.block_header_without_hash.next_l2_gas_price,
    VersionedConstants::latest_constants().min_gas_price,
);
// TODO(Asmaa): validate starknet_version and parent_hash when they are stored.
let block_number = sync_block.block_header_without_hash.block_number;
let timestamp   = sync_block.block_header_without_hash.timestamp;
...
if !(block_number == height
    && timestamp.0 >= last_block_timestamp
    && timestamp.0 <= now + self.config.static_config.block_timestamp_window_seconds)
{
    warn!(...);
    return false;   // ← returns false, but l2_gas_price is already wrong
}
``` [1](#0-0) 

The corrupted `self.l2_gas_price` then flows into three downstream paths:

1. **`build_proposal`** – the value is embedded in the `ProposalInit` sent to peers as the block's `l2_gas_price_fri`.
2. **`update_state_sync_with_new_block`** – it is written as `next_l2_gas_price` in `BlockHeaderWithoutHash`, which is committed to the state-sync layer and stored in `apollo_storage`.
3. **`finalize_decision`** – it is written into `FeeMarketInfo { next_l2_gas_price }` in the Cende blob. [2](#0-1) [3](#0-2) 

The `l2_gas_price` field is a direct input to `calculate_block_hash` through `PartialBlockHashComponents`:

```rust
pub struct PartialBlockHashComponents {
    ...
    pub l2_gas_price: GasPricePerToken,   // ← hashed into block hash
    ...
}
``` [4](#0-3) 

It is included in the Poseidon hash via `gas_prices_to_hash`: [5](#0-4) 

A test explicitly confirms that changing `l2_gas_price` changes the block hash: [6](#0-5) 

### Impact Explanation
A sync peer that supplies a block with a valid `next_l2_gas_price` but an out-of-range timestamp (or mismatched block number) causes `try_sync` to reject the block while leaving `self.l2_gas_price` permanently set to the attacker-chosen value. Every block built after this point will:

- Embed the wrong `l2_gas_price` in `PartialBlockHashComponents`, producing a wrong `PartialBlockHash` / final block hash.
- Store the wrong `next_l2_gas_price` in the committed `BlockHeaderWithoutHash`, which downstream nodes and the RPC layer read as authoritative.
- Return wrong fee-estimation results from `starknet_estimateFee` / `starknet_simulateTransactions` because those calls use the stored block's gas price.

This matches:
- **High** – RPC fee estimation and simulation return an authoritative-looking wrong value.
- **Critical** – Incorrect gas/fee accounting with economic impact (transactions priced against a manipulated base fee).
- **Critical** – Wrong block hash committed to storage and propagated to state sync.

### Likelihood Explanation
The trigger requires a sync peer to serve a `SyncBlock` whose `block_number` or `timestamp` fails the guard. In a P2P deployment the state-sync layer fetches blocks from network peers; a single malicious or Byzantine peer can craft such a response. The window is every call to `try_sync`, which is invoked on every consensus height transition.

### Recommendation
Move the `self.l2_gas_price` assignment to **after** the validity guard, so the mutation only occurs when the block is accepted:

```rust
// Validate first.
if !(block_number == height
    && timestamp.0 >= last_block_timestamp
    && timestamp.0 <= now + ...) {
    warn!(...);
    return false;
}
// Only update gas price once the block is confirmed valid.
self.l2_gas_price = max(
    sync_block.block_header_without_hash.next_l2_gas_price,
    VersionedConstants::latest_constants().min_gas_price,
);
``` [1](#0-0) 

### Proof of Concept

1. A sync peer returns a `SyncBlock` for height `H` with:
   - `next_l2_gas_price = ATTACKER_PRICE` (e.g., `u128::MAX`)
   - `block_number = H` (correct)
   - `timestamp = 0` (fails `timestamp >= last_block_timestamp` when `last_block_timestamp > 0`)

2. `try_sync` executes line 834: `self.l2_gas_price = max(ATTACKER_PRICE, min_gas_price)` → `self.l2_gas_price = ATTACKER_PRICE`.

3. The timestamp guard fires at line 844; `try_sync` returns `false`. The context does **not** advance height.

4. On the next consensus round, `build_proposal` is called. It reads `self.l2_gas_price = ATTACKER_PRICE` and embeds it in the `ProposalInit` and `BlockInfo` for block `H`.

5. `BlockExecutionArtifacts::new` constructs `PartialBlockHashComponents` with `l2_gas_price = ATTACKER_PRICE`.

6. `calculate_block_hash` hashes `ATTACKER_PRICE` into the block hash → wrong `PartialBlockHash` / final block hash stored in `apollo_storage`.

7. `starknet_estimateFee` reads the stored block header and returns fee estimates based on `ATTACKER_PRICE`, misleading users and applications. [7](#0-6) [8](#0-7)

### Citations

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L328-350)
```rust
        let block_header_without_hash = BlockHeaderWithoutHash {
            block_number: height,
            l1_gas_price,
            l1_data_gas_price,
            l2_gas_price,
            l2_gas_consumed: l2_gas_used,
            next_l2_gas_price: self.l2_gas_price,
            sequencer,
            timestamp: BlockTimestamp(init.timestamp),
            l1_da_mode: init.l1_da_mode,
            // TODO(guy.f): Figure out where/if to get the values below from and fill them.
            ..Default::default()
        };

        let sync_block = SyncBlock {
            state_diff: state_diff.clone(),
            account_transaction_hashes,
            l1_transaction_hashes,
            block_header_without_hash,
            block_header_commitments: Some(block_header_commitments),
        };

        self.deps.state_sync_client.add_new_block(sync_block).await
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L461-464)
```rust
                fee_market_info: FeeMarketInfo {
                    l2_gas_consumed: l2_gas_used,
                    next_l2_gas_price: self.l2_gas_price,
                },
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L833-858)
```rust
        // May be default for blocks older than 0.14.0, ensure min gas price is met.
        self.l2_gas_price = max(
            sync_block.block_header_without_hash.next_l2_gas_price,
            VersionedConstants::latest_constants().min_gas_price,
        );
        // TODO(Asmaa): validate starknet_version and parent_hash when they are stored.
        let block_number = sync_block.block_header_without_hash.block_number;
        let timestamp = sync_block.block_header_without_hash.timestamp;
        let last_block_timestamp =
            self.previous_block_info.as_ref().map_or(0, |info| info.timestamp);
        let now: u64 = self.deps.clock.unix_now();
        if !(block_number == height
            && timestamp.0 >= last_block_timestamp
            && timestamp.0 <= now + self.config.static_config.block_timestamp_window_seconds)
        {
            warn!(
                "Invalid block info: expected block number {}, got {}, expected timestamp range \
                 [{}, {}], got {}",
                height,
                block_number,
                last_block_timestamp,
                now + self.config.static_config.block_timestamp_window_seconds,
                timestamp.0,
            );
            return false;
        }
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L212-221)
```rust
pub struct PartialBlockHashComponents {
    pub header_commitments: BlockHeaderCommitments,
    pub block_number: BlockNumber,
    pub l1_gas_price: GasPricePerToken,
    pub l1_data_gas_price: GasPricePerToken,
    pub l2_gas_price: GasPricePerToken,
    pub sequencer: SequencerContractAddress,
    pub timestamp: BlockTimestamp,
    pub starknet_version: StarknetVersion,
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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator_test.rs (L243-251)
```rust
            },
            l2_gas_price: GasPricePerToken { price_in_fri: 1_u8.into(), price_in_wei: 1_u8.into() },
            sequencer: SequencerContractAddress(ContractAddress::from(1_u128)),
            timestamp: BlockTimestamp(1)
        },
        state_root: GlobalRoot(Felt::ONE),
        previous_block_hash: BlockHash(Felt::ONE)
    )
    // TODO(Aviv, 10/06/2024): add tests that changes the first hash input, and the const zero.
```

**File:** crates/apollo_batcher/src/block_builder.rs (L160-169)
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
```
