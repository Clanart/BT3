Based on my research, the closest structural analog to the Solidity gauge-rewards bug is in Agave's partitioned epoch-rewards pipeline, where a similar "no participation in the period → rewards silently discarded" pattern exists.

### Title
Epoch inflation reward budget is silently discarded when total stake reward points are zero for an epoch - (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

### Summary
The external report describes a Solidity gauge system where `_claimablePerPeriod` divides rewards by `totalVotesPerPeriod[period]`, and when that denominator is effectively zero for a period (no votes yet, e.g. a freshly created gauge), the notified rewards for that period become unrecoverable rather than being redistributed or refunded. Agave's Tower-epoch reward-points calculation has the same shape: rewards for an epoch are scaled by `points`, and when `points == 0` the entire computed inflation budget for that epoch is thrown away instead of being carried forward or refunded to the treasury/incinerator.

### Finding Description
`calculate_reward_points_partitioned` computes the total point value for an epoch, and explicitly returns `None` if the accumulated `points` sum to zero: [1](#0-0) 

This is exercised directly by the test `test_rewards_point_calculation_empty`, which asserts `point_value.is_none()` for a bank with no stake delegations that have earned points: [2](#0-1) 

That `None` propagates up through `calculate_validator_rewards` (`.map(...)`) into `calculate_rewards_for_partitioning`, where the result is unwrapped with `unwrap_or_default()`: [3](#0-2) 

Crucially, `epoch_inflation_rewards` (the epoch's inflation budget, computed *before* this fallback) was already calculated from capitalization for the non-Alpenglow/Tower path: [4](#0-3) 

When `.unwrap_or_default()` fires, `stake_rewards` becomes an empty `PartitionedStakeRewards` with `total_stake_rewards_lamports == 0`, and `reward_commissions` becomes empty — the computed `epoch_inflation_rewards` value is never referenced again for that epoch's distribution. There is no mechanism analogous to a "stuckEmissionsRecovery" step that later reclaims or re-attempts that budget; it is simply absent from `distribute_reward_commissions`/`begin_partitioned_rewards`'s downstream accounting, exactly the pattern the report calls out (rewards computed for a period, but the period ends up with no distributable recipients and the funds are effectively lost from that epoch's payout).

The "first period, no votes yet" precondition in the Solidity report maps to "epoch where accumulated Tower `points` across all stake delegations is zero" here — this is the sum in `calculate_reward_points_partitioned`, which is zero only when either there are no stake delegations, or none of them found a matching, correctly-owned vote account, or `calculate_points_for_tower` returned 0/`None` for all of them (e.g., vote credits not yet advanced relative to `credits_observed`).

### Impact Explanation
If this path is reached, the epoch's inflation reward budget is not credited to any validator/stake account and capitalization does not increase by that amount for the epoch — a form of reward loss/non-issuance, matching the "low risk" acknowledged classification of the original report. It does not directly cause fund theft, but it is a genuine invariant break in the reward-accounting flow (a computed, expected value is discarded without being accounted for or recovered), and the `assert!` in `distribute_reward_commissions` that reconciles `point_value.rewards` against distributed/burned/stake totals depends on `point_value` reflecting the *actual* rewards, which it does not in this fallback branch (default `PointValue` is used, not the real `epoch_inflation_rewards`). I could not fully confirm from the indexed code whether this specific `unwrap_or_default()` combination can ever cause the assertion in `distribute_reward_commissions` to fail (which would be validator-crashing/consensus-relevant) versus merely resulting in silently skipped inflation for the epoch — this needs direct testing/tracing that a background agent with full build access should verify.

### Likelihood Explanation
Reaching a network-wide state where total Tower points are zero for an entire epoch requires essentially all active stake delegations to have zero newly-earned credits relative to their `credits_observed`, which in a live, healthy Agave cluster with active voting is extremely unlikely — it is not attacker-triggerable under the normal "malicious peer/validator" adversary model excluded by scope, since it requires the honest majority to stop earning credits network-wide (functionally equivalent to a network already halted). This significantly limits it as an "unprivileged" exploit path; it is more of a defensive-coding/edge-case gap than an actively exploitable vulnerability under the stated Valid Impact criteria.

### Recommendation
When `calculate_reward_points_partitioned` returns `None` (or more generally when the computed `epoch_inflation_rewards` ends up unused because `stake_rewards`/`point_value` default to zero), the code should not silently drop `epoch_inflation_rewards`. Instead, either: (1) do not mint/allocate the inflation budget for that epoch until points are confirmed non-zero, or (2) explicitly account for and log/metric the unclaimed inflation amount so it is provably neither paid nor implicitly lost from future budgeting, mirroring the reporter's recommendation to make "stuck" rewards recoverable rather than unreachable.

### Proof of Concept
A conceptual reproduction (not run against a live cluster, since this requires unit/integration testing infrastructure):
1. Construct a `Bank` in the Tower/legacy inflation path with stake delegations whose `credits_observed` already matches or exceeds their vote account's `credits` (i.e., no new credits earned this epoch) for every delegation — reproducible via the existing test harness pattern in `test_rewards_point_calculation_empty`. [5](#0-4) 
2. Call `calculate_rewards_for_partitioning` for that epoch and observe that `stake_rewards.total_stake_rewards_lamports == 0` and `reward_commissions` is empty, even though `epoch_inflation_rewards` (from `calculate_epoch_inflation_rewards`) was computed as a positive value moments earlier.
3. Confirm (via `distribute_reward_commissions`/`begin_partitioned_rewards`) that this positive `epoch_inflation_rewards` value is not distributed to any account and is not otherwise recorded as deferred/recoverable for a future epoch.

Because I was unable to execute the actual test suite in this environment, the exact downstream behavior of the `assert!` in `distribute_reward_commissions` under this specific `unwrap_or_default()` scenario should be independently verified with a live build/test run before treating this as more than an informational finding.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L483-495)
```rust
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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L502-518)
```rust
        let CalculateValidatorRewardsResult {
            reward_commissions,
            stake_reward_calculation: stake_rewards,
            point_value,
        } = self
            .calculate_validator_rewards(
                stake_history,
                stake_delegations,
                cached_vote_accounts,
                rewarded_epoch,
                epoch_inflation_rewards,
                reward_epoch_delegated_stakes,
                reward_calc_tracer,
                thread_pool,
                metrics,
            )
            .unwrap_or_default();
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1005-1008)
```rust
        (points > 0).then_some(PointValue {
            rewards: epoch_inflation_rewards,
            points,
        })
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1588-1618)
```rust
    #[test]
    fn test_rewards_point_calculation_empty() {
        agave_logger::setup();

        // bank with no rewards to distribute
        let (genesis_config, _mint_keypair) = create_genesis_config(LAMPORTS_PER_SOL);
        let bank = Bank::new_for_tests(&genesis_config);

        let thread_pool = ThreadPoolBuilder::new().num_threads(1).build().unwrap();
        let rewards_metrics: RewardsMetrics = RewardsMetrics::default();
        let expected_rewards = 100_000_000_000;
        let stakes: RwLockReadGuard<Stakes<StakeAccount<Delegation>>> = bank.stakes_cache.stakes();
        let rewarded_epoch = bank.epoch().saturating_sub(1);
        let EpochRewardCalculateParamInfo {
            stake_history,
            stake_delegations,
            cached_vote_accounts,
        } = bank.get_epoch_params_for_recalculation(rewarded_epoch, &stakes);

        let point_value = bank.calculate_reward_points_partitioned(
            &stake_history,
            &stake_delegations,
            &cached_vote_accounts,
            expected_rewards,
            &AlpenglowEpochType::Tower,
            &thread_pool,
            &rewards_metrics,
        );

        assert!(point_value.is_none());
    }
```
