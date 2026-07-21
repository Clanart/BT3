### Title
Uncommitted `next_l2_gas_price` in Block Header Enables Malicious P2P Peer to Inject Wrong Gas Price into Syncing Node — (`crates/starknet_api/src/block.rs`, `crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs`)

---

### Summary

`BlockHeaderWithoutHash` carries a `next_l2_gas_price` field that is propagated over P2P sync and used to seed the L2 gas price for the next block. This field is **absent from `PartialBlockHashComponents`** and therefore absent from the block hash. A malicious P2P peer can send a `SignedBlockHeader` whose block hash is valid but whose `next_l2_gas_price` is arbitrary. The syncing node accepts the block, stores the manipulated price as its authoritative starting gas price, and subsequently proposes blocks with a wrong `l2_gas_price_fri`. Every honest validator rejects those proposals via an exact-equality check, and the node's RPC fee-estimation endpoint returns an authoritative-looking but wrong value for every caller.

---

### Finding Description

**Step 1 — The uncommitted field.**

`BlockHeaderWithoutHash` contains both `l2_gas_consumed` and `next_l2_gas_price`: [1](#0-0) 

`PartialBlockHashComponents`, which is the sole input to `calculate_block_hash`, contains `l2_gas_price` (the *current* block's price) but **not** `next_l2_gas_price` and **not** `l2_gas_consumed`: [2](#0-1) 

`calculate_block_hash` chains only the fields present in `PartialBlockHashComponents`: [3](#0-2) 

`next_l2_gas_price` is therefore not covered by the Poseidon block-hash commitment.

**Step 2 — P2P sync trusts the field verbatim.**

The protobuf `SignedBlockHeader` carries `next_l2_gas_price` as field 19: [4](#0-3) 

The converter deserialises it directly into `BlockHeaderWithoutHash.next_l2_gas_price` without recomputing it from `l2_gas_consumed`: [5](#0-4) 

**Step 3 — The syncing node adopts the manipulated value as its gas price.**

`try_sync` reads `next_l2_gas_price` from the synced block header and stores it directly as `self.l2_gas_price`: [6](#0-5) 

The production path mirrors this: after a successful sync the context's `l2_gas_price` equals whatever `next_l2_gas_price` the peer sent.

**Step 4 — Validators enforce exact equality on `l2_gas_price_fri`.**

When the poisoned node later proposes a block it broadcasts `ProposalInit.l2_gas_price_fri` equal to the manipulated value. Every honest validator rejects it: [7](#0-6) 

There is no margin or tolerance for `l2_gas_price_fri`; the check is strict equality, unlike the `within_margin` check used for L1 prices.

**Step 5 — No upper-bound cap on L2 gas price.**

`calculate_next_base_gas_price` has a floor (`min_gas_price`) but no ceiling. The only guard against overflow is a saturating cast to `u128::MAX`: [8](#0-7) 

A peer that injects `next_l2_gas_price = u128::MAX` causes the node to broadcast that value in every subsequent proposal and in every RPC fee-estimation response.

---

### Impact Explanation

**Broken commitment invariant:** `next_l2_gas_price` is stored in the committed block header (`StorageBlockHeader.next_l2_gas_price`) and propagated via P2P sync, but it is not covered by the block hash. A peer can therefore forge this field while presenting a valid block hash, violating the invariant that every field in a committed header is authenticated.

**Concrete wrong values produced:**

1. The syncing node's `SequencerConsensusContext.l2_gas_price` is set to the attacker-chosen value.
2. Every `ProposalInit` the node broadcasts carries `l2_gas_price_fri = attacker_value`, which honest validators reject.
3. The node's `starknet_estimateFee` and `starknet_call` on the pending block return fees computed against `attacker_value`, an authoritative-looking but wrong result for every API caller.
4. The `next_l2_gas_price` field written to `StorageBlockHeader` for any block the node commits via the sync path carries the wrong value, poisoning downstream storage readers.

This matches: **High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value.**

---

### Likelihood Explanation

The trigger is unprivileged. Any node participating in P2P block-header sync can craft a `SignedBlockHeader` with a valid block hash (computed honestly) and an arbitrary `next_l2_gas_price`. No special role, key, or stake is required. The receiving node has no way to detect the manipulation because the field is not covered by the hash. The attack is silent and persistent: once the poisoned value is stored, it propagates into every subsequent proposal and RPC response until the node is restarted with a correct sync source.

---

### Recommendation

**Short term:** In `try_sync` (and any other sync path that reads `next_l2_gas_price` from a peer-supplied header), recompute the value from the block's `l2_gas_consumed` and the previous block's `l2_gas_price` using `calculate_next_base_gas_price`, rather than trusting the peer-supplied field.

**Long term:** Add `next_l2_gas_price` (and `l2_gas_consumed`) to `PartialBlockHashComponents` so they are covered by the Poseidon block-hash commitment. This closes the gap between what is stored in the header and what is authenticated by the hash, consistent with how `l2_gas_price` is already included.

---

### Proof of Concept

```
1. Attacker runs a P2P peer connected to victim node V.

2. Attacker observes honest block N with valid block_hash H_N.

3. Attacker constructs a SignedBlockHeader for block N:
   - block_hash          = H_N          (valid, computed honestly)
   - l2_gas_price        = honest value  (in PartialBlockHashComponents → covered)
   - next_l2_gas_price   = u128::MAX     (NOT in PartialBlockHashComponents → unchecked)
   - All other fields    = honest values

4. Attacker sends this header to V via the P2P block-headers protocol.

5. V verifies block_hash == Poseidon(PartialBlockHashComponents, state_root, parent_hash).
   Verification passes because next_l2_gas_price is not an input.

6. V stores the block and sets:
     context.l2_gas_price = GasPrice(u128::MAX)

7. V proposes block N+1 with ProposalInit { l2_gas_price_fri: u128::MAX, ... }.

8. Every honest validator W computes the expected price from block N's actual
   l2_gas_consumed and rejects V's proposal:
     init.l2_gas_price_fri (u128::MAX) != block_info_validation.l2_gas_price_fri (correct)
   → ValidateProposalError::InvalidBlockInfo

9. V's RPC endpoint returns starknet_estimateFee results computed against u128::MAX,
   causing all callers to receive astronomically inflated fee estimates.

10. V cannot propose valid blocks until restarted with a trusted sync source.
``` [9](#0-8) [7](#0-6) [2](#0-1)

### Citations

**File:** crates/starknet_api/src/block.rs (L229-243)
```rust
#[derive(Debug, Default, Clone, Eq, PartialEq, Hash, Deserialize, Serialize, PartialOrd, Ord)]
pub struct BlockHeaderWithoutHash {
    pub parent_hash: BlockHash,
    pub block_number: BlockNumber,
    pub l1_gas_price: GasPricePerToken,
    pub l1_data_gas_price: GasPricePerToken,
    pub l2_gas_price: GasPricePerToken,
    pub l2_gas_consumed: GasAmount,
    pub next_l2_gas_price: GasPrice,
    pub state_root: GlobalRoot,
    pub sequencer: SequencerContractAddress,
    pub timestamp: BlockTimestamp,
    pub l1_da_mode: L1DataAvailabilityMode,
    pub starknet_version: StarknetVersion,
}
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L209-236)
```rust
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
/// All information required to calculate a block hash except for the state root and the parent
/// block hash.
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

**File:** crates/apollo_protobuf/src/protobuf/protoc_output.rs (L1163-1166)
```rust
    pub l2_gas_consumed: u64,
    #[prost(message, optional, tag = "19")]
    pub next_l2_gas_price: ::core::option::Option<Uint128>,
    #[prost(enumeration = "L1DataAvailabilityMode", tag = "20")]
```

**File:** crates/apollo_protobuf/src/converters/header.rs (L173-177)
```rust
        let l2_gas_consumed = value.l2_gas_consumed.into();
        let next_l2_gas_price = u128::from(
            value.next_l2_gas_price.ok_or(missing("SignedBlockHeader::next_l2_gas_price"))?,
        )
        .into();
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context_test.rs (L1510-1527)
```rust
    deps.state_sync_client.expect_get_block().times(1).return_once(|height| {
        let mut sync_block = SyncBlock::default();
        sync_block.block_header_without_hash.block_number = height;
        sync_block.block_header_without_hash.next_l2_gas_price = GasPrice(SYNCED_NEXT_L2_GAS_PRICE);
        Ok(sync_block)
    });

    let mut context = deps.build_context();
    context.config.dynamic_config.min_l2_gas_price_per_height =
        vec![PricePerHeight { height: 250, price: CONFIG_MIN_PRICE_AT_250 }];

    // Sync succeeds at height 200, l2_gas_price is taken from synced next_l2_gas_price.
    assert!(context.try_sync(SYNC_HEIGHT).await);
    assert_eq!(context.l2_gas_price, GasPrice(SYNCED_NEXT_L2_GAS_PRICE));

    // First height initialization at 200: synced value is kept.
    context.set_height_and_round(SYNC_HEIGHT, ROUND_0).await.unwrap();
    assert_eq!(context.l2_gas_price, GasPrice(SYNCED_NEXT_L2_GAS_PRICE));
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L276-285)
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
```

**File:** crates/apollo_consensus_orchestrator/src/fee_market/mod.rs (L83-138)
```rust
pub fn calculate_next_base_gas_price(
    price: GasPrice,
    gas_used: GasAmount,
    gas_target: GasAmount,
    min_gas_price: GasPrice,
) -> GasPrice {
    let versioned_constants =
        orchestrator_versioned_constants::VersionedConstants::latest_constants();
    assert!(
        gas_target < versioned_constants.max_block_size,
        "Gas target must be lower than max block size."
    );
    assert!(gas_target.0 > 0, "Gas target must be greater than zero.");
    assert!(
        versioned_constants.gas_price_max_change_denominator > 0,
        "Denominator constant must be greater than zero."
    );

    // If the current price is below the minimum, apply a gradual adjustment and return early.
    // This allows the price to increase by at most 1/MIN_GAS_PRICE_INCREASE_DENOMINATOR per block.
    if price < min_gas_price {
        let max_increase = price.0 / MIN_GAS_PRICE_INCREASE_DENOMINATOR;
        let adjusted = price.0 + max_increase;
        // Cap at min_gas_price to avoid overshooting
        let adjusted_price = adjusted.min(min_gas_price.0);
        info!(
            "Fee Market: Price {} below minimum gas price {}, adjusted price: {} )",
            price.0, min_gas_price.0, adjusted_price
        );
        return GasPrice(adjusted_price);
    }

    // Use U256 to avoid overflow, as multiplying a u128 by a u64 remains within U256 bounds.
    let gas_delta = U256::from(gas_used.0.abs_diff(gas_target.0));
    let gas_target_u256 = U256::from(gas_target.0);
    let price_u256 = U256::from(price.0);

    // Calculate price change by multiplying first, then dividing. This avoids the precision loss
    // that occurs when dividing before multiplying.
    let denominator =
        gas_target_u256 * U256::from(versioned_constants.gas_price_max_change_denominator);
    let price_change = (price_u256 * gas_delta) / denominator;

    let adjusted_price_u256 =
        if gas_used > gas_target { price_u256 + price_change } else { price_u256 - price_change };

    // Sanity check: ensure direction of change is correct
    assert!(
        gas_used > gas_target && adjusted_price_u256 >= price_u256
            || gas_used <= gas_target && adjusted_price_u256 <= price_u256
    );

    // Price should not realistically exceed u128::MAX, bound to avoid theoretical overflow.
    let adjusted_price = u128::try_from(adjusted_price_u256).unwrap_or(u128::MAX);
    GasPrice(max(adjusted_price, min_gas_price.0))
}
```
