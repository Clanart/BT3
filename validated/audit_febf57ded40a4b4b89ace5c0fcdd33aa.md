### Title
`nowrap` Boolean in `check_time_locks` Is Not Consensus-Enforced by Fork Height — Relative Timelock Overflow Produces Divergent Accept/Reject Outcomes - (File: `crates/chia-consensus/src/check_time_locks.rs`)

### Summary

`check_time_locks` accepts a caller-supplied `nowrap: bool` that switches relative-timelock arithmetic between `saturating_add` and `wrapping_add`. For max-value relative timelocks (e.g., `ASSERT_HEIGHT_RELATIVE 0xffffffff`), the two modes produce **opposite** accept/reject decisions on the same spend bundle. No consensus constant or fork-height gate inside chia_rs enforces which mode must be used at a given block height; the choice is delegated entirely to the Python/wasm caller. An unprivileged spend bundle carrying a crafted max-value relative timelock can therefore be simultaneously valid on nodes using one mode and invalid on nodes using the other, causing deterministic consensus divergence.

### Finding Description

`check_time_locks` in `crates/chia-consensus/src/check_time_locks.rs` performs relative-timelock checks for `ASSERT_HEIGHT_RELATIVE`, `ASSERT_SECONDS_RELATIVE`, `ASSERT_BEFORE_HEIGHT_RELATIVE`, and `ASSERT_BEFORE_SECONDS_RELATIVE` conditions. [1](#0-0) 

When `nowrap=true`, the addition saturates at the type maximum; when `nowrap=false`, it wraps around. The test suite explicitly documents that these two modes produce opposite outcomes for overflow inputs: [2](#0-1) 

The Python-facing binding exposes `nowrap` as a free parameter with no enforcement: [3](#0-2) 

The public Python stub confirms the parameter is caller-controlled: [4](#0-3) 

The function `get_flags_for_height_and_constants`, which is the canonical mechanism for deriving consensus behavior from block height, does **not** set any `nowrap`-equivalent flag: [5](#0-4) 

`ConsensusConstants` contains no `nowrap_height` or equivalent field: [6](#0-5) 

### Impact Explanation

A spend bundle containing `ASSERT_HEIGHT_RELATIVE 0xffffffff` (or `ASSERT_SECONDS_RELATIVE 0xffffffffffffffff`) is accepted by a node calling `check_time_locks(..., nowrap=false)` because the addition wraps to a small value, making the check trivially pass. The same bundle is rejected by a node calling `check_time_locks(..., nowrap=true)` because saturation keeps the threshold at `u32::MAX`/`u64::MAX`. Nodes that disagree on spend validity will diverge on the canonical chain, satisfying the **Critical** impact criterion: a valid unprivileged spend bundle can trigger deterministic consensus divergence. [7](#0-6) 

### Likelihood Explanation

The `nowrap` parameter is not gated by any fork height inside chia_rs. Any inconsistency in how chia-blockchain (Python) determines the `nowrap` value — across node versions, during a soft-fork transition, or due to a bug in the caller — immediately produces divergent consensus outcomes for any spend bundle that uses a near-maximum relative timelock value. The attacker input is a single CLVM condition with a crafted integer argument, requiring no special privileges. [8](#0-7) 

### Recommendation

Move the `nowrap` decision inside chia_rs, deriving it deterministically from `prev_transaction_block_height` and a new `ConsensusConstants` field (e.g., `nowrap_height`). Replace the free `nowrap: bool` parameter with the height and constants, mirroring the pattern already used by `get_flags_for_height_and_constants`. This ensures all nodes compute the same arithmetic mode for any given block height without relying on the Python caller to pass the correct value. [5](#0-4) 

### Proof of Concept

Construct a spend bundle where one coin spend emits:

```
(ASSERT_HEIGHT_RELATIVE . 0xffffffff)
```

The coin was confirmed at block index `N`. Call `check_time_locks` at `prev_transaction_block_height = N + 200`:

- **`nowrap=true`**: `N.saturating_add(0xffffffff) = u32::MAX`; `N+200 < u32::MAX` → **Err** (spend rejected).
- **`nowrap=false`**: `N.wrapping_add(0xffffffff) = N - 1`; `N+200 < N-1` is false → **Ok** (spend accepted).

The same spend bundle is simultaneously valid and invalid depending solely on the caller-supplied `nowrap` flag, with no consensus-enforced rule inside chia_rs determining which value is correct. [1](#0-0) [2](#0-1)

### Citations

**File:** crates/chia-consensus/src/check_time_locks.rs (L12-18)
```rust
pub fn check_time_locks(
    removal_coin_records: &HashMap<Bytes32, CoinRecord>,
    bundle_conds: &OwnedSpendBundleConditions,
    prev_transaction_block_height: u32,
    timestamp: u64,
    nowrap: bool,
) -> Result<(), ValidationErr> {
```

**File:** crates/chia-consensus/src/check_time_locks.rs (L55-68)
```rust
        if let Some(height_relative) = spend.height_relative {
            if nowrap {
                if prev_transaction_block_height
                    < unspent
                        .confirmed_block_index
                        .saturating_add(height_relative)
                {
                    return Err(ValidationErr::Err(ErrorCode::AssertHeightRelativeFailed));
                }
            } else if prev_transaction_block_height
                < unspent.confirmed_block_index.wrapping_add(height_relative)
            {
                return Err(ValidationErr::Err(ErrorCode::AssertHeightRelativeFailed));
            }
```

**File:** crates/chia-consensus/src/check_time_locks.rs (L122-141)
```rust
pub fn py_check_time_locks(
    removal_coin_records: HashMap<Bytes32, CoinRecord>,
    bundle_conds: &OwnedSpendBundleConditions,
    prev_transaction_block_height: u32,
    timestamp: u64,
    nowrap: bool,
) -> PyResult<Option<u32>> {
    let res = check_time_locks(
        &removal_coin_records,
        bundle_conds,
        prev_transaction_block_height,
        timestamp,
        nowrap,
    );

    match res {
        Ok(()) => Ok(None),
        Err(ec) => Ok(Some(ec.error_code().into())),
    }
}
```

**File:** crates/chia-consensus/src/check_time_locks.rs (L276-281)
```rust
    // 200 < 100 + u32::MAX -> Err with nowrap (saturates), Ok without (wraps)
    #[case::height_relative_wrap(
        Osc { height_relative: Some(0xffff_ffff), ..Default::default() },
        Err(ValidationErr::Err(ErrorCode::AssertHeightRelativeFailed)),
        Ok(()),
    )]
```

**File:** wheel/python/chia_rs/chia_rs.pyi (L40-46)
```text
def check_time_locks(
    removal_coin_records: dict[bytes32, CoinRecord],
    bundle_conds: SpendBundleConditions,
    prev_transaction_block_height: uint32,
    timestamp: uint64,
    nowrap: bool,
) -> Optional[int]: ...
```

**File:** crates/chia-consensus/src/spendbundle_validation.rs (L61-102)
```rust
pub fn get_flags_for_height_and_constants(
    prev_tx_height: u32,
    constants: &ConsensusConstants,
) -> ConsensusFlags {
    //  the hard-fork initiated with 2.0. To activate June 2024
    //  * costs are ascribed to some unknown condition codes, to allow for
    // soft-forking in new conditions with cost
    //  * a new condition, SOFTFORK, is added which takes a first parameter to
    //    specify its cost. This allows soft-forks similar to the softfork
    //    operator
    //  * BLS operators introduced in the soft-fork (behind the softfork
    //    guard) are made available outside of the guard.
    //  * division with negative numbers are allowed, and round toward
    //    negative infinity
    //  * AGG_SIG_* conditions are allowed to have unknown additional
    //    arguments
    //  * Allow the block generator to be serialized with the improved clvm
    //   serialization format (with back-references)

    // The soft fork initiated with 2.5.0. The activation date is still TBD.
    // Adds a new keccak256 operator under the softfork guard with extension 1.
    // This operator can be hard forked in later, but is not included in a hard fork yet.

    // In hard fork 2, we enable the keccak operator outside the softfork guard
    let mut flags = ConsensusFlags::empty();
    if prev_tx_height >= constants.hard_fork2_height {
        flags |= ConsensusFlags::ENABLE_KECCAK_OPS_OUTSIDE_GUARD
            | ConsensusFlags::COST_CONDITIONS
            | ConsensusFlags::ENABLE_SECP_OPS
            | ConsensusFlags::RELAXED_BLS;
    }

    if prev_tx_height >= constants.soft_fork8_height {
        flags |= ConsensusFlags::DISABLE_OP;
    }

    if prev_tx_height >= constants.soft_fork9_height {
        flags |= ConsensusFlags::SIMPLE_GENERATOR
            | ConsensusFlags::CANONICAL_INTS
            | ConsensusFlags::LIMIT_SPENDS;
    }
    flags
```

**File:** crates/chia-consensus/src/consensus_constants.rs (L18-106)
```rust
pub struct ConsensusConstants {
    /// How many blocks to target per sub-slot.
    slot_blocks_target: u32,

    /// How many blocks must be created per slot (to make challenge sb).
    min_blocks_per_challenge_block: u8,

    /// Max number of blocks that can be infused into a sub-slot.
    /// Note: This must be less than SUB_EPOCH_BLOCKS/2, and > SLOT_BLOCKS_TARGET.
    max_sub_slot_blocks: u32,

    /// The number of signage points per sub-slot (including the 0th sp at the sub-slot start).
    num_sps_sub_slot: u8,

    /// The sub_slot_iters for the first epoch.
    sub_slot_iters_starting: u64,

    /// Multiplied by the difficulty to get iterations.
    difficulty_constant_factor: u128,

    /// The difficulty for the first epoch.
    difficulty_starting: u64,

    /// The maximum factor by which difficulty and sub_slot_iters can change per epoch.
    difficulty_change_max_factor: u32,

    /// The number of blocks per sub-epoch.
    sub_epoch_blocks: u32,

    /// The number of blocks per sub-epoch, must be a multiple of SUB_EPOCH_BLOCKS.
    epoch_blocks: u32,

    /// The number of bits to look at in difficulty and min iters. The rest are zeroed.
    significant_bits: u8,

    /// Max is 1024 (based on ClassGroupElement int size).
    discriminant_size_bits: u16,

    /// H(plot id + challenge hash + signage point) must start with these many zeroes.
    /// This applies to original plots, and proof-of-space format
    number_zero_bits_plot_filter_v1: u8,

    /// H(plot id + challenge hash + signage point) must start with these many zeroes.
    /// This applies to the new plot format, and proof-of-space format
    number_zero_bits_plot_filter_v2: u8,

    /// The smallest and largest allowed plot size for the original plot
    /// format, v1. These are the K-values for the plots.
    min_plot_size_v1: u8,
    max_plot_size_v1: u8,

    /// v2 plots only support a single k-value, specified by this constant.
    plot_size_v2: u8,

    /// The target number of seconds per sub-slot.
    sub_slot_time_target: u16,

    /// The difference between signage point and infusion point (plus required_iters).
    num_sp_intervals_extra: u8,

    /// After soft-fork2, this is the new MAX_FUTURE_TIME.
    max_future_time2: u32,

    /// Than the average of the last NUMBER_OF_TIMESTAMPS blocks.
    number_of_timestamps: u8,

    /// Used as the initial cc rc challenges, as well as first block back pointers, and first SES back pointer.
    /// We override this value based on the chain being run (testnet0, testnet1, mainnet, etc).
    genesis_challenge: Bytes32,

    /// Forks of chia should change these values to provide replay attack protection.
    agg_sig_me_additional_data: Bytes32,
    /// By convention, the below additional data is derived from the agg_sig_me_additional_data
    agg_sig_parent_additional_data: Bytes32,
    agg_sig_puzzle_additional_data: Bytes32,
    agg_sig_amount_additional_data: Bytes32,
    agg_sig_puzzle_amount_additional_data: Bytes32,
    agg_sig_parent_amount_additional_data: Bytes32,
    agg_sig_parent_puzzle_additional_data: Bytes32,

    /// The block at height must pay out to this pool puzzle hash.
    genesis_pre_farm_pool_puzzle_hash: Bytes32,

    /// The block at height must pay out to this farmer puzzle hash.
    genesis_pre_farm_farmer_puzzle_hash: Bytes32,

    /// The maximum number of classgroup elements within an n-wesolowski proof.
    max_vdf_witness_size: u8,

```
