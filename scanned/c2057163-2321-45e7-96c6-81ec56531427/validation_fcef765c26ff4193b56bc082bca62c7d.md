## Title
Alpenglow's leader-supplied nanosecond clock lacks an epoch anchor, allowing a single rotating leader to compound `Clock::unix_timestamp` drift across blocks/epochs - (File: `runtime/src/block_component_processor.rs`, `runtime/src/bank.rs`, `core/src/block_creation_loop.rs`)

### Summary
The external report's core concern is that a single untrusted party (the miner) sets `block.timestamp` and can shift it within a small tolerance each block, and that without an absolute anchor this can produce an "incorrect result" for time-based validity checks. Agave's legacy clock-update path (`update_clock`/`get_timestamp_estimate`/`calculate_stake_weighted_timestamp`) defends against exactly this by requiring a stake-weighted median across many independent vote accounts and bounding the result against `epoch_start_timestamp`, an anchor tied to real elapsed slot time. The Alpenglow block-footer clock path, however, drops both protections: the new `Clock::unix_timestamp` is derived solely from the value the *current slot leader* self-reports, bounded only relative to the *previous block's* leader-reported value, with no stake-weighting and no re-anchoring to `epoch_start_timestamp`.

### Finding Description
In the Alpenglow path, the block leader stamps its own wall-clock time into the footer via `produce_block_footer`, first skewing it into a legal range with `skew_block_producer_time_nanos`: [1](#0-0) [2](#0-1) 

On the receiving/validating side, `enforce_nanosecond_clock_bounds` checks the footer's `block_producer_time_nanos` only against `[parent_time_nanos + 1, parent_time_nanos + 2 * elapsed_slot_duration_nanos]`, where `parent_time_nanos` comes from the parent bank's own footer-derived nanosecond clock (`get_nanosecond_clock`), not from any stake-weighted or genesis-anchored source: [3](#0-2) [4](#0-3) 

Once validated, the value is written unconditionally into the `Clock` sysvar and the Alpenglow nanosecond-clock account by `update_clock_from_footer`, and at epoch boundaries this same leader-controlled value becomes the new `epoch_start_timestamp` anchor for the whole epoch: [5](#0-4) 

This is materially weaker than the legacy path used pre-Alpenglow, where `update_clock` computes a stake-weighted median timestamp across many independent vote accounts and clamps it to `MAX_ALLOWABLE_DRIFT_PERCENTAGE_FAST`/`_SLOW_V2` relative to a PoH-estimate offset from `epoch_start_timestamp`: [6](#0-5) [7](#0-6) 
(canonical path: [8](#0-7) )

Because the Alpenglow bound's reference point (`parent_time_nanos`) is itself the *previous* leader's self-reported, already-bounded-but-potentially-skewed value rather than an independent stake-weighted or genesis-derived anchor, each single-hop check "passing" does not prevent multi-hop compounding: a leader can push the clock forward by up to `2 * elapsed_slot_duration_nanos` on each of the several consecutive blocks it produces within its own leader window, and if it lands on the first slot of a new epoch, that drifted value is baked in as `epoch_start_timestamp` for the rest of the epoch. Unlike the legacy path (which the report's fix ("restrict `age`", "don't set drift constant too low") exactly anticipates), there is no mechanism here re-checking accumulated drift against a stake-weighted or wall-clock ground truth once Alpenglow is active.

### Impact Explanation
`Clock::unix_timestamp` is a widely-relied-upon sysvar for on-chain program logic (vesting/lockup expiry, rate limiting, time-based authorization, feature-gated behavior, oracle-style staleness checks analogous to the reported `_valid`/`age` pattern). A rotating leader able to bias this value forward or backward, with the bias compounding into the epoch anchor, can cause programs relying on `Clock::unix_timestamp` to accept stale/future-dated conditions as valid or reject valid ones - a false-execution/false-acceptance class impact, matching the "Valid Impact" criteria (false execution/acceptance from an unprivileged, rotating-leader position, not requiring majority-stake collusion or a permanently malicious/trusted role).

### Likelihood Explanation
Any validator that is scheduled as leader for a slot/window - a normal, unprivileged, rotating role that every validator eventually assumes - can exploit this without needing majority stake or collusion, since the check only compares against the previous (also leader-controlled) footer value rather than an independent multi-party or genesis-anchored source. The drift-per-hop is nominally bounded to `2x` the slot duration, but this bound compounds over the leader's own multi-block window and can be permanently captured into `epoch_start_timestamp` if it coincides with an epoch boundary.

### Recommendation
Anchor the Alpenglow nanosecond-clock bound to an independent, non-leader-controlled reference (e.g., the same stake-weighted timestamp/`epoch_start_timestamp` mechanism used by the legacy path, or the PoH-based elapsed-time estimate from genesis) rather than solely to the immediately preceding leader-reported value, and re-validate/re-anchor accumulated drift at least once per epoch against that independent reference, mirroring the `MAX_ALLOWABLE_DRIFT_PERCENTAGE_FAST`/`_SLOW_V2` bound already used in `calculate_stake_weighted_timestamp`.

### Proof of Concept
1. Alpenglow is active; a validator wins several consecutive leader slots (a "leader window").
2. For each block in its window, the leader sets `block_producer_time_nanos` at the maximum allowed by `skew_block_producer_time_nanos`/`nanosecond_time_bounds`, i.e., `parent_time_nanos + 2 * elapsed_slot_duration_nanos`, which is legal by `enforce_nanosecond_clock_bounds` for that single hop.
3. Because `parent_time_nanos` for the next block is `get_nanosecond_clock()` of the just-produced (already-skewed) block, the skew compounds across the leader's own window (`runtime/src/block_component_processor.rs:684-712`, `739-750`).
4. If the final skewed block happens to start a new epoch, `update_clock_from_footer` sets `epoch_start_timestamp` to this drifted value (`runtime/src/bank.rs:3444-3448`), permanently anchoring the bias for the whole epoch with no stake-weighted correction, unlike the legacy `update_clock` path (`runtime/src/bank.rs:2426-2465`).
5. Downstream programs reading `Clock::unix_timestamp` for time-gated logic observe the leader-biased value.

### Citations

**File:** core/src/block_creation_loop.rs (L499-513)
```rust
fn skew_block_producer_time_nanos(
    parent_time_nanos: i64,
    working_bank_time_nanos: i64,
    elapsed_slot_duration_nanos: u128,
) -> i64 {
    let (min_working_bank_time, max_working_bank_time) =
        BlockComponentProcessor::nanosecond_time_bounds(
            parent_time_nanos,
            elapsed_slot_duration_nanos,
        );

    working_bank_time_nanos
        .max(min_working_bank_time)
        .min(max_working_bank_time)
}
```

**File:** core/src/block_creation_loop.rs (L517-544)
```rust
fn produce_block_footer(
    bank: &Bank,
    skip_reward_cert: Option<SkipRewardCertificate>,
    notar_reward_cert: Option<NotarRewardCertificate>,
    highest_finalized: Option<&ValidatedBlockFinalizationCert>,
) -> BlockFooterV1 {
    let mut block_producer_time_nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("Misconfigured system clock; couldn't measure block producer time.")
        .as_nanos() as i64;

    let slot = bank.slot();

    if let Some(parent_bank) = bank.parent() {
        // Get parent time from alpenglow clock (nanoseconds) or fall back to clock sysvar (seconds -> nanoseconds)
        let parent_time_nanos = parent_bank
            .get_nanosecond_clock()
            .unwrap_or_else(|| bank.clock().unix_timestamp.saturating_mul(1_000_000_000));
        let parent_slot = parent_bank.slot();
        let elapsed_slot_duration_nanos =
            bank.slot_range_duration_nanos(parent_slot.saturating_add(1), slot);

        block_producer_time_nanos = skew_block_producer_time_nanos(
            parent_time_nanos,
            block_producer_time_nanos,
            elapsed_slot_duration_nanos,
        );
    }
```

**File:** runtime/src/block_component_processor.rs (L684-712)
```rust
    fn enforce_nanosecond_clock_bounds(
        bank: &Bank,
        parent_bank: &Bank,
        footer: &BlockFooterV1,
    ) -> Result<(), BlockComponentProcessorError> {
        // Get parent time from the nanosecond clock account, or from the Tower-based
        // clock for the first Alpenglow block.
        let parent_time_nanos = parent_bank
            .get_nanosecond_clock()
            .unwrap_or_else(|| bank.clock().unix_timestamp.saturating_mul(1_000_000_000));

        let parent_slot = parent_bank.slot();
        let current_time_nanos =
            Self::block_producer_time_nanos_as_i64(footer.block_producer_time_nanos)?;
        let current_slot = bank.slot();
        let elapsed_slot_duration_nanos =
            bank.slot_range_duration_nanos(parent_slot.saturating_add(1), current_slot);

        let (lower_bound_nanos, upper_bound_nanos) =
            Self::nanosecond_time_bounds(parent_time_nanos, elapsed_slot_duration_nanos);

        let is_valid =
            lower_bound_nanos <= current_time_nanos && current_time_nanos <= upper_bound_nanos;

        match is_valid {
            true => Ok(()),
            false => Err(BlockComponentProcessorError::NanosecondClockOutOfBounds),
        }
    }
```

**File:** runtime/src/block_component_processor.rs (L739-750)
```rust
    pub fn nanosecond_time_bounds(
        parent_time_nanos: i64,
        elapsed_slot_duration_nanos: u128,
    ) -> (i64, i64) {
        let min_working_bank_time = parent_time_nanos.saturating_add(1);
        let max_working_bank_time_offset = elapsed_slot_duration_nanos
            .saturating_mul(2)
            .min(i64::MAX as u128) as i64;
        let max_working_bank_time = parent_time_nanos.saturating_add(max_working_bank_time_offset);

        (min_working_bank_time, max_working_bank_time)
    }
```

**File:** runtime/src/bank.rs (L2426-2465)
```rust
    fn update_clock(&self, parent_epoch: Option<Epoch>) {
        let mut unix_timestamp = self.clock().unix_timestamp;
        // set epoch_start_timestamp to None to warp timestamp
        let epoch_start_timestamp = {
            let epoch = if let Some(epoch) = parent_epoch {
                epoch
            } else {
                self.epoch()
            };
            let first_slot_in_epoch = self.epoch_schedule().get_first_slot_in_epoch(epoch);
            Some((first_slot_in_epoch, self.clock().epoch_start_timestamp))
        };
        let max_allowable_drift = MaxAllowableDrift {
            fast: MAX_ALLOWABLE_DRIFT_PERCENTAGE_FAST,
            slow: MAX_ALLOWABLE_DRIFT_PERCENTAGE_SLOW_V2,
        };

        let ancestor_timestamp = self.clock().unix_timestamp;
        if let Some(timestamp_estimate) =
            self.get_timestamp_estimate(max_allowable_drift, epoch_start_timestamp)
        {
            unix_timestamp = timestamp_estimate;
            if timestamp_estimate < ancestor_timestamp {
                unix_timestamp = ancestor_timestamp;
            }
        }
        datapoint_info!(
            "bank-timestamp-correction",
            ("slot", self.slot(), i64),
            ("from_genesis", self.unix_timestamp_from_genesis(), i64),
            ("corrected", unix_timestamp, i64),
            ("ancestor_timestamp", ancestor_timestamp, i64),
        );
        let mut epoch_start_timestamp =
            // On epoch boundaries, update epoch_start_timestamp
            if parent_epoch.is_some() && parent_epoch.unwrap() != self.epoch() {
                unix_timestamp
            } else {
                self.clock().epoch_start_timestamp
            };
```

**File:** runtime/src/bank.rs (L3428-3478)
```rust
    /// Update the clock sysvar from a block footer's nanosecond timestamp.
    /// Also stores the nanosecond value for later retrieval via `get_nanosecond_clock`.
    pub fn update_clock_from_footer(&self, unix_timestamp_nanos: i64) {
        if !self.feature_set.snapshot().alpenglow {
            return;
        }

        // On epoch boundaries, update epoch_start_timestamp
        //
        // Note: the genesis block's bank is created via new_from_genesis, which calls update_clock
        // unconditionally. In update_clock, we have a check for whether slot == 0, and if that's
        // the case, the clock is set to self.unix_timestamp_from_genesis().
        //
        // As a result, we don't actually need the (0, _) case below, since it's never invoked.
        // However, include this for completeness in the match statement.
        let unix_timestamp_s = unix_timestamp_nanos / 1_000_000_000;
        let epoch_start_timestamp = match (self.slot, self.parent()) {
            (0, _) => self.unix_timestamp_from_genesis(),
            (_, Some(parent)) if parent.epoch() != self.epoch() => unix_timestamp_s,
            _ => self.clock().epoch_start_timestamp,
        };

        // Update clock sysvar
        // NOTE: block footer UNIX timestamps are in nanoseconds, but clock sysvar stores timestamps
        // in seconds
        let clock = sysvar::clock::Clock {
            slot: self.slot,
            epoch_start_timestamp,
            epoch: self.epoch_schedule().get_epoch(self.slot),
            leader_schedule_epoch: self.epoch_schedule().get_leader_schedule_epoch(self.slot),
            unix_timestamp: unix_timestamp_s,
        };

        self.update_sysvar_account(&sysvar::clock::id(), |account| {
            create_account(
                &clock,
                self.inherit_specially_retained_account_fields(account),
            )
        });

        // Update Alpenglow clock
        let data = wincode::serialize(&unix_timestamp_nanos).unwrap();
        let lamports = Rent::default().minimum_balance(data.len());
        let mut alpenclock_acct = AccountSharedData::new(lamports, data.len(), &system_program::ID);
        alpenclock_acct.set_data_from_slice(&data);

        self.store_account_and_update_capitalization(&NANOSECOND_CLOCK_ACCOUNT, &alpenclock_acct);

        self.transaction_processor
            .reset_and_fill_sysvar_cache_entries(self);
    }
```

**File:** runtime/src/stake_weighted_timestamp.rs (L26-96)
```rust
pub(crate) fn calculate_stake_weighted_timestamp<I, K, V, T>(
    unique_timestamps: I,
    stakes: &HashMap<Pubkey, (u64, T /*Account|VoteAccount*/)>,
    slot: Slot,
    elapsed_slot_duration: impl Fn(Slot, Slot) -> Duration,
    epoch_start_timestamp: Option<(Slot, UnixTimestamp)>,
    max_allowable_drift: MaxAllowableDrift,
) -> Option<UnixTimestamp>
where
    I: IntoIterator<Item = (K, V)>,
    K: Borrow<Pubkey>,
    V: Borrow<(Slot, UnixTimestamp)>,
{
    let mut stake_per_timestamp: BTreeMap<UnixTimestamp, u128> = BTreeMap::new();
    let mut total_stake: u128 = 0;
    for (vote_pubkey, slot_timestamp) in unique_timestamps {
        let (timestamp_slot, timestamp) = slot_timestamp.borrow();
        let offset = elapsed_slot_duration(*timestamp_slot, slot);
        let estimate = timestamp.saturating_add(offset.as_secs() as i64);
        let stake = stakes
            .get(vote_pubkey.borrow())
            .map(|(stake, _account)| stake)
            .unwrap_or(&0);
        stake_per_timestamp
            .entry(estimate)
            .and_modify(|stake_sum| *stake_sum = stake_sum.saturating_add(*stake as u128))
            .or_insert(*stake as u128);
        total_stake = total_stake.saturating_add(*stake as u128);
    }
    if total_stake == 0 {
        return None;
    }
    let mut stake_accumulator: u128 = 0;
    let mut estimate = 0;
    // Populate `estimate` with stake-weighted median timestamp
    for (timestamp, stake) in stake_per_timestamp.into_iter() {
        stake_accumulator = stake_accumulator.saturating_add(stake);
        if stake_accumulator > total_stake / 2 {
            estimate = timestamp;
            break;
        }
    }
    // Bound estimate by `max_allowable_drift` since the start of the epoch
    if let Some((epoch_start_slot, epoch_start_timestamp)) = epoch_start_timestamp {
        let poh_estimate_offset = elapsed_slot_duration(epoch_start_slot, slot);
        let estimate_offset =
            Duration::from_secs((estimate as u64).saturating_sub(epoch_start_timestamp as u64));
        let max_allowable_drift_fast =
            poh_estimate_offset.saturating_mul(max_allowable_drift.fast) / 100;
        let max_allowable_drift_slow =
            poh_estimate_offset.saturating_mul(max_allowable_drift.slow) / 100;
        if estimate_offset > poh_estimate_offset
            && estimate_offset.saturating_sub(poh_estimate_offset) > max_allowable_drift_slow
        {
            // estimate offset since the start of the epoch is higher than
            // `max_allowable_drift_slow`
            estimate = epoch_start_timestamp
                .saturating_add(poh_estimate_offset.as_secs() as i64)
                .saturating_add(max_allowable_drift_slow.as_secs() as i64);
        } else if estimate_offset < poh_estimate_offset
            && poh_estimate_offset.saturating_sub(estimate_offset) > max_allowable_drift_fast
        {
            // estimate offset since the start of the epoch is lower than
            // `max_allowable_drift_fast`
            estimate = epoch_start_timestamp
                .saturating_add(poh_estimate_offset.as_secs() as i64)
                .saturating_sub(max_allowable_drift_fast.as_secs() as i64);
        }
    }
    Some(estimate)
}
```
