## Analysis

The C4 report's bug class is: **a shared pool of "unclaimed"/undistributed value is not properly tracked by the denominator used to compute each claimant's share, so claimants can be paid out based on a stale/inconsistent snapshot and the sum of individual payouts can exceed the actual pool.**

I found a structural analog of this in Agave's SIMD-0123 block-revenue-sharing reward path.

### Title
Block-reward share calculation uses a stale total-stake denominator while the numerator reflects post-reward-augmented delegation stake, allowing aggregate over-payment from a vote account's `pending_delegator_rewards` pool - (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

### Summary
`calculate_block_reward` computes each stake delegation's share of a vote account's shared, unclaimed reward pool (`pending_delegator_rewards`) as `pending_delegator_rewards * stake / total_active_stake`, where `total_active_stake` is a fixed snapshot (`RewardEpochDelegatedStakes`) taken at the end of the rewarded epoch, but `stake` is recomputed from the *current* delegation state at calculation/recalculation time. The code's own comment acknowledges that `stake` can exceed `total_active_stake` after rewards have already been merged into a delegation's stake, and the resulting ratio is only clamped **per-delegation** to `min(pending_delegator_rewards)`, not against any tracked remaining balance of the pool. This mirrors the GlpStrategy bug: the "current balance"/denominator used to price a claim does not stay consistent with a growing/shared value, so multiple claimants can each capture more than their fair share of the same underlying pool.

### Finding Description
`calculate_block_reward` divides a vote account's `pending_delegator_rewards` among its delegators proportional to stake: [1](#0-0) 

The `total_active_stake` denominator comes from `RewardEpochDelegatedStakes`, a snapshot fixed at the end of the rewarded epoch: [2](#0-1) 

But the numerator `stake` is computed via `delegation_effective_stake(delegation, rewarded_epoch, ...)` against the delegation object passed in from `stake_delegations`, which — during `recalculate_stake_rewards` — is read live from `StakesCache`, i.e., **after** inflation stake rewards have already been merged into `delegation.stake` via `adjust_delegations_for_rent`: [3](#0-2) 

The code explicitly documents that this makes `stake > total_active_stake` possible during recalculation, and "clamps it just to be safe" — but only per delegation: [4](#0-3) 

This per-delegation clamp (`.min(pending_delegator_rewards)`) is exactly the kind of guard that looks sufficient but is not: it bounds what any *single* stake account can claim to the vote account's full pending pool, but does nothing to bound the *sum* across all delegators of that vote account. If several delegations to the same vote account simultaneously have `stake` inflated relative to the stale `total_active_stake` snapshot (which is entirely plausible since the snapshot reflects the pre-reward total while several individual delegations have each grown from `adjust_delegations_for_rent`), the sum of `pending_delegator_rewards * stake_i / total_active_stake` across delegators `i` can exceed `pending_delegator_rewards` itself — analogous to `GlpStrategy._currentBalance()` not tracking unclaimed rewards and letting a depositor's share be priced against a balance that will retroactively increase.

### Impact Explanation
If the aggregate block-reward payout to stakers of a given vote account exceeds that vote account's actual `pending_delegator_rewards`/backing lamports, either (a) lamports are minted/paid to stake accounts that were never actually funded by that vote account (a form of fund creation / theft from the shared reward pool), or (b) the vote account's lamport balance is driven below what `withdraw`'s SIMD-0123 checks assume is reserved for pending delegator rewards, undermining the withdrawal guard at: [5](#0-4) 

Either outcome is an unprivileged accounting break in reward distribution, i.e., fund theft/loss/false-payout via a core Agave runtime path (not a malicious-validator or trusted-plugin assumption).

### Likelihood Explanation
This only manifests via the `recalculate_stake_rewards` path, which is explicitly invoked to recompute rewards from an already-active `EpochRewards` sysvar (e.g., partial distribution across a fork/epoch-boundary recovery): [6](#0-5) 

Reaching this state requires the specific timing where recalculation occurs after `adjust_delegations_for_rent` has already folded a first-pass inflation reward into `delegation.stake` for one or more delegators of the same vote account, while `total_active_stake` still reflects the epoch-end pre-reward snapshot. The code's own inline comment ("harmless in practice, but we clamp it just to be safe") indicates the authors were aware `stake > total_active_stake` is reachable, but reasoned about it only in the single-account, not aggregate, case.

### Recommendation
Track a running/atomic "remaining pending_delegator_rewards for this vote account during this partitioned distribution" value (rather than relying purely on the static `pending_delegator_rewards` field read once) and clamp the *sum* of block rewards paid to all delegators of a vote account to that value, not just each individual reward. Alternatively, ensure the numerator stake used in `calculate_block_reward` is always derived from the same epoch-end snapshot basis as `total_active_stake` (i.e., never recomputed from post-reward-augmented `StakesCache` state) so that per-delegation ratios can never exceed 1 relative to the fixed total.

### Proof of Concept
1. Vote account `V` ends rewarded epoch `E` with `total_active_stake = 100` SOL split across delegators `A` (60 SOL) and `B` (40 SOL), and `pending_delegator_rewards = 10` SOL, per `RewardEpochDelegatedStakes::set` snapshot (`runtime/src/alpenglow_epoch_type.rs:71-106`).
2. First-pass reward calculation runs during `compute_new_epoch_caches_and_rewards`; before all partitions are distributed, a recalculation is triggered (`recalculate_partitioned_rewards_if_active`), and by that point `adjust_delegations_for_rent` has already merged inflation rewards into `A`'s and `B`'s `delegation.stake`, e.g., raising `A.stake` to 90 and `B.stake` to 70 (sum 160 > the fixed `total_active_stake` of 100).
3. `calculate_block_reward` computes `A`'s share as `10 * 90/100 = 9` SOL and `B`'s share as `10 * 70/100 = 7` SOL — both individually clamped under `10`, but summing to `16` SOL, 6 SOL more than `V`'s actual `pending_delegator_rewards` of `10` SOL (`calculation.rs:206-231`).
4. `store_stake_accounts_in_partition`/`build_updated_stake_reward` mint these lamports directly onto each stake account (`distribution.rs:262-267`) with no cross-check against the vote account's actual remaining pending balance, resulting in an aggregate over-payment funded outside of any tracked source.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L206-231)
```rust
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
```

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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1038-1061)
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L269-294)
```rust
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
```

**File:** programs/vote/src/vote_state/mod.rs (L1113-1121)
```rust
        // SIMD-0123: withdrawable balance when pending_delegator_rewards > 0
        // is lamports - pending_delegator_rewards - rent_exempt_minimum.
        let min_rent_exempt_balance = rent_sysvar.minimum_balance(vote_account.get_data().len());
        let min_balance = min_rent_exempt_balance
            .checked_add(pending_delegator_rewards)
            .ok_or(InstructionError::ArithmeticOverflow)?;
        if remaining_balance < min_balance {
            return Err(InstructionError::InsufficientFunds);
        }
```
