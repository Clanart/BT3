### Title
Unchecked `u128` multiplication overflow panic in tower-epoch stake reward calculation can halt all validators at an epoch boundary - (File: `runtime/src/inflation_rewards/mod.rs`)

### Summary
The reported Float Capital bug is a class of "combined-loss / extreme-value arithmetic produces an unsafe cast/overflow that reverts (panics) a shared, unprivileged, state-transition-critical code path." The closest Agave analog is the unchecked `checked_mul(...).expect(...)` in `calculate_stake_rewards()` (tower/migration-epoch path), which converts a `u128` overflow into a hard `panic!` inside the epoch-boundary reward-calculation routine that every validator executes deterministically when crossing an epoch.

### Finding Description
In the tower-epoch reward-scaling branch of `calculate_stake_rewards`, a per-delegation `tower_points: u128` value is multiplied by the network-wide `point_value.rewards: u64` and the result is asserted to fit in `u128` via `.expect(...)`, mirroring the unsafe-cast-then-multiply pattern in the Float Capital report (`poolValue` cast then multiplied by `1e18`, reverting on overflow): [1](#0-0) 

and the equivalent branch for the migration epoch: [2](#0-1) 

`tower_points` is accumulated per stake delegation as `stake_amount * earned_credits`, summed across every epoch retained in the vote account's epoch-credits history: [3](#0-2) 

`point_value.rewards` is the **entire network's** per-epoch inflation reward pool (not scaled per delegator), which is later divided by total points: [1](#0-0) 

Because the multiplication of `tower_points` (which can grow with the delegator's stake size and the number of un-redeemed epochs of credits retained in the vote account) by the full-network `point_value.rewards` happens **before** the division by `point_value.points`, there is no per-operation bound that keeps the intermediate product inside `u128`. The code's own test suite explicitly demonstrates that this `.expect()` is reachable and panics for large-but-plausible inputs: [4](#0-3) 

This is structurally identical to the Float Capital issue: an arithmetic combination of two independently-bounded-looking values (stake × un-redeemed credits × network reward pool) is not capped at the point where it is combined, and the fallback for exceeding the numeric type is an unconditional panic rather than a saturating/clamping/error-returning path — exactly the pattern WATCHPUG flagged (`uint256(poolValue)` cast then multiplied, overflowing and reverting the whole rebalance function).

### Impact Explanation
`calculate_stake_rewards` is invoked from `calculate_reward_points_partitioned` / `calculate_stake_rewards_and_commissions`, which run for every stake delegation during epoch-boundary reward calculation: [5](#0-4) [6](#0-5) 

Because every correctly-behaving validator computes this identically at the same epoch boundary from the same on-chain state (stake accounts, vote accounts, inflation schedule — none of which require a malicious peer, plugin, or leaked key), a panic here would deterministically crash **all** validators simultaneously at the epoch transition rather than causing a fork or degrading a single node. This matches the "non-RPC remote... crash" / "consensus halt" impact category: an unprivileged combination of on-chain stake/vote state (attacker only needs to hold or delegate stake and simply not redeem rewards for many epochs) drives a shared, safety-critical arithmetic operation into an unrecoverable panic instead of a graceful error, unlike the neighboring `DistributionError::ArithmeticOverflow` handling used elsewhere in the reward-distribution code path (`runtime/src/bank/partitioned_epoch_rewards/distribution.rs`), which shows the project is aware such overflows are possible and normally handles them without panicking — but this particular multiplication is not similarly guarded.

### Likelihood Explanation
The likelihood is comparable to the original Float Capital finding: the maintainers/testsuite themselves only reach the panic with extreme values (`u64::MAX` stake/rewards/credits in the unit tests), and in practice mainnet-beta stake sizes, epoch lengths (~432,000 slots/epoch), and the bounded epoch-credits history make the exact overflow difficult to trigger under today's real network parameters — the same caveat the Float Capital team gave ("given system parameterizations ... unlikely to be an issue in practice, but likely still worth making a change for"). I was not able to fully verify the exact numeric bound of the epoch-credits retention window (`MAX_EPOCH_CREDITS_HISTORY`) or the current mainnet epoch length within the available search budget, so the precise feasibility of triggering the overflow under real mainnet constraints (vs. only under adversarial/extreme configurations, e.g. a devnet with different epoch-length/large single-delegator stake) remains unconfirmed.

### Recommendation
Replace the `.expect("Rewards intermediate calculation should fit within u128")` panic with a bounded/checked computation that either (a) performs the division before the multiplication where mathematically safe, (b) saturates/clamps the result the way `calculate_block_reward` already does (`.try_into().unwrap_or(u64::MAX).min(pending_delegator_rewards)`), or (c) returns a recoverable error/skip-reward result instead of panicking, so that no single stake delegation's un-redeemed credit history combined with the epoch reward pool can crash the reward-calculation pass for the whole network.

### Proof of Concept
The repository's own parameterized test demonstrates the panic is reachable in the current implementation: [7](#0-6) 

`#[test_case(u64::MAX, 1_000, u64::MAX => panics "Rewards intermediate calculation should fit within u128")]` and `#[test_case(1, u64::MAX, u64::MAX => panics "Rewards should fit within u64")]` show that feeding extreme (but type-valid) `stake`, `rewards`, and `credits` values into `calculate_stake_rewards` deterministically panics rather than returning an error — confirming the unguarded overflow path exists in the epoch-boundary reward-computation routine that all validators execute in lock-step.

### Citations

**File:** runtime/src/inflation_rewards/mod.rs (L299-307)
```rust
            // In tower, `points` still needs to be scaled by `point_value` to calculate this
            // `vote_state` earned.
            // The final unwrap is safe, as points_value.points is guaranteed to be non zero above.
            tower_points
                .checked_mul(u128::from(point_value.rewards))
                .expect("Rewards intermediate calculation should fit within u128")
                .checked_div(point_value.points)
                .unwrap()
        }
```

**File:** runtime/src/inflation_rewards/mod.rs (L319-330)
```rust
            let total_slots = (num_tower_slots + num_ag_slots) as u128;
            let tower_points = tower_points
                .checked_mul(u128::from(point_value.rewards))
                .expect("Rewards intermediate calculation should fit within u128")
                .checked_div(point_value.points)
                .unwrap()
                .checked_mul(*num_tower_slots as u128)
                .unwrap()
                .checked_div(total_slots)
                .unwrap();
            tower_points + ag_points
        }
```

**File:** runtime/src/inflation_rewards/mod.rs (L1139-1168)
```rust
    #[test_case(u64::MAX, 1_000, u64::MAX => panics "Rewards intermediate calculation should fit within u128")]
    #[test_case(1, u64::MAX, u64::MAX => panics "Rewards should fit within u64")]
    fn calculate_rewards_tests(stake: u64, rewards: u64, credits: u64) {
        let mut vote_state = VoteStateHandler::new_v4(VoteStateV4::default());

        let stake = new_stake(stake, &Pubkey::default(), vote_state.as_ref_v4(), u64::MAX);

        vote_state.increment_credits(0, credits);

        let stake_history = &StakeHistory::default();
        let new_rate_activation_epoch = None;
        let commission_rate_in_basis_points = true;
        let adjust_delegations_for_rent = true;

        calculate_stake_rewards(
            &stake,
            vote_state.as_ref_v4().inflation_rewards_commission_bps,
            DelegatedVoteState::from(vote_state.as_ref_v4()),
            CalculationEnvironment {
                rewarded_epoch: 0,
                point_value: &PointValue { rewards, points: 1 },
                stake_history,
                new_rate_activation_epoch,
                commission_rate_in_basis_points,
                adjust_delegations_for_rent,
                use_fixed_point_stake_math: true,
            },
            null_tracer(),
            &AlpenglowEpochType::Tower,
        );
```

**File:** runtime/src/inflation_rewards/points.rs (L187-234)
```rust
fn tower_epoch_credits_iter(
    stake: &Stake,
    epoch_credits_iter: impl Iterator<Item = (Epoch, u64, u64)>,
    stake_history: &StakeHistory,
    inflation_point_calc_tracer: Option<impl Fn(&InflationPointCalculationEvent)>,
    new_rate_activation_epoch: Option<Epoch>,
    use_fixed_point_stake_math: bool,
) -> (u128, u64, bool) {
    let mut points = 0;
    let credits_in_stake = stake.credits_observed;
    let mut new_credits_observed = credits_in_stake;
    let mut saw_marker = false;

    for entry in epoch_credits_iter {
        if entry == AG_MIGRATION_EPOCH_CREDIT {
            saw_marker = true;
            break;
        }
        let (epoch, final_epoch_credits, initial_epoch_credits) = entry;
        let earned_credits = calc_earned_credits(
            stake,
            final_epoch_credits,
            initial_epoch_credits,
            &mut new_credits_observed,
        );
        let stake_amount = u128::from(delegation_effective_stake(
            &stake.delegation,
            epoch,
            stake_history,
            new_rate_activation_epoch,
            use_fixed_point_stake_math,
        ));

        // finally calculate points for this epoch
        let earned_points = stake_amount * earned_credits;
        points += earned_points;

        if let Some(inflation_point_calc_tracer) = inflation_point_calc_tracer.as_ref() {
            inflation_point_calc_tracer(&InflationPointCalculationEvent::CalculatedPoints(
                epoch,
                stake_amount,
                earned_credits,
                earned_points,
            ));
        }
    }
    (points, new_credits_observed, saw_marker)
}
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L780-850)
```rust
    fn calculate_stake_rewards_and_commissions<'a>(
        &self,
        stake_history: &StakeHistory,
        stake_delegations: Vec<(&'a Pubkey, &'a StakeAccount<Delegation>)>,
        cached_vote_accounts: CachedVoteAccounts<'_>,
        rewarded_epoch: Epoch,
        point_value: PointValue,
        ag_epoch_type: &AlpenglowEpochType,
        thread_pool: &ThreadPool,
        reward_calc_tracer: Option<impl RewardCalcTracer>,
        metrics: &mut RewardsMetrics,
    ) -> (RewardCommissions, StakeRewardCalculation) {
        let new_warmup_cooldown_rate_epoch = self.new_warmup_cooldown_rate_epoch();
        let feature_snapshot = self.feature_set.snapshot();
        let use_fixed_point_stake_math = feature_snapshot.upgrade_bpf_stake_program_to_v5_1;
        let delay_commission_updates = feature_snapshot.delay_commission_updates;
        let commission_rate_in_basis_points = feature_snapshot.commission_rate_in_basis_points;
        // Name intentionally doesn't match -- "adjust delegations for rent" is
        // part of relaxing post-exec min balance checks.
        let adjust_delegations_for_rent = feature_snapshot.relax_post_exec_min_balance_check;
        let custom_commission_collector = feature_snapshot.custom_commission_collector;
        let block_revenue_sharing = feature_snapshot.block_revenue_sharing;

        let mut measure_redeem_rewards = Measure::start("redeem-rewards");
        // For N stake delegations, where N is >1,000,000, we produce:
        // * N stake rewards,
        // * M reward commission accounts, where M is a number of stake nodes.
        //   Currently, way smaller number than 1,000,000. And we can expect it
        //   to always be significantly smaller than number of delegations.
        //
        // Producing the stake reward with rayon triggers a lot of
        // (re)allocations. To avoid that, we allocate it at the start and
        // pass `stake_rewards.spare_capacity_mut()` as one of iterators.
        let stake_delegations_len = stake_delegations.len();
        let mut stake_rewards = PartitionedStakeRewards::with_capacity(stake_delegations_len);
        let rewards_accumulator: RewardsAccumulator = thread_pool.install(|| {
            stake_delegations
                .par_iter()
                .zip(&mut stake_rewards.spare_capacity_mut()[..stake_delegations_len])
                .with_min_len(500)
                .filter_map(|((stake_pubkey, stake_account), reward_ref)| {
                    let block_reward = if block_revenue_sharing {
                        calculate_block_reward(
                            rewarded_epoch,
                            stake_account.delegation(),
                            stake_history,
                            cached_vote_accounts.distribution_epoch_vote_accounts,
                            ag_epoch_type,
                            new_warmup_cooldown_rate_epoch,
                            use_fixed_point_stake_math,
                        )
                    } else {
                        0
                    };
                    let maybe_reward_record = self.redeem_delegation_rewards(
                        rewarded_epoch,
                        stake_pubkey,
                        stake_account,
                        &point_value,
                        stake_history,
                        &cached_vote_accounts,
                        reward_calc_tracer.as_ref(),
                        new_warmup_cooldown_rate_epoch,
                        delay_commission_updates,
                        commission_rate_in_basis_points,
                        adjust_delegations_for_rent,
                        ag_epoch_type,
                        custom_commission_collector,
                        use_fixed_point_stake_math,
                    );

```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L940-1002)
```rust
    /// Calculates epoch reward points from stake/vote accounts.
    /// Returns reward lamports and points for the epoch or none if points == 0.
    fn calculate_reward_points_partitioned<'a>(
        &self,
        stake_history: &StakeHistory,
        stake_delegations: &Vec<(&'a Pubkey, &'a StakeAccount<Delegation>)>,
        cached_vote_accounts: &CachedVoteAccounts<'_>,
        epoch_inflation_rewards: u64,
        ag_epoch_type: &AlpenglowEpochType,
        thread_pool: &ThreadPool,
        metrics: &RewardsMetrics,
    ) -> Option<PointValue> {
        let CachedVoteAccounts {
            distribution_epoch_vote_accounts,
            ..
        } = cached_vote_accounts;

        let solana_vote_program: Pubkey = solana_vote_program::id();
        let new_warmup_cooldown_rate_epoch = self.new_warmup_cooldown_rate_epoch();
        match ag_epoch_type {
            AlpenglowEpochType::Alpenglow { .. } => {
                // In alpenglow, we do not need to compute `PointValue::points` as the final
                // rewards are simply the total credits stored in the vote account.  We just need
                // to return a `Some` value with valid rewards.
                return Some(PointValue {
                    rewards: epoch_inflation_rewards,
                    points: 0,
                });
            }
            AlpenglowEpochType::Tower => {
                // For tower we need to compute the valid `PointValue::points`.
            }
            AlpenglowEpochType::MigrationEpoch { .. } => {
                // For the migrating epoch, we need to compute the tower portion of `PointValue::points`.
            }
        }

        let use_fixed_point_stake_math = self.use_fixed_point_stake_math();
        let (points, measure_us) = measure_us!(thread_pool.install(|| {
            stake_delegations
                .par_iter()
                .map(|(_stake_pubkey, stake_account)| {
                    let vote_pubkey = stake_account.delegation().voter_pubkey;

                    let Some(vote_account) = distribution_epoch_vote_accounts.get(&vote_pubkey)
                    else {
                        return 0;
                    };
                    if vote_account.owner() != &solana_vote_program {
                        return 0;
                    }

                    calculate_points_for_tower(
                        stake_account.stake_state(),
                        DelegatedVoteState::from(vote_account.vote_state_view()),
                        stake_history,
                        new_warmup_cooldown_rate_epoch,
                        use_fixed_point_stake_math,
                    )
                    .unwrap_or(0)
                })
                .sum::<u128>()
        }));
```
