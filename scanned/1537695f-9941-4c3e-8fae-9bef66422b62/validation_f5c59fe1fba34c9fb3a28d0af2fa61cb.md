## Title
`Bank::apply_slot_time_persistent_changes` can desynchronize `rent_collector.slots_per_year` from `slots_per_year`/`SlotParamsArchive`, and the mismatch is only caught by a hard `assert_eq!` that panics the validator - (File: `runtime/src/bank.rs`)

### Summary
Analogous to the `RemoraToken`/`PaymentSettler` bug — where one contract's copy of a shared value (`stablecoin`) could drift from another's because only one side had an update path — `Bank` keeps the "slots-per-year" value duplicated in three places: `self.slots_per_year`, `self.rent_collector.slots_per_year`, and the value implied by `self.slot_params` (a `SlotParamsArchive` derived from the active `feature_set`/`epoch_schedule`). These three copies are supposed to always agree, but they are updated through different code paths at different times, and the only thing that detects a divergence is `assert_bank_matches_slot_params()`, which calls `assert_eq!` and will `panic!` the validator process if they disagree.

### Finding Description
`Bank::apply_slot_time_persistent_changes` is the single function responsible for keeping `ns_per_slot`, `slots_per_year`, and `rent_collector.slots_per_year` in sync with the currently active `slot_params`: [1](#0-0) 

This function must be called every time `slot_params` changes (e.g., on feature activation at an epoch boundary via `refresh_slot_params()` in `compute_and_apply_new_feature_activations`). However, `refresh_slot_params()` itself only rebuilds the cached `SlotParamsArchive`: [2](#0-1) 

`refresh_slot_params()` and `apply_slot_time_persistent_changes()` are two separate calls that must both fire, in the right order, whenever the feature set (and therefore slot params) changes. `compute_and_apply_new_feature_activations` (the per-epoch-boundary path) calls `refresh_slot_params()`: [3](#0-2) 

but does **not** call `apply_slot_time_persistent_changes()` in the same function — that call only happens inside `apply_activated_features()`, which is invoked from genesis and snapshot-restore paths, not from the epoch-boundary feature-activation path: [4](#0-3) 

So there are effectively two different "appliers" of slot-time changes (`apply_slot_time_runtime_changes` for cost-tracker/partitioned-rewards fields, and `apply_slot_time_persistent_changes` for `ns_per_slot`/`slots_per_year`/`rent_collector.slots_per_year`), and only some call sites invoke both together. The consistency between `self.slots_per_year`, `self.rent_collector.slots_per_year`, and the value baked into `slot_params` is never re-derived on demand; it is asserted, not enforced: [5](#0-4) 

This mirrors the report's bug class exactly: `stablecoin` in `PaymentSettler` had an update path (`changeStablecoin`) while `RemoraToken`'s copy had none, so after a change the two diverged and dependent functions reverted. Here, `slot_params` (derived from `feature_set`) can change at every epoch boundary via `refresh_slot_params()`, but the dependent, snapshot-serialized copies (`slots_per_year`, `rent_collector.slots_per_year`) are only refreshed by a separate function (`apply_slot_time_persistent_changes`) that is not called from every path that changes `slot_params`. If any call site (present or future) updates `feature_set`/`slot_params` without following up with `apply_slot_time_persistent_changes`, the persisted fields go stale relative to the derived `SlotParamsArchive`, and the only safety net is the `assert_eq!` in `assert_bank_matches_slot_params`, which is a hard panic, not a graceful error path.

### Impact Explanation
Unlike the Solidity case (function reverts), Agave's analog is worse for consensus liveness: because the invariant is enforced with `assert_eq!`/`panic!` rather than a recoverable error, any code path that manages to change the derived slot-time parameters (`slot_params`) without also calling `apply_slot_time_persistent_changes` (or vice versa — updating the persisted fields without refreshing `slot_params`) will cause `assert_bank_matches_slot_params()` to panic the validator process the next time it is invoked (e.g., on snapshot load/restore). A validator-wide panic on a widely-exercised bank-lifecycle path is a non-RPC remote-triggerable crash/consensus-halt risk if the divergence can be reached during normal feature-activation processing that every validator on the network executes identically — turning a benign inconsistency into a synchronized fleet-wide crash rather than an isolated, recoverable error.

### Likelihood Explanation
This is a latent structural risk rather than a demonstrated exploit: I was not able to fully verify, within the scope of local/index-only inspection, that a *currently reachable* runtime path calls `refresh_slot_params()` without a matching `apply_slot_time_persistent_changes()` call in a way that actually changes the derived `slots_per_year`/`ns_per_slot` values before `assert_bank_matches_slot_params()` is checked (the assert only runs on `compute_and_apply_features_after_snapshot_restore`, and normal epoch-boundary code seems to keep values unchanged unless a slot-time-reduction feature activates). This needs confirmation with a full read of `compute_active_feature_set`, `SlotParamsArchive::any_slot_time_reduction_effective`, and all call sites of `refresh_slot_params`/`apply_slot_time_persistent_changes`/`assert_bank_matches_slot_params`, which the available indexed context did not fully cover.

### Recommendation
Consolidate the "slot params changed" invariant into a single function that atomically updates `slot_params`, `slots_per_year`, `ns_per_slot`, and `rent_collector.slots_per_year` together, rather than relying on callers to invoke `refresh_slot_params()` and `apply_slot_time_persistent_changes()` in the correct order from every call site. Replace the `assert_eq!`-based consistency check with a self-healing recomputation (recompute the persisted fields from `slot_params` rather than panicking) wherever this can be done without altering already-serialized snapshot semantics, or at minimum add a debug-only assertion plus a runtime fallback that recomputes and logs rather than crashing the process.

### Proof of Concept
Not independently reproducible from the indexed context alone. The `PoC` for this would be: construct a `Bank`, activate a slot-time-reduction feature at an epoch boundary via `compute_and_apply_new_feature_activations` while bypassing/removing the corresponding `apply_slot_time_persistent_changes()` call (either an existing code path or a hypothetical future call site), then invoke `assert_bank_matches_slot_params()` (reachable via `compute_and_apply_features_after_snapshot_restore` on snapshot load) to show the `assert_eq!("snapshot slot-time slots_per_year mismatch")` panics. Confirming whether this is reachable *today* (vs. only through a future code-maintenance regression) requires tracing every caller of `refresh_slot_params` and `apply_slot_time_persistent_changes` and the `slot_time_feature_gates()` activation logic, which was not fully explorable within available tool budget.

### Citations

**File:** runtime/src/bank.rs (L2732-2751)
```rust
    /// Rebuilds slot-param state from the current feature set.
    fn refresh_slot_params(&mut self) {
        self.refresh_slot_params_with_baseline(self.slot_params.baseline_params());
    }

    fn refresh_slot_params_from_snapshot(&mut self, genesis_config: &GenesisConfig) {
        let (feature_set, _) = self.compute_active_feature_set(false);
        self.refresh_slot_params_with_baseline(
            self.snapshot_restore_slot_params_baseline(genesis_config, &feature_set),
        );
    }

    /// Rebuilds cached slot params while preserving the supplied slot-0 baseline.
    ///
    /// The cache is not serialized into snapshots; it is reconstructed from
    /// existing Bank fields during genesis and snapshot restore.
    fn refresh_slot_params_with_baseline(&mut self, baseline_params: SlotParams) {
        self.slot_params =
            SlotParamsArchive::new(&self.feature_set, &self.epoch_schedule, baseline_params);
    }
```

**File:** runtime/src/bank.rs (L4888-4899)
```rust
    /// Applies slot-time changes for fields serialized into snapshots.
    fn apply_slot_time_persistent_changes(&mut self) {
        let params = self.current_slot_params();
        self.ns_per_slot = params.ns_per_slot();
        self.slots_per_year = params.slots_per_year();
        self.rent_collector.slots_per_year = params.slots_per_year();
        if !self.feature_set.is_active(&feature_set::alpenglow::id())
            && self.hashes_per_tick().is_some()
        {
            self.set_hashes_per_tick(params.hashes_per_tick());
        }
    }
```

**File:** runtime/src/bank.rs (L4901-4932)
```rust
    /// Verifies bank fields are consistent with current slot params.
    fn assert_bank_matches_slot_params(&self) {
        let params = self.current_slot_params();
        assert_eq!(
            self.ns_per_slot,
            params.ns_per_slot(),
            "snapshot slot-time ns_per_slot mismatch"
        );
        assert_eq!(
            self.slots_per_year.to_bits(),
            params.slots_per_year().to_bits(),
            "snapshot slot-time slots_per_year mismatch"
        );
        assert_eq!(
            self.rent_collector.slots_per_year.to_bits(),
            params.slots_per_year().to_bits(),
            "snapshot slot-time rent_collector.slots_per_year mismatch"
        );
        let hashes_per_tick = self.hashes_per_tick();
        if !self.feature_set.is_active(&feature_set::alpenglow::id()) && hashes_per_tick.is_some() {
            assert_eq!(
                hashes_per_tick,
                params.hashes_per_tick(),
                "snapshot slot-time hashes_per_tick mismatch"
            );
        }
        assert_eq!(
            self.entry_bytes_budget().slot_limit(),
            params.max_entry_bytes_per_slot(),
            "snapshot slot-time entry byte budget mismatch"
        );
    }
```

**File:** runtime/src/bank.rs (L4955-4986)
```rust
    /// This is called from genesis and snapshot restore
    fn apply_activated_features(&mut self) {
        // Update active set of reserved account keys which are not allowed to be write locked
        self.reserved_account_keys = {
            let mut reserved_keys = ReservedAccountKeys::clone(&self.reserved_account_keys);
            reserved_keys.update_active_set(&self.feature_set);
            Arc::new(reserved_keys)
        };

        // Many fields are not serialized in snapshot or any configs. Rebuild
        // them from the feature set so the initial bank state is consistent.
        self.refresh_slot_params();
        self.apply_slot_time_runtime_changes();
        self.apply_simd_0339_invoke_cost_changes();

        let program_runtime_environment =
            self.create_program_runtime_environment(&self.feature_set);
        self.transaction_processor
            .global_program_cache
            .write()
            .unwrap()
            .latest_root_slot = self.slot;
        self.transaction_processor
            .epoch_boundary_preparation
            .write()
            .unwrap()
            .upcoming_epoch = self.epoch;
        self.transaction_processor.program_runtime_environment = program_runtime_environment;

        // Load all active built-in programs after the program runtime environment has been initialized
        self.add_active_builtin_programs();
    }
```

**File:** runtime/src/bank.rs (L6121-6127)
```rust
    fn compute_and_apply_new_feature_activations(&mut self) {
        let include_pending = true;
        let (feature_set, new_feature_activations) =
            self.compute_active_feature_set(include_pending);
        self.feature_set = Arc::new(feature_set);
        self.refresh_slot_params();

```
