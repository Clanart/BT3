[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** runtime/src/slot_params.rs (L295-320)
```rust
    /// Returns the exact wall-clock duration in nanoseconds for
    /// `start_slot..=end_slot`.
    pub(crate) fn slot_range_duration_nanos(&self, start_slot: Slot, end_slot: Slot) -> u128 {
        if start_slot > end_slot {
            return 0;
        }

        let mut cursor = start_slot;
        let mut params = self.params_at_slot(start_slot);
        let mut duration = 0u128;

        for (&effective_slot, &effective_params) in self
            .param_transitions
            .range((Excluded(start_slot), Included(end_slot)))
        {
            duration = duration.saturating_add(
                u128::from(effective_slot.saturating_sub(cursor))
                    .saturating_mul(params.ns_per_slot()),
            );
            cursor = effective_slot;
            params = effective_params;
        }

        let remaining_slots = u128::from(end_slot.saturating_sub(cursor)) + 1;
        duration.saturating_add(remaining_slots.saturating_mul(params.ns_per_slot()))
    }
```

**File:** runtime/src/bank.rs (L2366-2373)
```rust
    /// computed unix_timestamp at this slot height
    pub fn unix_timestamp_from_genesis(&self) -> i64 {
        self.genesis_creation_time.saturating_add(
            (self.slot as u128)
                .saturating_mul(self.ns_per_slot)
                .saturating_div(1_000_000_000) as i64,
        )
    }
```

**File:** runtime/src/bank.rs (L2459-2469)
```rust
        let mut epoch_start_timestamp =
            // On epoch boundaries, update epoch_start_timestamp
            if parent_epoch.is_some() && parent_epoch.unwrap() != self.epoch() {
                unix_timestamp
            } else {
                self.clock().epoch_start_timestamp
            };
        if self.slot == 0 {
            unix_timestamp = self.unix_timestamp_from_genesis();
            epoch_start_timestamp = self.unix_timestamp_from_genesis();
        }
```

**File:** runtime/src/bank.rs (L2995-3018)
```rust
    fn get_timestamp_estimate(
        &self,
        max_allowable_drift: MaxAllowableDrift,
        epoch_start_timestamp: Option<(Slot, UnixTimestamp)>,
    ) -> Option<UnixTimestamp> {
        let mut get_timestamp_estimate_time = Measure::start("get_timestamp_estimate");
        let slots_per_epoch = self.epoch_schedule().slots_per_epoch;
        let vote_accounts = self.vote_accounts();
        let recent_timestamps = vote_accounts.iter().filter_map(|(pubkey, (_, account))| {
            let vote_state = account.vote_state_view();
            let last_timestamp = vote_state.last_timestamp();
            let slot_delta = self.slot().checked_sub(last_timestamp.slot)?;
            (slot_delta <= slots_per_epoch)
                .then_some((*pubkey, (last_timestamp.slot, last_timestamp.timestamp)))
        });
        let elapsed_slot_duration = |from_slot: Slot, to_slot: Slot| {
            if from_slot >= to_slot {
                Duration::ZERO
            } else {
                Duration::from_nanos_u128(
                    self.slot_range_duration_nanos(from_slot.saturating_add(1), to_slot),
                )
            }
        };
```

**File:** runtime/src/bank/tests.rs (L7092-7098)
```rust
    let expected_elapsed = Duration::from_nanos_u128(
        bank.slot_range_duration_nanos(vote_timestamp_slot + 1, current_slot),
    );
    let scalar_elapsed =
        Duration::from_nanos(bank.ns_per_slot as u64) * (current_slot - vote_timestamp_slot) as u32;

    assert_ne!(expected_elapsed.as_secs(), scalar_elapsed.as_secs());
```
