## Finding

The Agave analog to TRST‑M‑10 is in `runtime/src/bank.rs`: when a `Bank` is created from a parent whose epoch is more than one epoch behind the new bank's epoch, the epoch-boundary reward logic still only computes and distributes inflation for **exactly one epoch** — the immediate parent epoch — no matter how many epochs actually elapsed.

### Title
Inflation rewards are computed for only one epoch regardless of how many epochs actually elapsed between parent and child banks - (`runtime/src/bank.rs`)

### Summary
`Bank::prepare_for_block_execution` triggers epoch-boundary processing with a simple `if parent_epoch < self.epoch()` check, then calls `process_new_epoch(parent_epoch, ...)` exactly once, using `parent_epoch` as the sole `rewarded_epoch`, independent of the actual epoch gap. `calculate_epoch_inflation_rewards` scales the payout only by the duration of that single `rewarded_epoch`. If more than one epoch elapses in a single parent→child hop (e.g. due to a long stretch of skipped/leaderless slots spanning multiple epoch boundaries, or via `warp_from_parent`/`new_from_parent` jumping far ahead), all epochs strictly between `parent_epoch` and the new epoch are silently dropped from the inflation-reward calculation — mirroring the TRST‑M‑10 pattern of not scaling emissions to the number of periods actually passed.

### Finding Description
`_new_from_parent`/`prepare_for_block_execution` decides whether to run epoch-boundary processing purely from a boolean comparison: [1](#0-0) 

`process_new_epoch` is invoked with `parent_epoch` as the single value used to derive `rewarded_epoch` throughout the whole pipeline (`compute_new_epoch_caches_and_rewards` → `calculate_rewards` → `calculate_rewards_for_partitioning`): [2](#0-1) 

The actual reward budget for that epoch is computed from a single epoch's duration in years, not the elapsed epoch gap: [3](#0-2) [4](#0-3) 

And `calculate_rewards_for_partitioning` feeds exactly this single-epoch inflation figure into the point-value used to pay every stake account for that boundary crossing: [5](#0-4) 

There is no code path anywhere in this pipeline that iterates or sums over `self.epoch() - parent_epoch` epochs; `rewarded_epoch` is always `parent_epoch`, a single value. The comment on `warp_from_parent` explicitly acknowledges that a child bank's parent "could be millions of slots in the past" — i.e., many epochs away — yet it delegates directly to `Bank::new_from_parent`, which goes through the very same single-epoch `process_new_epoch` path: [6](#0-5) 

The broken invariant: the protocol implicitly assumes at most one epoch boundary is crossed per parent→child bank transition (an assumption baked into the `if parent_epoch < self.epoch()` boolean and the single `rewarded_epoch = parent_epoch`). Nothing in `_new_from_parent`, `prepare_for_block_execution`, or `process_new_epoch` asserts or enforces `self.epoch() == parent_epoch + 1`; the code silently degrades to "reward exactly one epoch" whenever that assumption is violated.

### Impact Explanation
When multiple epochs elapse between a parent bank and its child bank (this happens naturally on Solana whenever a long run of consecutive slots produce no block — slot numbers, and therefore epoch boundaries, keep advancing via the clock/`PoH` even with no blocks, and blockstore parent-child linkage simply uses the next actually-produced slot), all inflation/stake-reward accrual for the intermediate, skipped epochs is permanently lost:
- Capitalization is only inflated for one epoch's worth of `slots_per_year`-scaled duration instead of the true elapsed time.
- Stake/vote accounts receive commissions and rewards computed off a single epoch's `point_value`, silently forfeiting the inflation budget that should have accrued for the other skipped epochs.
- Because `capitalization`, `stake_history`, and `epoch_stakes` are advanced only once per such transition, this is not a recoverable/retryable computation — the missed epochs' rewards are gone for good, understating economic emissions and stake payouts across the whole validator set, similar in spirit to how TRST‑M‑10 caused per-period reward flattening regardless of elapsed periods.

### Likelihood Explanation
This does not require a malicious actor: it is triggered purely by network conditions (an extended stretch with no blocks produced, e.g. during a cluster-wide outage or a fork resolution delay) spanning more than one epoch boundary, which is a scenario Agave's own code anticipates (see the `warp_from_parent` doc comment about parents "millions of slots" behind). No special/trusted privileges, malicious peers, or admin actions are needed — an ordinary, unprivileged sequence of skipped slots is sufficient to hit the single-epoch reward path.

### Recommendation
`process_new_epoch`/`prepare_for_block_execution` should handle the case where `self.epoch() > parent_epoch + 1` explicitly: either iterate reward calculation over every skipped epoch (`parent_epoch..self.epoch()`, each with its own capitalization/duration snapshot), or scale `calculate_epoch_inflation_rewards` by the aggregate duration of all elapsed epochs rather than assuming a single epoch transition. At minimum, add an explicit assertion/metrics signal when more than one epoch is skipped so the discrepancy is visible and can be reconciled rather than silently dropped.

### Proof of Concept
1. Start a bank at slot `S0` in epoch `E`.
2. Advance the *clock/slot* (not necessarily by producing intervening blocks) so the next produced child bank's slot `S1` falls in epoch `E + k` for `k > 1` (e.g., via `Bank::new_from_parent(parent, leader, S1)` or `Bank::warp_from_parent(parent, leader, S1)`, as already exercised in `test_alpenglow_partitioned_rewards_use_epoch_start_budget_after_burn`, which itself warps two epochs at once using `SLOTS_PER_EPOCH.saturating_mul(2)`).
3. Observe that `prepare_for_block_execution` still calls `process_new_epoch(parent_epoch, ...)` exactly once (`parent_epoch = E`), and `calculate_epoch_inflation_rewards` is invoked with `rewarded_epoch = E` only: [7](#0-6) 

4. Compare the capitalization increase against what should have accrued for `k` full epochs of inflation — the bank only mints/distributes rewards for one epoch's worth, permanently omitting the other `k-1` epochs' emissions.

### Citations

**File:** runtime/src/bank.rs (L1815-1846)
```rust
    /// process for the start of a new epoch
    fn process_new_epoch(
        &mut self,
        parent_epoch: Epoch,
        parent_slot: Slot,
        parent_capitalization: u64,
        parent_height: u64,
        reward_calc_tracer: Option<impl RewardCalcTracer>,
    ) {
        let epoch = self.epoch();
        let slot = self.slot();
        let thread_pool = rewards_calculation_thread_pool();

        let (_, apply_feature_activations_time_us) = measure_us!(
            thread_pool.install(|| { self.compute_and_apply_new_feature_activations() })
        );

        let mut rewards_metrics = RewardsMetrics::default();
        let NewEpochBundle {
            stake_history,
            unfiltered_distribution_vote_accounts,
            delegated_stakes,
            filtered_distribution_vote_accounts,
            rewards_calculation,
            calculate_activated_stake_time_us,
            update_rewards_with_thread_pool_time_us,
        } = self.compute_new_epoch_caches_and_rewards(
            thread_pool,
            parent_epoch,
            reward_calc_tracer,
            &mut rewards_metrics,
        );
```

**File:** runtime/src/bank.rs (L1925-1950)
```rust
    /// Like `new_from_parent` but additionally:
    /// * Doesn't assume that the parent is anywhere near `slot`, parent could be millions of slots
    ///   in the past
    /// * Adjusts the new bank's tick height to avoid having to run PoH for millions of slots
    /// * Freezes the new bank, assuming that the user will `Bank::new_from_parent` from this bank
    pub fn warp_from_parent(parent: Arc<Bank>, leader: SlotLeader, slot: Slot) -> Self {
        parent.freeze();
        let parent_timestamp = parent.clock().unix_timestamp;
        let mut new = Bank::new_from_parent(parent, leader, slot);
        new.update_epoch_stakes(new.epoch_schedule().get_epoch(slot), None);
        new.tick_height.store(new.max_tick_height(), Relaxed);

        let mut clock = new.clock();
        clock.epoch_start_timestamp = parent_timestamp;
        clock.unix_timestamp = parent_timestamp;
        new.update_sysvar_account(&sysvar::clock::id(), |account| {
            create_account(
                &clock,
                new.inherit_specially_retained_account_fields(account),
            )
        });
        new.transaction_processor
            .fill_missing_sysvar_cache_entries(&new);
        new.freeze();
        new
    }
```

**File:** runtime/src/bank.rs (L1980-1995)
```rust
        // Following code may touch AccountsDb, requiring proper ancestors
        let (_, update_epoch_time_us) = measure_us!({
            if parent_epoch < self.epoch() {
                self.process_new_epoch(
                    parent_epoch,
                    parent_slot,
                    parent_capitalization,
                    parent_block_height,
                    reward_calc_tracer,
                );
            } else {
                // Save a snapshot of stakes for use in consensus and stake weighted networking
                let leader_schedule_epoch = self.epoch_schedule().get_leader_schedule_epoch(slot);
                self.update_epoch_stakes(leader_schedule_epoch, None);
            }
        });
```

**File:** runtime/src/bank.rs (L2911-2917)
```rust
    pub fn epoch_duration_in_years(&self, epoch: Epoch) -> f64 {
        // period: time that has passed as a fraction of a year, basically the length of
        //  an epoch as a fraction of a year
        //  calculated as: slots_elapsed / (slots / year)
        self.epoch_schedule().get_slots_in_epoch(epoch) as f64
            / self.slots_per_year_for_epoch(epoch)
    }
```

**File:** runtime/src/bank.rs (L2966-2977)
```rust
    /// For a given `capitalization` (total_supply in lamports) and `epoch`, returns the
    /// `epoch inflation rewards` in lamports.
    pub(crate) fn calculate_epoch_inflation_rewards(
        &self,
        capitalization: u64,
        epoch: Epoch,
    ) -> u64 {
        let slot_in_year = self.slot_in_year_for_inflation();
        let validator_rate = self.inflation.read().unwrap().validator(slot_in_year);
        let epoch_duration_in_years = self.epoch_duration_in_years(epoch);
        (validator_rate * capitalization as f64 * epoch_duration_in_years) as u64
    }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L482-495)
```rust
        let capitalization = self.capitalization();
        let epoch_inflation_rewards =
            if AlpenglowEpochType::is_alpenglow_or_migration_epoch(self, rewarded_epoch) {
                EpochInflationAccountState::new_from_bank(self)
                    .and_then(|state| state.inflation_rewards_for_epoch(rewarded_epoch))
                    .unwrap_or_else(|| {
                        panic!(
                            "Missing epoch inflation state for non-Tower reward epoch \
                             {rewarded_epoch}"
                        )
                    })
            } else {
                self.calculate_epoch_inflation_rewards(capitalization, rewarded_epoch)
            };
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L2863-2887)
```rust
        let bank = Bank::new_from_parent(
            bank,
            SlotLeader::default(),
            SLOTS_PER_EPOCH.saturating_mul(2),
        );
        assert_eq!(bank.epoch(), 2);

        let epoch_rewards = bank.get_epoch_rewards_sysvar();
        let EpochRewardStatus::Active(EpochRewardPhase::Calculation(calculation_status)) =
            &bank.epoch_reward_status
        else {
            panic!("{:?} not active calculation", bank.epoch_reward_status);
        };
        let stake_rewards = calculation_status
            .all_stake_rewards
            .enumerated_rewards_iter()
            .map(|(_index, reward)| reward.inflation.stake_reward)
            .sum::<u64>();
        assert_eq!(epoch_rewards.total_rewards, recorded_budget);
        assert_eq!(
            epoch_rewards.distributed_rewards + stake_rewards,
            recorded_payout,
            "every recorded reward lamport must still be paid"
        );
    }
```
