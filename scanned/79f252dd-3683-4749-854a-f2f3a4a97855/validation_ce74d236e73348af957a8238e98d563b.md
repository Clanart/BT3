Based on the investigation, the strongest local analog to the "reward gets silently dropped when a privileged update overwrites the payout set before the pending party can claim it" bug class is in Agave's partitioned epoch-rewards recalculation path.

### Title
Unpaid stake rewards can be silently dropped when `recalculate_partitioned_rewards_if_active` rebuilds the pending reward set from live `StakesCache` state - (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

### Summary
Partitioned epoch rewards are computed once during the Calculation phase and then paid out over several blocks during the Distribution phase. If the bank restarts or is reconstructed mid-distribution (e.g., from a snapshot while `EpochRewards` sysvar is still `active`), the pending reward set is not restored from the original calculation — it is recomputed from whatever the *current* `StakesCache` contains via `recalculate_stake_rewards`. Any stake account that a user has legitimately deactivated/closed/withdrawn between the original calculation and the recalculation simply disappears from the recomputed set, and its already-earned, not-yet-distributed reward for the prior epoch is dropped rather than paid.

### Finding Description
`begin_partitioned_rewards` computes `all_stake_rewards` once at epoch boundary and stores it in `Bank::epoch_reward_status`, then partitions it into per-block payouts consumed by `distribute_partitioned_epoch_rewards` over multiple slots. [1](#0-0) 

If the bank is rebuilt while distribution is still active, `recalculate_partitioned_rewards_if_active` is invoked to regenerate the pending set, replacing `Bank::epoch_reward_status` with a freshly recomputed distribution: [2](#0-1) 

That recomputation, `recalculate_stake_rewards`, derives the delegation set from the *current* `self.stakes_cache.stakes()` rather than reusing the original `all_stake_rewards` snapshot taken at the epoch boundary: [3](#0-2) 

The code's own comments acknowledge that recalculation using live account state diverges from the original calculation and can produce values that "should NOT be used ever" for commission accounts: [4](#0-3) 

`StakesCache` only retains delegations that are still active; a stake account fully deactivated, merged, split, or withdrawn/closed by its owner between the original calculation and the recalculation trigger will not be present in `stake_delegations` passed into `calculate_stake_rewards_and_commissions`. Since the reward-building path (`calculate_reward_points_partitioned` / `redeem_rewards`) only iterates over the current `stake_delegations`, any pubkey missing from that set is simply omitted from the regenerated `all_stake_rewards`/`partition_indices`, with no fallback to the original, already-committed entry.

This is the direct structural analog of the reported bug: `updateRewardsMetadata` overwriting the merkle root/proof set before `User A` claims causes `User A`'s already-earned reward to disappear from the new proof set. Here, `recalculate_partitioned_rewards_if_active` overwrites the pending reward/partition set before the affected staker's payout block is reached, and a state change unrelated to any admin action (the staker's own legitimate stake-account lifecycle action) causes that staker's already-earned, not-yet-paid reward for the prior epoch to vanish from the regenerated set.

### Impact Explanation
The dropped entry represents inflation reward lamports that were already earned for the previous epoch and reflected in the `EpochRewards` sysvar's `total_rewards`/`distributed_rewards` accounting, but never minted into the staker's account. This is a fund-loss bug against an unprivileged, non-malicious party who has no way to detect or prevent it — it depends solely on the sequencing of a validator restart/snapshot-based bank reconstruction relative to normal stake account lifecycle operations.

### Likelihood Explanation
Requires the target stake account's reward to be scheduled in a not-yet-reached distribution partition and the account to become fully undelegated (deactivated stake fully cooled down, merged away, or closed) in the window between the original per-epoch calculation and a subsequent recalculation trigger (bank restart/snapshot reload while `EpochRewards.active == true`). This is a narrow, timing-dependent window, but it does not rely on any malicious actor, admin privilege, or trusted-role assumption — only ordinary validator restart behavior plus a user's own routine stake management, both of which are expected to occur in production.

### Recommendation
When rebuilding the pending distribution set mid-epoch, preserve the originally-committed `all_stake_rewards` for stakers who have not yet been paid instead of recomputing solely from the live `StakesCache`; or, if recomputation is required for correctness reasons (e.g., accounting for rent/warmup changes), explicitly carry forward any pubkey present in the original calculation but absent from the live `StakesCache`, paying it out using the last valid recorded amount before it is dropped.

### Proof of Concept
1. Reach the epoch boundary so `begin_partitioned_rewards` computes `all_stake_rewards` including staker `S` with a nonzero pending stake reward, scheduled into a distribution partition beyond the first block.
2. Before that partition's block height is reached, have `S` fully deactivate/close/merge away their stake account (an ordinary, permitted stake-account operation).
3. Force the bank to be rebuilt while `EpochRewards.active == true` (validator restart / snapshot reload mid-distribution), triggering `recalculate_partitioned_rewards_if_active`.
4. `recalculate_stake_rewards` rebuilds `all_stake_rewards` from `self.stakes_cache.stakes()`, which no longer contains `S`'s delegation.
5. `S`'s previously computed, not-yet-distributed reward is absent from the new `all_stake_rewards`/`partition_indices`; distribution proceeds to inactive without ever crediting `S`, even though `total_rewards`/`distributed_rewards` accounting in the sysvar assumed it would be paid. [5](#0-4)

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L241-282)
```rust
    pub(in crate::bank) fn begin_partitioned_rewards(
        &mut self,
        parent_epoch: Epoch,
        parent_slot: Slot,
        parent_block_height: u64,
        rewards_calculation: &PartitionedRewardsCalculation,
        rewards_metrics: &mut RewardsMetrics,
        thread_pool: &ThreadPool,
    ) -> u64 {
        let RewardCommissionLamportAmounts {
            distributed_lamports,
            distributed_to_incinerator_lamports,
            burned_lamports,
        } = self.distribute_reward_commissions(
            parent_epoch,
            rewards_calculation,
            rewards_metrics,
            thread_pool,
        );

        let slot = self.slot();
        let distribution_starting_block_height =
            self.block_height() + REWARD_CALCULATION_NUM_BLOCKS;

        let PartitionedRewardsCalculation {
            stake_rewards,
            point_value,
            ..
        } = rewards_calculation;

        let stake_rewards = Arc::clone(&stake_rewards.stake_rewards);

        let num_partitions = self.get_reward_distribution_num_blocks(&stake_rewards);
        self.set_epoch_reward_status_calculation(distribution_starting_block_height, stake_rewards);

        self.create_epoch_rewards_sysvar(
            distributed_lamports + distributed_to_incinerator_lamports + burned_lamports,
            distribution_starting_block_height,
            num_partitions,
            point_value,
            0, // block_rewards
        );
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1011-1095)
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

    /// Returns a vector of partitioned stake rewards. StakeRewards are
    /// recalculated from an active EpochRewards sysvar, vote accounts from
    /// EpochStakes, and stake accounts from StakesCache.
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
    }
```
