Audit Report

## Title
Partitioned Epoch-Reward Recalculation Uses Live `StakesCache` State Instead of Reward-Epoch-Boundary Stake, Corrupting Individual Stake-Reward Payouts - (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

## Summary
`recalculate_stake_rewards` re-derives each stake account's `Delegation` from the bank's current `self.stakes_cache.stakes()` at the moment of recalculation, rather than from a snapshot taken at the reward-epoch boundary, when it recomputes pending stake rewards mid-distribution. [1](#0-0)  Because a delegator can permissionlessly mutate their own `Delegation` (`stake`, `activation_epoch`, `deactivation_epoch`) via `Split`/`Merge`/`Deactivate`/`Delegate` between the initial epoch-boundary calculation and a later recalculation trigger, the recalculated per-account reward for `rewarded_epoch` can be computed against a delegation state that did not exist during that epoch, producing an incorrect payout for that individual stake account.

## Finding Description
`recalculate_partitioned_rewards_if_active` is invoked to redo pending stake-reward computation whenever the `EpochRewards` sysvar is still `active`, i.e., partitioned distribution has not finished (e.g., on a fork switch or restart mid-distribution). [2](#0-1)  It calls `recalculate_stake_rewards`, which reads `self.stakes_cache.stakes()` — the bank's live, current stakes cache — and passes it into `get_epoch_params_for_recalculation(rewarded_epoch, &stakes)` to obtain `stake_delegations` used for the recomputation. [3](#0-2)  Those live `Delegation` values are then fed into `calculate_stake_rewards_and_commissions`, the same function used for the original epoch-boundary calculation, and the resulting `stake_rewards` are the field that is actually used to drive real payouts. [4](#0-3) 

The reward math in `calculate_stake_points_and_credits`/`tower_epoch_credits_iter`/`calculate_alpenglow_points` calls `delegation_effective_stake(&stake.delegation, epoch, stake_history, ...)`, which uses `stake.delegation.stake`, `activation_epoch`, and `deactivation_epoch` together with `stake_history` to compute the effective stake at `epoch`. [5](#0-4) [6](#0-5)  This correctly reconstructs warmup/cooldown ratios for `rewarded_epoch` from `stake_history`, but it operates on whichever `Delegation` fields are currently live. If a delegator changes `stake`/`activation_epoch`/`deactivation_epoch` between the original epoch-boundary calculation and a later recalculation, the recomputed effective stake for `rewarded_epoch` is based on the *new* delegation shape (e.g., a newly-set `deactivation_epoch` could make the delegation appear deactivating/deactivated for `rewarded_epoch` when it was fully active during that epoch), not the historically accurate one.

The code's own comment acknowledges this exact class of problem for reward commission accounts loaded "from the current bank, and not the start of the epoch," and explicitly disclaims using the recalculated `RewardCommissionAccounts` for that reason. [7](#0-6)  No equivalent disclaimer or protective snapshot exists for the `stake_rewards` field, which is the value actually consumed for payout — only the total delegated-stake denominator (`RewardEpochDelegatedStakes`) is frozen for the epoch, not each individual account's `Delegation`. [8](#0-7) 

## Impact Explanation
This is a plausible fund-loss/fund-mispayment concern in the `runtime`/`accounts` scope: an individual delegator's payout for `rewarded_epoch` could differ from what was actually earned if recalculation is triggered after that delegator has independently modified their own stake account. However, I was not able to fully verify within the available context whether `get_epoch_params_for_recalculation` filters stake_delegations by `activation_epoch <= rewarded_epoch` (which would exclude newly-activated/re-delegated stake) or by other epoch-boundary constraints that might narrow or eliminate the exploitable window (e.g., only accounts whose `activation_epoch` predates `rewarded_epoch`, or stakes credits already redeemed for that epoch being skipped via `credits_in_stake >= credits_in_vote`). The `calculate_stake_points_and_credits` function does skip recalculation for a stake whose observed credits already match/exceed the vote account's credits for the epoch, which limits (but does not eliminate) the window in some cases. [9](#0-8) 

## Likelihood Explanation
The precondition requires: (1) a partitioned distribution still active across multiple blocks/forks, and (2) a fork switch or restart triggering `recalculate_partitioned_rewards_if_active` while a delegator has performed a permissionless stake operation in between. Both conditions are plausible in production but timing-dependent, and the actual functional impact depends on unverified filtering logic in `get_epoch_params_for_recalculation` and credit-based skip logic in `calculate_stake_points_and_credits`, which could constrain the practical exploitability of this issue.

## Recommendation
Snapshot the per-account `Delegation` (stake, activation_epoch, deactivation_epoch) at the reward-epoch boundary the same way `RewardEpochDelegatedStakes` freezes the total per-vote-account delegated stake, and have `recalculate_stake_rewards` consult that frozen snapshot instead of `self.stakes_cache.stakes()` when recomputing pending stake rewards mid-distribution.

## Proof of Concept
1. Epoch `N` ends; `calculate_stake_rewards_and_commissions` computes stake rewards for delegator `D` using `D`'s `Delegation` at that moment (`stake = S1`), and starts partitioned distribution (`EpochRewards` sysvar `active = true`).
2. Before all partitions are distributed, `D` submits a `Split`/`Deactivate` instruction that changes `D`'s live `Delegation` fields (`stake`, `deactivation_epoch`).
3. A fork switch/restart triggers `recalculate_partitioned_rewards_if_active` → `recalculate_stake_rewards` while the sysvar is still `active`. [10](#0-9) 
4. `recalculate_stake_rewards` reads `D`'s `Delegation` from the current `stakes_cache` (post-mutation) and recomputes `D`'s stake reward for `rewarded_epoch = N` using the post-mutation delegation shape. [3](#0-2) 
5. Compare `D`'s recalculated `stake_reward` against the original pre-mutation calculation to confirm divergence — this requires extending the existing regression test at `runtime/src/bank/partitioned_epoch_rewards/calculation.rs:2770-2797` to mutate an individual delegator's `Delegation` (not just observe the shared `RewardEpochDelegatedStakes` denominator) between the initial calculation and recalculation, and assert the individual stake reward is unchanged (which is the correctness property currently unverified by tests).

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1011-1033)
```rust
    /// If rewards are still active, recalculates partitioned stake rewards and
    /// updates Bank::epoch_reward_status. This method assumes that reward
    /// commissions have already been calculated and delivered, and *only*
    /// recalculates stake rewards
    pub(in crate::bank) fn recalculate_partitioned_rewards_if_active<F, TP>(
        &mut self,
        thread_pool_builder: F,
    ) where
        F: FnOnce() -> TP,
        TP: std::borrow::Borrow<ThreadPool>,
    {
        let epoch_rewards_sysvar = self.get_epoch_rewards_sysvar();
        if epoch_rewards_sysvar.active {
            let thread_pool = thread_pool_builder();
            let (stake_rewards, partition_indices) =
                self.recalculate_stake_rewards(&epoch_rewards_sysvar, thread_pool.borrow());
            self.set_epoch_reward_status_distribution(
                epoch_rewards_sysvar.distribution_starting_block_height,
                stake_rewards,
                partition_indices,
            );
        }
    }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1038-1058)
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1063-1094)
```rust
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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L2770-2797)
```rust
        assert!(unpaid_reward.inflation.stake_reward > 0);

        // Force exactly one stake reward to be distributed before simulating
        // snapshot restore. That write updates StakesCache with a larger
        // delegation for the same vote account.
        bank.set_epoch_reward_status_distribution(
            bank.block_height(),
            Arc::clone(&original_stake_rewards),
            vec![vec![paid_index], vec![unpaid_index]],
        );
        bank.distribute_partitioned_epoch_rewards();

        let epoch_rewards_sysvar = bank.get_epoch_rewards_sysvar();
        assert!(epoch_rewards_sysvar.active);
        let (recalculated_stake_rewards, _partition_indices) =
            bank.recalculate_stake_rewards(&epoch_rewards_sysvar, &thread_pool);
        let recalculated_unpaid_reward = recalculated_stake_rewards
            .enumerated_rewards_iter()
            .find_map(|(_index, reward)| {
                (reward.stake_pubkey == unpaid_reward.stake_pubkey).then_some(reward)
            })
            .expect("unpaid stake reward must still be pending after recalculation");

        assert_eq!(
            unpaid_reward.inflation.stake_reward, recalculated_unpaid_reward.inflation.stake_reward,
            "recalculation after partial distribution must use the same AG delegated stake \
             denominator as the original epoch-boundary calculation"
        );
```

**File:** runtime/src/inflation_rewards/points.rs (L212-218)
```rust
        let stake_amount = u128::from(delegation_effective_stake(
            &stake.delegation,
            epoch,
            stake_history,
            new_rate_activation_epoch,
            use_fixed_point_stake_math,
        ));
```

**File:** runtime/src/inflation_rewards/points.rs (L272-278)
```rust
    let stake_amount = u128::from(delegation_effective_stake(
        &stake.delegation,
        epoch,
        stake_history,
        new_rate_activation_epoch,
        use_fixed_point_stake_math,
    ));
```

**File:** runtime/src/inflation_rewards/points.rs (L366-411)
```rust
    let credits_in_stake = stake.credits_observed;
    let credits_in_vote = vote_state.credits;
    // if there is no newer credits since observed, return no point
    match credits_in_vote.cmp(&credits_in_stake) {
        Ordering::Less => {
            if let Some(inflation_point_calc_tracer) = inflation_point_calc_tracer.as_ref() {
                inflation_point_calc_tracer(&SkippedReason::ZeroCreditsAndReturnRewound.into());
            }
            // Don't adjust stake.activation_epoch for simplicity:
            //  - generally fast-forwarding stake.activation_epoch forcibly (for
            //    artificial re-activation with re-warm-up) skews the stake
            //    history sysvar. And properly handling all the cases
            //    regarding deactivation epoch/warm-up/cool-down without
            //    introducing incentive skew is hard.
            //  - Conceptually, it should be acceptable for the staked SOLs at
            //    the recreated vote to receive rewards again immediately after
            //    rewind even if it looks like instant activation. That's
            //    because it must have passed the required warmed-up at least
            //    once in the past already
            //  - Also such a stake account remains to be a part of overall
            //    effective stake calculation even while the vote account is
            //    missing for (indefinite) time or remains to be pre-remove
            //    credits score. It should be treated equally to staking with
            //    delinquent validator with no differentiation.

            // hint with true to indicate some exceptional credits handling is needed
            return CalculatedStakePoints {
                tower_points: 0,
                ag_points: 0,
                new_credits_observed: credits_in_vote,
                force_credits_update_with_skipped_reward: true,
            };
        }
        Ordering::Equal => {
            if let Some(inflation_point_calc_tracer) = inflation_point_calc_tracer.as_ref() {
                inflation_point_calc_tracer(&SkippedReason::ZeroCreditsAndReturnCurrent.into());
            }
            // don't hint caller and return current value if credits remain unchanged (= delinquent)
            return CalculatedStakePoints {
                tower_points: 0,
                ag_points: 0,
                new_credits_observed: credits_in_stake,
                force_credits_update_with_skipped_reward: false,
            };
        }
        Ordering::Greater => {}
```
