Based on my investigation, the strongest and cleanest analog to the Berachain block-time hardcoding bug is `core/src/epoch_specs.rs`'s `get_epoch_duration`, which drives gossip's stale-CRDS purge/timeout window.

### Title
Gossip CRDS timeout uses hardcoded legacy slot time instead of active runtime slot duration - (File: core/src/epoch_specs.rs)

### Summary
Agave's runtime supports reducing the actual slot time from the legacy 400ms down to 350/300/250/200ms via a family of feature gates (`reduce_slot_time_to_350ms`, `..._300ms`, `..._250ms`, `..._200ms`), tracked per-bank in `SlotParams`/`SlotParamsArchive` and exposed via `Bank::ns_per_slot_at_slot`. However, `get_epoch_duration` in `core/src/epoch_specs.rs`, which is consumed by gossip's `ClusterInfo` for CRDS staleness/purge timing, explicitly computes epoch duration using the hardcoded legacy constant `DEFAULT_MS_PER_SLOT` (400ms) rather than the bank's actual/effective slot time.

### Finding Description
`get_epoch_duration` computes: [1](#0-0) 
which multiplies the number of slots in the epoch by the fixed legacy constant `DEFAULT_MS_PER_SLOT`, with a comment stating this is "intentional." This value (`EpochSpecs::epoch_duration`) feeds `ClusterInfo`'s epoch-duration used for CRDS timeout/purge, mirroring the same computation pattern seen in `gossip/src/cluster_info.rs`'s `DEFAULT_EPOCH_DURATION`: [2](#0-1) 

Meanwhile, the runtime tracks real, feature-activated slot durations via `SlotParams`, with values as low as 200ms: [3](#0-2) 
and `Bank::ns_per_slot_at_slot`/`current_slot_params` reflect the actual effective slot pacing at any given slot: [4](#0-3) 

Just as the Berachain governor hardcoded a 15s block time when the chain actually runs at 5s (producing shorter-than-intended voting windows), Agave's gossip epoch-duration calculation hardcodes the legacy 400ms slot time even when `reduce_slot_time_to_*` features make the chain run 2x (200ms) faster. Since real epochs then complete in less wall-clock time than `DEFAULT_MS_PER_SLOT * slots_in_epoch` implies, the computed "epoch duration" used for CRDS purge/timeout is inflated relative to the actual epoch cadence — the inverse mismatch of the original report, but the same root cause: a size/duration limit derived from an assumed per-slot time constant that diverges from the network's actual configured/effective block time.

### Impact Explanation
`EpochSpecs::epoch_duration` is used by gossip's CRDS table to time out and purge stale entries tied to a validator's active-stake epoch (staked-node CRDS values, e.g., contact info / stake-weighted values used for peer selection and pull-request scoring). If the effective slot time is reduced via the `reduce_slot_time_to_*` feature family, several real epochs' worth of wall-clock time elapse while gossip still measures staleness against the old, larger legacy-slot-time-derived duration. This causes stale CRDS/stake data to be retained far longer than intended relative to the actual epoch boundaries, which can skew peer/stake-based decisions in gossip (e.g., holding onto outdated staked-node info long past its real epoch validity). This is a purge/staleness accounting inconsistency in gossip's CRDS handling, not a workable fund-theft or consensus-halt primitive, and the underlying values are informational (peer table decisions), not directly a rooting/execution decision.

### Likelihood Explanation
The `reduce_slot_time_to_*` feature family is a real, live cluster feature-gate mechanism already wired through `Bank::slot_params`, `apply_slot_time_persistent_changes`, and tested extensively in `runtime/src/bank/tests.rs` (e.g., `test_reduce_slot_time_features_active_at_genesis`, `test_reduce_slot_time_range_duration`). Once any of these features activates cluster-wide, every validator's `get_epoch_duration` call in `core/src/epoch_specs.rs` is affected identically and permanently for that epoch — this is not a targeted/attacker-triggered path, it's a systemic miscalculation across all nodes once the feature activates.

### Recommendation
Compute `get_epoch_duration` (and the analogous `DEFAULT_EPOCH_DURATION` fallback in `gossip/src/cluster_info.rs`) from the bank's actual effective slot-time parameters (`Bank::ns_per_slot_at_slot` / `slot_range_duration_nanos`) for the epoch in question, rather than the fixed `DEFAULT_MS_PER_SLOT`/`DEFAULT_NS_PER_SLOT` legacy constant, so gossip staleness windows track real epoch cadence as slot-time-reduction features activate.

### Proof of Concept
1. Activate `reduce_slot_time_to_200ms` (or any of the `reduce_slot_time_to_*` gates) on a test cluster, per the activation flow exercised in `runtime/src/bank/tests.rs::test_reduce_slot_time_features_active_at_genesis`.
2. After the feature's effective epoch (one epoch after activation, per `SlotParamsArchive::feature_effective_slot`), the bank's real slot cadence becomes 200ms while `EpochSpecs::epoch_duration()` (`core/src/epoch_specs.rs:93-98`) still returns `slots_in_epoch * DEFAULT_MS_PER_SLOT` (400ms/slot).
3. Observe that gossip's CRDS purge/staleness window computed from this duration is ~2x longer in wall-clock terms than an actual epoch, causing stake-related CRDS data to persist across multiple real epochs before being purged — inconsistent with the intended "one epoch" staleness bound. [1](#0-0) [5](#0-4) [6](#0-5)

### Citations

**File:** core/src/epoch_specs.rs (L93-98)
```rust
fn get_epoch_duration(bank: &Bank) -> Duration {
    let num_slots = bank.get_slots_in_epoch(bank.epoch());
    // Gossip staked-CRDS timeout/purge intentionally follows the legacy
    // default slot duration, not the runtime slot duration.
    Duration::from_millis(num_slots.saturating_mul(DEFAULT_MS_PER_SLOT))
}
```

**File:** gossip/src/cluster_info.rs (L99-102)
```rust
const DEFAULT_EPOCH_DURATION: Duration =
    Duration::from_millis(DEFAULT_SLOTS_PER_EPOCH * DEFAULT_MS_PER_SLOT);
/// milliseconds we sleep for between gossip rounds
pub const GOSSIP_SLEEP_MILLIS: u64 = 100;
```

**File:** runtime/src/slot_params.rs (L122-181)
```rust
pub const LEGACY_HASHES_PER_TICK: u64 = 62_500;
pub(crate) const LEGACY_SLOT_PARAMS: SlotParams = SlotParams {
    ns_per_slot: 400_000_000,
    slots_per_year: 78_892_314.984,
    hashes_per_tick: Some(LEGACY_HASHES_PER_TICK),
    cost_tracker_limits: CostTrackerLimits::new(24_000_000, 60_000_000, 100_000_000),
    max_data_shreds_per_slot: 32_768,
    max_code_shreds_per_slot: 32_768,
    max_entry_bytes_per_slot: 20 * 1024 * 1024,
    partitioned_epoch_rewards_stake_account_stores_per_block: 4096,
    vat_to_burn_per_epoch: 1_600_000_000,
};

pub(crate) const SLOT_PARAMS_350MS: SlotParams = SlotParams {
    ns_per_slot: 350_000_000,
    slots_per_year: 90_162_645.696,
    hashes_per_tick: Some(54_687),
    cost_tracker_limits: CostTrackerLimits::new(21_000_000, 52_500_000, 87_500_000),
    max_data_shreds_per_slot: 28_672,
    max_code_shreds_per_slot: 28_672,
    max_entry_bytes_per_slot: 18_350_080,
    partitioned_epoch_rewards_stake_account_stores_per_block: 3_584,
    vat_to_burn_per_epoch: 1_400_000_000,
};

pub(crate) const SLOT_PARAMS_300MS: SlotParams = SlotParams {
    ns_per_slot: 300_000_000,
    slots_per_year: 105_189_753.312,
    hashes_per_tick: Some(46_875),
    cost_tracker_limits: CostTrackerLimits::new(18_000_000, 45_000_000, 75_000_000),
    max_data_shreds_per_slot: 24_576,
    max_code_shreds_per_slot: 24_576,
    max_entry_bytes_per_slot: 15_728_640,
    partitioned_epoch_rewards_stake_account_stores_per_block: 3_072,
    vat_to_burn_per_epoch: 1_200_000_000,
};

pub(crate) const SLOT_PARAMS_250MS: SlotParams = SlotParams {
    ns_per_slot: 250_000_000,
    slots_per_year: 126_227_703.974,
    hashes_per_tick: Some(39_062),
    cost_tracker_limits: CostTrackerLimits::new(15_000_000, 37_500_000, 62_500_000),
    max_data_shreds_per_slot: 20_480,
    max_code_shreds_per_slot: 20_480,
    max_entry_bytes_per_slot: 13_107_200,
    partitioned_epoch_rewards_stake_account_stores_per_block: 2_560,
    vat_to_burn_per_epoch: 1_000_000_000,
};

pub(crate) const SLOT_PARAMS_200MS: SlotParams = SlotParams {
    ns_per_slot: 200_000_000,
    slots_per_year: 157_784_629.968,
    hashes_per_tick: Some(31_250),
    cost_tracker_limits: CostTrackerLimits::new(12_000_000, 30_000_000, 50_000_000),
    max_data_shreds_per_slot: 16_384,
    max_code_shreds_per_slot: 16_384,
    max_entry_bytes_per_slot: 10_485_760,
    partitioned_epoch_rewards_stake_account_stores_per_block: 2_048,
    vat_to_burn_per_epoch: 800_000_000,
};
```

**File:** runtime/src/slot_params.rs (L183-211)
```rust
/// Slot-time reduction gates in the intended activation order.
const SLOT_TIME_REDUCTION_PARAMS: [(Pubkey, SlotParams); 4] = [
    (
        feature_set::reduce_slot_time_to_350ms::ID,
        SLOT_PARAMS_350MS,
    ),
    (
        feature_set::reduce_slot_time_to_300ms::ID,
        SLOT_PARAMS_300MS,
    ),
    (
        feature_set::reduce_slot_time_to_250ms::ID,
        SLOT_PARAMS_250MS,
    ),
    (
        feature_set::reduce_slot_time_to_200ms::ID,
        SLOT_PARAMS_200MS,
    ),
];

/// Returns slot-time feature gates mapped to runtime slot parameters.
pub fn slot_time_feature_gates() -> [(Pubkey, SlotParams); 4] {
    SLOT_TIME_REDUCTION_PARAMS
}

/// Returns all slot-time reduction feature IDs in activation order.
pub fn slot_time_feature_ids() -> [Pubkey; 4] {
    SLOT_TIME_REDUCTION_PARAMS.map(|(feature_id, _)| feature_id)
}
```

**File:** runtime/src/bank.rs (L2867-2870)
```rust
    /// Returns the effective slot duration for `slot`.
    pub fn ns_per_slot_at_slot(&self, slot: Slot) -> u128 {
        self.slot_params_at_slot(slot).ns_per_slot()
    }
```
