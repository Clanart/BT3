## Title
Blockhash/ALT-validity windows are denominated in a fixed slot count while slot duration is now a dynamically feature-gated variable, causing expiration windows to silently shrink after a slot-time reduction — ([File: runtime/src/slot_params.rs], [File: accounts-db/src/blockhash_queue.rs], [File: runtime/src/bank/check_transactions.rs])

## Summary
The original report flags an oracle that encodes a 6-hour expiration as a fixed number of blocks, silently breaking when the chain's block time differs from the assumed 12 s. The Agave analog is structurally identical: the `BlockhashQueue`/`MAX_PROCESSING_AGE` transaction-validity window (and the related Address-Lookup-Table deactivation bound) is encoded purely as a count of slots, while this same codebase has introduced a live, feature-gated mechanism (`slot_params.rs`) that changes the real slot duration (`ns_per_slot`) from 400 ms down to 200 ms during a cluster's lifetime. A fixed-slot-count window whose real-world duration is silently cut by up to 2x when the chain's "block time" changes is the same broken invariant as the reported bug.

## Finding Description
`BlockhashQueue::is_hash_valid_for_age` / `get_hash_info_if_valid` determine hash validity strictly by counting registered-hash indices, i.e., by slots, with no reference to wall-clock time: [1](#0-0) [2](#0-1) 

`Bank::is_blockhash_valid` and `Bank::check_transaction_age` gate transaction acceptance on this slot-counted `max_processing_age`: [3](#0-2) [4](#0-3) 

This value (`MAX_PROCESSING_AGE`) has historically been meaningful only because slot time was fixed at 400 ms — i.e., "150 slots" implicitly meant "~60 seconds". The codebase, however, now explicitly supports changing the wall-clock length of a slot at runtime via feature activation, defined in `runtime/src/slot_params.rs`: [5](#0-4) [6](#0-5) 

`Bank::ns_per_slot_at_slot` / `slot_range_duration_nanos` and the accompanying tests confirm that `ns_per_slot` is not a chain-wide constant anymore but a per-slot, feature-activation-dependent value that can be as low as half the legacy value: [7](#0-6) [8](#0-7) 

Nowhere in `BlockhashQueue` or `Bank::check_transaction_age`/`is_blockhash_valid` is `max_age` (a slot count) rescaled using `ns_per_slot_at_slot`/`SlotParamsArchive` when the slot-time-reduction features activate. The same fixed-slot-count assumption appears again in the Address-Lookup-Table deactivation bound used by banking-stage packet ingestion, whose own doc comment concedes the deactivation period is really block/time-based but is approximated with a slot count via `estimate_last_valid_slot` (implementation not present in the indexed snapshot, but the caller explicitly treats it as a lower bound converted from a real-time deactivation period): [9](#0-8) 

This is the exact bug class from the external report — "expiration measured in blocks assuming a fixed block time" — except instead of manifesting across different EVM chains with different block times, it manifests across time on the *same* Agave cluster whenever a `reduce_slot_time_to_{350,300,250,200}ms` feature is activated: the number of slots stays the same (`MAX_PROCESSING_AGE`), but the wall-clock time it represents shrinks in proportion to the slot-time reduction.

## Impact Explanation
Existing test coverage shows that the assumed real-time duration of blockhash expiration is directly wired into cluster-wide liveness/consensus behavior, not just a UX inconvenience: `local-cluster/tests/local_cluster.rs::test_fork_choice_refresh_old_votes` demonstrates a scenario where blockhash expiration timing interacting with vote-refresh determines whether the network stalls trying to switch forks: [10](#0-9) [11](#0-10) 

If the real elapsed time corresponding to `MAX_PROCESSING_AGE` slots is silently cut in half around a slot-time-reduction activation boundary, transactions/votes that relied on the previously assumed ~60-second window (for resigning, relaying, or re-processing) can expire earlier than validator/operator logic anticipates, increasing `BlockhashNotFound` failures and vote-refresh churn cluster-wide during the transition. Because this affects an unprivileged, protocol-level invariant (transaction/vote validity), not an attacker-controlled input, it falls in the "false execution/acceptance" and "consensus stall/liveness" impact bucket rather than fund theft.

## Likelihood Explanation
Medium. The mismatch only manifests around a live `reduce_slot_time_to_*ms` feature activation — a deliberate, cluster-wide, but fully supported and already-implemented code path in this snapshot (`slot_params.rs`, `agave_feature_set::reduce_slot_time_to_200ms` etc.). Every future activation of one of these features would trigger the discrepancy for `MAX_PROCESSING_AGE` slots following the transition, with no attacker action required — this is a design/systemic bug, not an exploit requiring malicious input.

## Recommendation
Rescale slot-count-based expiration windows (`BlockhashQueue::max_age`/`MAX_PROCESSING_AGE`, the ALT-deactivation slot bound in `calculate_max_age`/`estimate_last_valid_slot`) using the currently effective `ns_per_slot` (via `Bank::slot_params`/`SlotParamsArchive`) so the *wall-clock* duration of these validity windows stays constant across slot-time transitions, analogous to the report's recommendation to key expiry off `block.timestamp` rather than a fixed block count.

## Proof of Concept
Not independently reproducible from the indexed snapshot alone (the concrete numeric value of `MAX_PROCESSING_AGE` and the body of `estimate_last_valid_slot` live outside what the index returned, so I cannot confirm with certainty whether `estimate_last_valid_slot` already performs a dynamic-`ns_per_slot` conversion). The mechanism can be demonstrated conceptually using the existing test scaffolding:
1. Use `Bank::new_for_tests` + `bank.feature_set.activate(&feature_set::reduce_slot_time_to_200ms::id(), activation_slot)` + `bank.refresh_slot_params()` as already exercised in `test_reduce_slot_time_range_duration`. [12](#0-11) 
2. Register `MAX_PROCESSING_AGE` blockhashes both before and after the activation slot and compare `Bank::slot_range_duration_nanos` for the two windows — despite the identical slot count, the wall-clock duration differs by up to 2x, confirming the fixed-slot-count expiry no longer represents a constant real-time window.

Given the incomplete visibility into `estimate_last_valid_slot`'s implementation and the exact value/usage sites of `MAX_PROCESSING_AGE` (defined in `solana_clock`, not indexed here), a Devin session with full repository access is recommended to confirm whether these values are already rescaled by `ns_per_slot_at_slot`, and if not, to implement the fix described above.

### Citations

**File:** accounts-db/src/blockhash_queue.rs (L97-108)
```rust
    /// Check if the age of the hash is within the specified age
    pub fn is_hash_valid_for_age(&self, hash: &Hash, max_age: usize) -> bool {
        self.get_hash_info_if_valid(hash, max_age).is_some()
    }

    /// Get hash info for the specified hash if it is in the queue and its age
    /// of the hash is within the specified age
    pub fn get_hash_info_if_valid(&self, hash: &Hash, max_age: usize) -> Option<&HashInfo> {
        self.hashes.get(hash).filter(|info| {
            Self::is_hash_index_valid(self.last_hash_index, max_age, info.hash_index)
        })
    }
```

**File:** accounts-db/src/blockhash_queue.rs (L130-132)
```rust
    fn is_hash_index_valid(last_hash_index: u64, max_age: usize, hash_index: u64) -> bool {
        last_hash_index - hash_index <= max_age as u64
    }
```

**File:** runtime/src/bank.rs (L2867-2903)
```rust
    /// Returns the effective slot duration for `slot`.
    pub fn ns_per_slot_at_slot(&self, slot: Slot) -> u128 {
        self.slot_params_at_slot(slot).ns_per_slot()
    }

    /// Returns slots/year for the slot params active at `epoch` start.
    fn slots_per_year_for_epoch(&self, epoch: Epoch) -> f64 {
        let first_slot = self.epoch_schedule().get_first_slot_in_epoch(epoch);
        self.slot_params_at_slot(first_slot).slots_per_year()
    }

    /// Returns the wall-clock duration in years for `[start_slot, end_slot)`.
    fn slot_range_duration_in_years(&self, start_slot: Slot, end_slot: Slot) -> f64 {
        if start_slot >= end_slot {
            return 0.0;
        }

        let mut cursor = start_slot;
        let mut params = self.slot_params.baseline_params();
        let mut duration = 0.0;

        for (effective_slot, effective_params) in self.slot_params.param_transitions() {
            if effective_slot <= start_slot {
                params = effective_params;
                continue;
            }
            if effective_slot >= end_slot {
                break;
            }

            duration += (effective_slot - cursor) as f64 / params.slots_per_year();
            cursor = effective_slot;
            params = effective_params;
        }

        duration + (end_slot - cursor) as f64 / params.slots_per_year()
    }
```

**File:** runtime/src/bank.rs (L3323-3326)
```rust
    pub fn is_blockhash_valid(&self, hash: &Hash) -> bool {
        let blockhash_queue = self.blockhash_queue.read().unwrap();
        blockhash_queue.is_hash_valid_for_age(hash, self.max_processing_age())
    }
```

**File:** runtime/src/bank/check_transactions.rs (L229-256)
```rust
    fn check_transaction_age(
        &self,
        tx: &impl SVMMessage,
        max_age: usize,
        next_durable_nonce: &DurableNonce,
        hash_queue: &BlockhashQueue,
        error_counters: &mut TransactionErrorMetrics,
        strict_nonce_size_check: bool,
        strict_nonce_authority_check: bool,
    ) -> TransactionResult<Option<Pubkey>> {
        let recent_blockhash = tx.recent_blockhash();
        if hash_queue
            .get_hash_info_if_valid(recent_blockhash, max_age)
            .is_some()
        {
            Ok(None)
        } else if let Some((nonce_address, _)) = self.check_nonce_transaction_validity(
            tx,
            next_durable_nonce,
            strict_nonce_size_check,
            strict_nonce_authority_check,
        ) {
            Ok(Some(nonce_address))
        } else {
            error_counters.blockhash_not_found += 1;
            Err(TransactionError::BlockhashNotFound)
        }
    }
```

**File:** runtime/src/slot_params.rs (L122-145)
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
```

**File:** runtime/src/slot_params.rs (L159-201)
```rust
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
```

**File:** runtime/src/bank/tests.rs (L6978-7016)
```rust
fn test_reduce_slot_time_range_duration() {
    const SLOTS_PER_EPOCH: Slot = 32;
    let bank_for_activations = |activations: &[(Pubkey, Slot)]| {
        let (mut genesis_config, _) = create_genesis_config_with_legacy_hashes(1_000_000);
        genesis_config.epoch_schedule =
            EpochSchedule::custom(SLOTS_PER_EPOCH, SLOTS_PER_EPOCH, false);
        let mut bank = Bank::new_for_tests(&genesis_config);
        let mut feature_set = FeatureSet::default();
        for (feature_id, activation_slot) in activations {
            feature_set.activate(feature_id, *activation_slot);
        }
        bank.feature_set = Arc::new(feature_set);
        bank.refresh_slot_params();
        bank
    };

    let ordered_activations = slot_time_feature_gates()
        .into_iter()
        .zip([1, 33, 65, 97])
        .map(|((feature_id, _), activation_slot)| (feature_id, activation_slot))
        .collect::<Vec<_>>();
    let bank = bank_for_activations(&ordered_activations);

    let expected_duration = [
        (SLOTS_PER_EPOCH, LEGACY_SLOT_PARAMS),
        (SLOTS_PER_EPOCH, SLOT_PARAMS_350MS),
        (SLOTS_PER_EPOCH, SLOT_PARAMS_300MS),
        (SLOTS_PER_EPOCH, SLOT_PARAMS_250MS),
        (SLOTS_PER_EPOCH, SLOT_PARAMS_200MS),
    ]
    .into_iter()
    .map(|(slots, params)| slots as f64 / params.slots_per_year())
    .sum::<f64>();

    assert_eq!(
        bank.slot_range_duration_in_years(0, SLOTS_PER_EPOCH * 5)
            .to_bits(),
        expected_duration.to_bits()
    );
```

**File:** core/src/banking_stage/transaction_scheduler/receive_and_buffer.rs (L474-499)
```rust
/// Given the epoch, the minimum deactivation slot, and the current slot,
/// return the `MaxAge` that should be used for the transaction. This is used
/// to determine the maximum slot that a transaction will be considered valid
/// for, without re-resolving addresses or resanitizing.
///
/// This function considers the deactivation period of Address Table
/// accounts. If the deactivation period runs past the end of the epoch,
/// then the transaction is considered valid until the end of the epoch.
/// Otherwise, the transaction is considered valid until the deactivation
/// period.
///
/// Since the deactivation period technically uses blocks rather than
/// slots, the value used here is the lower-bound on the deactivation
/// period, i.e. the transaction's address lookups are valid until
/// AT LEAST this slot.
fn calculate_max_age(
    sanitized_epoch: Epoch,
    deactivation_slot: Slot,
    current_slot: Slot,
) -> MaxAge {
    let alt_min_expire_slot = estimate_last_valid_slot(deactivation_slot.min(current_slot));
    MaxAge {
        sanitized_epoch,
        alt_invalidation_slot: alt_min_expire_slot,
    }
}
```

**File:** local-cluster/tests/local_cluster.rs (L3360-3387)
```rust
// Verifies replay refreshes an old vote whose original transaction expired so fork
// choice can still converge instead of stalling on stale weight.
#[test]
#[serial]
// Steps in this test:
// We want to create a situation like:
/*
      1 (2%, killed and restarted) --- 200 (37%, lighter fork)
    /
    0
    \-------- 4 (38%, heavier fork)
*/
// where the 2% that voted on slot 1 don't see their votes land in a block
// due to blockhash expiration, and thus without resigning their votes with
// a newer blockhash, will deem slot 4 the heavier fork and try to switch to
// slot 4, which doesn't pass the switch threshold. This stalls the network.

// We do this by:
// 1) Creating a partition so all three nodes don't see each other
// 2) Kill the validator with 2%
// 3) Wait for longer than blockhash expiration
// 4) Copy in the lighter fork's blocks up, *only* up to the first slot in the lighter fork
// (not all the blocks on the lighter fork!), call this slot `L`
// 5) Restart the validator with 2% so that he votes on `L`, but the vote doesn't land
// due to blockhash expiration
// 6) Resolve the partition so that the 2% repairs the other fork, and tries to switch,
// stalling the network.

```

**File:** local-cluster/tests/local_cluster.rs (L3447-3457)
```rust
    let on_before_partition_resolved =
        |cluster: &mut LocalCluster, context: &mut PartitionContext| {
            // Equal to ms_per_slot * MAX_PROCESSING_AGE, rounded up
            let sleep_time_ms = ms_for_n_slots(
                MAX_PROCESSING_AGE as u64 * total_slots_to_lighter_partition_ratio as u64,
                ticks_per_slot,
            );
            info!("Wait for blockhashes to expire, {sleep_time_ms} ms");

            // Wait for blockhashes to expire
            sleep(Duration::from_millis(sleep_time_ms));
```
