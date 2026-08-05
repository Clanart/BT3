## Finding

Based on my research, the closest real analog to the PaymentSplit "shares recalculated after partial release" bug is in Agave's block-revenue (SIMD-0123) reward distribution, specifically `calculate_block_reward` in `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`.

### Title
Per-account clamp in `calculate_block_reward` does not bound the aggregate payout from a vote account's `pending_delegator_rewards` pool - ([File: runtime/src/bank/partitioned_epoch_rewards/calculation.rs])

### Summary
`calculate_block_reward` computes each stake account's share of a validator's block-revenue pool as `pending_delegator_rewards * stake / total_active_stake`, then clamps that *single* result to `.min(pending_delegator_rewards)` [1](#0-0) . The function's own comment acknowledges the exact "shares recalculated after partial release" failure mode from the report: "During recalculation, if stake account has already received rewards, it's possible to have `stake > total_active_stake`... individual rewards look greater than the pending rewards" [2](#0-1) . The fix only clamps the *per-stake-account* value against the full pool, it does not track or decrement how much of `pending_delegator_rewards` has already been allocated to previously-processed stake accounts within the same validator during the same distribution/recalculation pass.

### Finding Description
This mirrors the reported PaymentSplit bug class: a proportional-share pool (`pending_delegator_rewards`) is divided among "payees" (stake accounts delegated to a vote account) using a denominator (`total_active_stake`, taken from `reward_epoch_delegated_stakes`) that is fixed for the rewarded epoch [3](#0-2) . When recalculation occurs (e.g., due to reward distribution being recomputed across partitions/forks as `recalculate_partitioned_rewards_if_active` triggers, seen in tests exercising recalculated rewards) [4](#0-3) , individual delegations can have grown (compounded) such that `stake > total_active_stake`, causing the computed share for a single account to exceed the entire pool - which the code clamps to `pending_delegator_rewards` per-account.

Nowhere in the distribution path (`store_stake_accounts_in_partition`, `build_updated_stake_reward`, `distribute_epoch_rewards_in_partition`) did I find code that decrements the vote account's `pending_delegator_rewards` field or otherwise tracks cumulative block-reward payout against the pool as multiple stake accounts belonging to the same validator are processed [5](#0-4) [6](#0-5) . The only place `pending_delegator_rewards` is mutated is `add_pending_delegator_rewards` (increment on deposit) [7](#0-6)  - I found no corresponding decrement call anywhere in the codebase.

### Impact Explanation
If the aggregate block-reward payout across all of a validator's delegators is not bounded by the validator's actual `pending_delegator_rewards` balance, stake accounts could be credited with more lamports than the vote account ever deposited, analogous to the reported underflow/over-payment bug where "future payments might also be corrupted." Whether this results in actual fund creation depends on whether the vote account's lamports are debited elsewhere when `block_reward` is credited to stake accounts, an accounting step I could not locate within available context - this is a gap in my verification, not a confirmed absence.

### Likelihood Explanation
This path is only reachable when Alpenglow's `block_revenue_sharing` feature is active and requires a recalculation event (fork switch during partitioned distribution) plus compounding stake growth across multiple delegations to the same validator - a narrow but not attacker-controlled condition; it is a systemic accounting-completeness issue rather than a directly exploitable unprivileged attack primitive.

### Recommendation
Track cumulative block-reward lamports already allocated per vote account across the stake accounts processed in a distribution/recalculation pass, and clamp the aggregate (not just each individual account's share) to `pending_delegator_rewards`; additionally confirm and, if missing, add an explicit debit of `pending_delegator_rewards`/vote-account lamports corresponding to each block reward credited to a stake account.

### Proof of Concept
Not independently constructed - the analysis is based on the documented edge case in the `calculate_block_reward` comment and the absence of any aggregate-tracking or decrement logic found via search of the distribution/vote-state code paths [8](#0-7) .

**Caveat**: Due to codebase index size limits, I was unable to definitively confirm whether the vote account's lamports/`pending_delegator_rewards` are debited through some code path I did not locate (e.g., inside `redeem_delegation_rewards` or a related SVM/loader hook). If such a debit exists and is itself bounded correctly, the impact of this finding would be limited to metrics/accounting drift rather than fund creation. I recommend a Devin session with full repository access to trace the complete lamport flow for `block_reward` end-to-end before treating this as a confirmed high-severity issue.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L173-232)
```rust
/// Calculates block reward for a stake account based on SIMD-0123
fn calculate_block_reward(
    rewarded_epoch: Epoch,
    delegation: &Delegation,
    stake_history: &StakeHistory,
    distribution_epoch_vote_accounts: &VoteAccounts,
    ag_epoch_type: &AlpenglowEpochType,
    new_warmup_cooldown_rate_epoch: Option<Epoch>,
    use_fixed_point_stake_math: bool,
) -> u64 {
    let vote_pubkey = delegation.voter_pubkey;
    let Some(vote_account) = distribution_epoch_vote_accounts.get(&vote_pubkey) else {
        debug!("could not find vote account {vote_pubkey} in cache");
        return 0;
    };
    let vote_state = vote_account.vote_state_view();
    let pending_delegator_rewards = vote_state.pending_delegator_rewards();
    // NOTE: during recalculation, `distribution_epoch_vote_accounts` already
    // includes updated stake activation values from after the new epoch
    // calculation, so we need to use `RewardEpochDelegatedStakes` for the exact
    // values at the end of the reward epoch.
    let (AlpenglowEpochType::Alpenglow {
        reward_epoch_delegated_stakes,
        ..
    }
    | AlpenglowEpochType::MigrationEpoch {
        reward_epoch_delegated_stakes,
        ..
    }) = ag_epoch_type
    else {
        debug!("Alpenglow must be enabled for block reward calculation");
        return 0;
    };
    let total_active_stake = reward_epoch_delegated_stakes
        .delegated_stakes
        .get(&vote_pubkey)
        .copied()
        .unwrap_or(0);
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
}
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L2645-2663)
```rust
        // that they are the same.
        let recalculated_rewards =
            build_partitioned_stake_rewards(recalculated_rewards, partition_indices);
        assert_eq!(
            expected_starting_block_height,
            distribution_starting_block_height
        );
        assert_eq!(expected_stake_rewards.len(), recalculated_rewards.len());
        // First partition has already been distributed, so recalculation
        // returns 0 rewards
        assert_eq!(recalculated_rewards[0].num_rewards(), 0);
        let epoch_rewards_sysvar = bank.get_epoch_rewards_sysvar();
        let starting_index = (bank.block_height() + 1
            - epoch_rewards_sysvar.distribution_starting_block_height)
            as usize;
        compare_stake_rewards(
            &expected_stake_rewards[starting_index..],
            &recalculated_rewards[starting_index..],
        );
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L239-325)
```rust
    fn build_updated_stake_reward(
        distribution_epoch: u64,
        stake_history: &StakeHistory,
        new_warmup_cooldown_rate_epoch: Option<Epoch>,
        stakes_cache_accounts: &imbl::HashMap<Pubkey, StakeAccount<Delegation>>,
        partitioned_stake_reward: &PartitionedStakeReward,
        rent: &Rent,
        adjust_delegations_for_rent: bool,
        use_fixed_point_stake_math: bool,
    ) -> Result<StakeReward, DistributionError> {
        let stake_account = stakes_cache_accounts
            .get(&partitioned_stake_reward.stake_pubkey)
            .ok_or(DistributionError::AccountNotFound)?
            .clone();

        let (mut account, stake_state): (AccountSharedData, StakeStateV2) = stake_account.into();
        let StakeStateV2::Stake(meta, stake, flags) = stake_state else {
            // StakesCache only stores accounts where StakeStateV2::delegation().is_some()
            unreachable!(
                "StakesCache entry {:?} failed StakeStateV2 deserialization",
                partitioned_stake_reward.stake_pubkey
            )
        };
        account
            .checked_add_lamports(partitioned_stake_reward.inflation.stake_reward)
            .map_err(|_| DistributionError::ArithmeticOverflow)?;
        account
            .checked_add_lamports(partitioned_stake_reward.block_reward)
            .map_err(|_| DistributionError::ArithmeticOverflow)?;

        let mut new_stake = partitioned_stake_reward.inflation.stake;
        if adjust_delegations_for_rent {
            let minimum_balance = rent.minimum_balance(account.data().len());
            // The rewarded epoch is right before the distribution epoch
            let rewarded_epoch = distribution_epoch.saturating_sub(1);
            // The entry in `partitioned_stake_reward` contains the rewards,
            // calculated during the calculation phase
            let delegation_with_rewards = new_stake.delegation.stake;
            adjust_delegation_for_rent(
                &mut new_stake.delegation,
                rewarded_epoch,
                delegation_with_rewards,
                account.lamports(),
                minimum_balance,
            );
        } else {
            let expected_delegation = stake
                .delegation
                .stake
                .saturating_add(partitioned_stake_reward.inflation.stake_reward);
            assert_eq!(
                expected_delegation, new_stake.delegation.stake,
                "stake reward delegation must be consistent with the updated stake account \
                 lamport balance"
            );
        }
        account
            .set_state(&StakeStateV2::Stake(meta, new_stake, flags))
            .map_err(|_| DistributionError::UnableToSetState)?;

        let stake_at_distribution_epoch = delegation_effective_stake(
            &new_stake.delegation,
            distribution_epoch,
            stake_history,
            new_warmup_cooldown_rate_epoch,
            use_fixed_point_stake_math,
        );
        let reward_type = if stake_at_distribution_epoch == 0 {
            RewardType::DeactivatedStake
        } else {
            RewardType::Staking
        };
        Ok(StakeReward {
            stake_pubkey: partitioned_stake_reward.stake_pubkey,
            stake_reward_info: StakeRewardInfo {
                reward_type,
                lamports: i64::try_from(
                    partitioned_stake_reward.inflation.stake_reward
                        + partitioned_stake_reward.block_reward,
                )
                .unwrap(),
                post_balance: account.lamports(),
                commission_bps: partitioned_stake_reward.inflation.commission_bps,
            },
            stake_account: account,
        })
    }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L336-423)
```rust
    fn store_stake_accounts_in_partition(
        &self,
        partition_rewards: &StartBlockHeightAndPartitionedRewards,
        partition_index: u64,
    ) -> DistributionResults {
        let feature_snapshot = self.feature_set.snapshot();
        // Name intentionally doesn't match -- "adjust delegations for rent" is
        // part of relaxing post-exec min balance checks.
        let adjust_delegations_for_rent = feature_snapshot.relax_post_exec_min_balance_check;
        let use_fixed_point_stake_math = feature_snapshot.upgrade_bpf_stake_program_to_v5_1;

        let mut stake_reward_lamports_minted = 0;
        let mut stake_reward_lamports_burned = 0;
        let mut block_reward_lamports_distributed = 0;
        let mut block_reward_lamports_burned = 0;
        let indices = partition_rewards
            .partition_indices
            .get(partition_index as usize)
            .unwrap_or_else(|| {
                panic!(
                    "partition index out of bound: {partition_index} >= {}",
                    partition_rewards.partition_indices.len()
                )
            });
        let mut updated_stake_rewards = Vec::with_capacity(indices.len());
        let stakes_cache = self.stakes_cache.stakes();
        let stakes_cache_accounts = stakes_cache.stake_delegations();
        let stake_history = stakes_cache.history();
        let new_warmup_cooldown_rate_epoch = self.new_warmup_cooldown_rate_epoch();
        let rent = &self.rent_collector.rent;
        for index in indices {
            let partitioned_stake_reward = partition_rewards
                .all_stake_rewards
                .get(*index)
                .unwrap_or_else(|| {
                    panic!(
                        "partition reward out of bound: {index} >= {}",
                        partition_rewards.all_stake_rewards.total_len()
                    )
                })
                .as_ref()
                .unwrap_or_else(|| {
                    panic!("partition reward {index} is empty");
                });
            let stake_pubkey = partitioned_stake_reward.stake_pubkey;
            let stake_reward_amount = partitioned_stake_reward.inflation.stake_reward;
            let block_reward_amount = partitioned_stake_reward.block_reward;

            match Self::build_updated_stake_reward(
                self.epoch,
                stake_history,
                new_warmup_cooldown_rate_epoch,
                stakes_cache_accounts,
                partitioned_stake_reward,
                rent,
                adjust_delegations_for_rent,
                use_fixed_point_stake_math,
            ) {
                Ok(stake_reward) => {
                    stake_reward_lamports_minted += stake_reward_amount;
                    block_reward_lamports_distributed += block_reward_amount;
                    updated_stake_rewards.push(stake_reward);
                }
                Err(err) => {
                    error!(
                        "bank::distribution::store_stake_accounts_in_partition() failed for \
                         {stake_pubkey}, {stake_reward_amount} lamports burned: {err:?}"
                    );
                    stake_reward_lamports_burned += stake_reward_amount;
                    block_reward_lamports_burned += block_reward_amount;
                }
            }
        }
        drop(stakes_cache);
        self.store_accounts(
            (self.slot(), &updated_stake_rewards[..]),
            // Reuse the rewards calculation thread pool to parallelize
            // loading the previous versions of the stake accounts.
            Some(crate::bank::rewards_calculation_thread_pool()),
        );
        DistributionResults {
            stake_reward_lamports_minted,
            stake_reward_lamports_burned,
            block_reward_lamports_distributed,
            block_reward_lamports_burned,
            updated_stake_rewards,
        }
    }
```

**File:** programs/vote/src/vote_state/handler.rs (L196-209)
```rust
    pub(crate) fn add_pending_delegator_rewards(
        &mut self,
        amount: u64,
    ) -> Result<(), InstructionError> {
        match &mut self.target_state {
            TargetVoteState::V4(v4) => {
                v4.pending_delegator_rewards = v4
                    .pending_delegator_rewards
                    .checked_add(amount)
                    .ok_or(InstructionError::ArithmeticOverflow)?;
                Ok(())
            }
        }
    }
```
