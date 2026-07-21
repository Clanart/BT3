### Title
`l2_gas_consumed` and `next_l2_gas_price` Accumulated and Stored but Excluded from Block Hash Commitment for v0.14.0+ Blocks — (`File: crates/starknet_api/src/block_hash/block_hash_calculator.rs`)

### Summary

`l2_gas_consumed` and `next_l2_gas_price` are fully stored in `StorageBlockHeader`, propagated through P2P sync, and used to drive the fee market for the next block, but are never fed into `gas_prices_to_hash` and therefore never committed into the block hash for any protocol version. An explicit TODO in the code acknowledges the omission for v0.14.0+. Because the codebase already processes v0.14.2 blocks, every block hash produced by this sequencer omits these two fields, meaning the block hash does not bind the fee-market output.

### Finding Description

`gas_prices_to_hash` is the sole function that converts gas-price data into the felt(s) chained into `calculate_block_hash`. For `BlockHashVersion >= V0_13_4` it hashes only `l1_gas_price`, `l1_data_gas_price`, and `l2_gas_price`:

```rust
// TODO(Ayelet): add l2_gas_consumed, next_l2_gas_price after 0.14.0.
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
                ...
                .chain(&l2_gas_price.price_in_wei.0.into())
                .chain(&l2_gas_price.price_in_fri.0.into())
                .get_poseidon_hash(),
        ]
    }
    ...
}
``` [1](#0-0) 

`PartialBlockHashComponents`, the struct that carries all inputs to `calculate_block_hash`, has no fields for `l2_gas_consumed` or `next_l2_gas_price`:

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
``` [2](#0-1) 

Meanwhile, both fields are fully populated in `StorageBlockHeader` and round-tripped through P2P sync: [3](#0-2) [4](#0-3) 

`next_l2_gas_price` is the direct output of the fee-market calculation and seeds the gas price for the next block: [5](#0-4) 

The codebase already ingests and stores v0.14.2 blocks (confirmed by `block_post_0_14_2.json` which carries both `l2_gas_consumed` and `next_l2_gas_price`), so the omission is active for every block the sequencer currently produces. [6](#0-5) 

### Impact Explanation

Because `l2_gas_consumed` and `next_l2_gas_price` are not committed in the block hash, the block hash does not bind the fee-market output. Two blocks that differ only in `next_l2_gas_price` produce an identical block hash. A sequencer can therefore set an arbitrary `next_l2_gas_price` — which directly controls the gas price charged to every transaction in the following block — without that manipulation being detectable through the block hash. This constitutes an incorrect fee/gas accounting effect with direct economic impact, matching the scope: *"Incorrect fee, gas, bouncer, resource accounting, refund, balance, or L1 gas price effect with economic impact."*

Additionally, any RPC or sync consumer that reconstructs or verifies the block hash will compute a value that diverges from the protocol-correct hash for v0.14.0+ blocks, matching: *"High. RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value."*

### Likelihood Explanation

The omission is unconditional for all blocks at `StarknetVersion >= V0_13_4` (which covers v0.14.x). No configuration flag or runtime check re-introduces the missing fields. The TODO comment confirms the developers are aware the fields must be added but have not yet done so, while the codebase is already operating at v0.14.2.

### Recommendation

1. Add `l2_gas_consumed: GasAmount` and `next_l2_gas_price: GasPrice` fields to `PartialBlockHashComponents`.
2. Populate them in `PartialBlockHashComponents::new` from `BlockInfo`.
3. Extend `gas_prices_to_hash` (or introduce a new versioned branch for `>= V0_14_0`) to chain `l2_gas_consumed` and `next_l2_gas_price` into the Poseidon hash alongside the existing gas-price fields.
4. Update `calculate_block_hash` to pass the new fields through.
5. Add a regression test that verifies changing `l2_gas_consumed` or `next_l2_gas_price` changes the resulting block hash.

### Proof of Concept

```
1. Build a block at starknet_version = "0.14.2" with next_l2_gas_price = X.
   Record block_hash_A = calculate_block_hash(...).

2. Build an otherwise identical block with next_l2_gas_price = X * 1000.
   Record block_hash_B = calculate_block_hash(...).

3. Assert block_hash_A == block_hash_B.
   // Passes because gas_prices_to_hash never receives next_l2_gas_price.

4. The second block charges users 1000× the intended gas price for the next block,
   yet its block hash is indistinguishable from the first.
```

The root cause is in `gas_prices_to_hash` at line 416–443 of `crates/starknet_api/src/block_hash/block_hash_calculator.rs`, and the missing struct fields in `PartialBlockHashComponents` at lines 209–221 of the same file. [7](#0-6) [2](#0-1)

### Citations

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L209-221)
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
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L409-443)
```rust
// For starknet version >= 0.13.3, returns:
// [Poseidon (
//     "STARKNET_GAS_PRICES0", gas_price_wei, gas_price_fri, data_gas_price_wei, data_gas_price_fri,
//     l2_gas_price_wei, l2_gas_price_fri
// )].
// Otherwise, returns:
// [gas_price_wei, gas_price_fri, data_gas_price_wei, data_gas_price_fri].
// TODO(Ayelet): add l2_gas_consumed, next_l2_gas_price after 0.14.0.
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

**File:** crates/apollo_storage/src/header.rs (L86-89)
```rust
    /// The amount of L2 gas consumed.
    pub l2_gas_consumed: GasAmount,
    /// The next L2 gas price.
    pub next_l2_gas_price: GasPrice,
```

**File:** crates/apollo_protobuf/src/converters/header.rs (L173-177)
```rust
        let l2_gas_consumed = value.l2_gas_consumed.into();
        let next_l2_gas_price = u128::from(
            value.next_l2_gas_price.ok_or(missing("SignedBlockHeader::next_l2_gas_price"))?,
        )
        .into();
```

**File:** crates/apollo_consensus_orchestrator/src/fee_market/mod.rs (L27-32)
```rust
pub struct FeeMarketInfo {
    /// Total gas consumed in the current block.
    pub l2_gas_consumed: GasAmount,
    /// Gas price for the next block.
    pub next_l2_gas_price: GasPrice,
}
```

**File:** crates/apollo_starknet_client/resources/reader/block_post_0_14_2.json (L1188-1191)
```json
    "starknet_version": "0.14.2",
    "l2_gas_consumed": 988191555,
    "next_l2_gas_price": "0x1dcd65000"
}
```
