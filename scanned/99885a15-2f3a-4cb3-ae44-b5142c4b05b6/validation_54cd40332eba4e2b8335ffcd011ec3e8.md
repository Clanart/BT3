## Analysis Result

I found a plausible analog, but I was not able to fully verify it before running out of investigation budget, so I present it with explicit caveats rather than as a confirmed finding.

### Title
Per-delegation clamping of block-reward payout in `calculate_block_reward` can silently break the epoch-rewards-sysvar budget invariant - (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

### Summary
The external report's core defect is that a derived quantity (`z_proceed`) is computed from a scaling factor and never checked against the budget it is drawn from (`I`, the idle share reserve), so the code can pay out more than what is actually available/expected. The closest local analog is `calculate_block_reward`, which computes a per-stake-account share of a vote account's `pending_delegator_rewards` and explicitly *clamps* the result instead of asserting/enforcing that the sum of all per-delegation shares stays within the vote account's actual pending-reward budget.

### Finding Description
`calculate_block_reward` computes each delegation's share of a vote account's block-revenue-sharing pool as:
```
(pending_delegator_rewards as u128 * stake as u128 / total_active_stake as u128)
    .try_into()
    .unwrap_or(u64::MAX)
    .min(pending_delegator_rewards)
``` [1](#0-0) 

The code comment itself acknowledges the underlying invariant can be violated: *"During recalculation, if stake account has already received rewards, it's possible to have `stake > total_active_stake`... individual rewards look greater than the pending rewards. This is harmless in practice, but we clamp it just to be safe"* [2](#0-1) . This mirrors the report's pattern exactly: an intermediate value is derived from a ratio computed against a possibly-stale total, and instead of validating the invariant that the *sum* of all payouts cannot exceed the pool, the code clamps only the *individual* value to be `≤ pending_delegator_rewards` — there is no check anywhere that `Σ block_reward` across all delegations of a vote account stays within the vote account's actual `pending_delegator_rewards` balance at distribution time.

This calculation runs per-stake-delegation in parallel via `calculate_stake_rewards_and_commissions`, feeding into `PartitionedStakeReward.block_reward` [3](#0-2) , which is later minted into stake accounts during `build_updated_stake_reward` via `checked_add_lamports` [4](#0-3) , and the running total is only asserted against the total `commission + stake_rewards` budget in `distribute_reward_commissions`, not against per-vote-account block-reward budgets [5](#0-4) .

The report's third recommendation ("check `z_proceed ≤ I`") has no counterpart here: nothing verifies that the sum of individual clamped `block_reward` values for a single vote account does not exceed that vote account's actual `pending_delegator_rewards` recorded on-chain, especially under the recalculation path (`recalculate_stake_rewards`) where `total_active_stake` can legitimately diverge from the value used at initial calculation time [6](#0-5) .

### Impact Explanation
I could not confirm within the available investigation whether this discrepancy is actually exploitable to cause fund creation/over-mint, because I was unable to fully trace how/when `pending_delegator_rewards` on the vote account itself is decremented relative to this distribution path (this lives in `programs/vote/src/vote_state/mod.rs`, which I located but did not have time to read in full). If the vote-program-side decrement of `pending_delegator_rewards` is done independently and doesn't reconcile with the sum of clamped per-delegation shares computed here, this could result in either (a) stake accounts receiving less than their fair share (rounding/clamping loss, low severity) or (b) in more contrived divergence scenarios between calculation-time and distribution-time `total_active_stake`, a mismatch between what is debited from the vote account and what is credited to stake accounts.

### Likelihood Explanation
Unknown/low confidence. The comment explicitly says the scenario "is harmless in practice," and the clamp does bound the per-delegation value to `pending_delegator_rewards`, which limits blast radius for a single delegation. Whether the aggregate-over-budget scenario is actually reachable requires cross-referencing the vote program's debit logic for `pending_delegator_rewards` (`programs/vote/src/vote_state/mod.rs`, `programs/vote/src/vote_processor.rs`), which I did not have remaining budget to fully verify.

### Recommendation
Add an aggregate check (analogous to the report's `z_proceed ≤ I` recommendation): sum the `block_reward` values computed per vote account within `calculate_stake_rewards_and_commissions` and assert/clamp the aggregate against the vote account's actual `pending_delegator_rewards`, rather than relying solely on the per-delegation `.min(pending_delegator_rewards)` clamp.

### Proof of Concept
Not constructed — I was not able to verify the vote-program-side debit path within the available tool budget, so I cannot demonstrate a concrete lamport-creation or fund-loss scenario. This should be verified against `programs/vote/src/vote_state/mod.rs` and `programs/vote/src/vote_processor.rs` (where `pending_delegator_rewards` is mutated) before treating this as a confirmed vulnerability rather than a plausible analog.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L211-231)
```rust
    if total_active_stake == 0 {
        0
    } else {
        let stake = delegation_effective_stake(
            delegation,
            rewarded_epoch,
            stake_history,
            new_warmup_cooldown_rate_epoch,
            use_fixed_point_stake_math,
        );
        // During recalculation, if stake account has already received rewards,
        // it's possible to have `stake > total_active_stake`. If
        // `pending_delegator_rewards` is a huge number, we could potentially
        // overflow a `u64`. We can also have individual rewards look greater
        // than the pending rewards. This is harmless in practice, but we
        // clamp it just to be safe
        (pending_delegator_rewards as u128 * stake as u128 / total_active_stake as u128)
            .try_into()
            .unwrap_or(u64::MAX)
            .min(pending_delegator_rewards)
    }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L397-408)
```rust
        // verify that we didn't pay any more than we expected to
        assert!(
            point_value.rewards
                >= distributed_lamports
                    + distributed_to_incinerator_lamports
                    + burned_lamports
                    + total_stake_rewards_lamports,
            "point_value={point_value:?}, distributed_lamports={distributed_lamports}, \
             distributed_to_incinerator_lamports={distributed_to_incinerator_lamports} \
             burned_lamports={burned_lamports}, \
             total_stake_rewards_lamports={total_stake_rewards_lamports}"
        );
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L820-833)
```rust
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1038-1094)
```rust
    fn recalculate_stake_rewards(
        &self,
        epoch_rewards_sysvar: &EpochRewards,
        thread_pool: &ThreadPool,
    ) -> (Arc<PartitionedStakeRewards>, Vec<Vec<usize>>) {
        assert!(epoch_rewards_sysvar.active);
        // If rewards are active, the rewarded epoch is always the immediately
        // preceding epoch.
        let rewarded_epoch = self.epoch().saturating_sub(1);

        let point_value = PointValue {
            rewards: epoch_rewards_sysvar.total_rewards,
            points: epoch_rewards_sysvar.total_points,
        };

        let stakes = self.stakes_cache.stakes();
        let EpochRewardCalculateParamInfo {
            stake_history,
            stake_delegations,
            cached_vote_accounts,
        } = self.get_epoch_params_for_recalculation(rewarded_epoch, &stakes);
        let ag_epoch_type = AlpenglowEpochType::get(self, rewarded_epoch, || {
            RewardEpochDelegatedStakes::get(self)
        });

        // On recalculation, only the `StakeRewardCalculation::stake_rewards`
        // field is relevant. It is assumed that reward commission accounts have
        // already been calculated and delivered, while
        // `StakeRewardCalculation::total_rewards` only reflects rewards that
        // have not yet been distributed.
        //
        // NOTE: the `RewardCommissionAccounts` will NOT have a correct
        // post_lamport amount if the commission account is NOT the vote account,
        // because the commission account is loaded from the current bank, and
        // not the start of the epoch. We don't have a snapshot of all commission
        // accounts from the start of the epoch. For this reason, the
        // `RewardCommissionAccounts` calculated in this function call should
        // NOT be used ever.
        let (_, StakeRewardCalculation { stake_rewards, .. }) = self
            .calculate_stake_rewards_and_commissions(
                &stake_history,
                stake_delegations,
                cached_vote_accounts,
                rewarded_epoch,
                point_value,
                &ag_epoch_type,
                thread_pool,
                null_tracer(),
                &mut RewardsMetrics::default(), // This is required, but not reporting anything at the moment
            );
        drop(stakes);
        let partition_indices = hash_rewards_into_partitions(
            &stake_rewards,
            &epoch_rewards_sysvar.parent_blockhash,
            epoch_rewards_sysvar.num_partitions as usize,
        );
        (stake_rewards, partition_indices)
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L262-267)
```rust
        account
            .checked_add_lamports(partitioned_stake_reward.inflation.stake_reward)
            .map_err(|_| DistributionError::ArithmeticOverflow)?;
        account
            .checked_add_lamports(partitioned_stake_reward.block_reward)
            .map_err(|_| DistributionError::ArithmeticOverflow)?;
```
