## Title
Feature-activation race between reward calculation and partitioned distribution can trigger a hard `assert_eq!` panic in `build_updated_stake_reward`, halting validators — ([File: runtime/src/bank/partitioned_epoch_rewards/distribution.rs])

## Summary
The external report describes a "table vs actual balance" mismatch: an off-chain ledger (whitelist balances) can diverge from the real on-chain balance it is supposed to represent, leaving funds inaccessible. The closest Agave analog is the partitioned epoch-rewards distribution pipeline, where a pre-computed "table" of expected stake delegations (calculated once, at the start of an epoch) is later checked against the actual updated stake-account state during a *separate, multi-block* distribution phase — using a non-graceful `assert_eq!` rather than an error path.

## Finding Description
Stake rewards for an epoch are computed once in `begin_partitioned_rewards`/`calculate_stake_rewards_and_commissions` and stored as a `PartitionedStakeRewards` table (the analog of the "whitelist table"), then distributed lamport-by-lamport over many subsequent blocks in `distribute_partitioned_epoch_rewards` / `distribute_epoch_rewards_in_partition` / `store_stake_accounts_in_partition`. [1](#0-0) 

When each partition is finally applied, `build_updated_stake_reward` reconciles the pre-computed table entry (`partitioned_stake_reward.inflation.stake.delegation.stake`) against the stake account's current lamport balance. If the `adjust_delegations_for_rent` feature flag (`relax_post_exec_min_balance_check`) is *not* active, the code does not correct any mismatch — it instead asserts that the two must already agree: [2](#0-1) 

This flag is re-read fresh, per partition, per block, from the live `self.feature_set.snapshot()` inside `store_stake_accounts_in_partition`: [3](#0-2) 

Distribution spans many blocks (`partition_indices.len()` blocks, computed from `get_reward_distribution_num_blocks`), which is a window during which a feature activation can land: [4](#0-3) [5](#0-4) 

The invariant the assert relies on — "the table's expected delegation always equals rent_exempt_reserve-adjusted lamports" — is exactly the class of "table balance vs actual balance divergence" from the report, except here the divergence is checked, but checked with a `panic!`-style `assert_eq!` in production code rather than being handled gracefully (unlike the `Result<_, DistributionError>` pattern used elsewhere in the same function for `AccountNotFound`/`ArithmeticOverflow`/`UnableToSetState`). Additionally, `recalculate_partitioned_rewards_if_active` independently recomputes the table mid-distribution on bank recreation/replay, using whatever feature state and stake-cache state is current at that time, rather than the state at original calculation time: [6](#0-5) 

If the recomputed/pre-computed table entry's delegation and the account's actual post-reward lamports ever diverge while `adjust_delegations_for_rent` is false for that call, the `assert_eq!` fires and panics the bank-processing thread, rather than returning a recoverable `DistributionError`.

## Impact Explanation
An `assert_eq!` panic during block replay/production is not an isolated failure — it panics the validator process handling that bank, and because reward distribution is deterministic and identical logic runs on every validator processing the same slot, a triggering condition would panic *all* validators simultaneously, producing a cluster-wide consensus halt. This maps to the allowed "false execution/rooting/acceptance, consensus halt" impact category, and is strictly more severe than the original report's "locked funds" because it is a chain-wide safety property (deterministic execution) rather than isolated fund access.

## Likelihood Explanation
I was not able to fully confirm, within the tool budget, the exact scenario(s) under which `adjust_delegations_for_rent` can differ between the original calculation of a `partitioned_stake_reward`'s `inflation.stake.delegation.stake` and the later distribution-time check — this requires tracing feature-activation-epoch semantics (`FeatureSet::activated_slot` vs. epoch boundaries) and whether `recalculate_stake_rewards` is guaranteed to reproduce byte-identical delegation values as the original calculation across forks/replay. The code comment "Because stake accounts are checked in calculation, and further state mutation prevents by stake-program restrictions, there should never be rewards burned" suggests the authors believe stake-program mutation is blocked during the interval, but I could not locate the enforcement point for that restriction in the code (grep for `RewardInterval`/`InsideInterval` only found the enum definition and test usages, not an instruction-blocking check). This is the key open question: if that enforcement is incomplete or feature-gated separately from `adjust_delegations_for_rent`, the assert becomes reachable by ordinary epoch-boundary/feature-activation timing rather than requiring any malicious actor. Given this uncertainty, likelihood is assessed as **speculative/unverified** rather than confirmed.

## Recommendation
- Replace the `assert_eq!` in the `else` branch of `build_updated_stake_reward` (runtime/src/bank/partitioned_epoch_rewards/distribution.rs, lines 284-294) with a recoverable `DistributionError` variant (consistent with the other error paths in the same function), so a mismatch degrades to a burned/logged reward rather than a validator panic.
- Audit `recalculate_partitioned_rewards_if_active`/`recalculate_stake_rewards` to guarantee they reproduce identical delegation math regardless of feature-activation timing relative to the original `begin_partitioned_rewards` calculation, or snapshot `adjust_delegations_for_rent` once per epoch (at calculation time) and carry it through the `PartitionedStakeReward` structure instead of re-reading `feature_set.snapshot()` per distribution block.
- Locate and confirm the actual enforcement mechanism (if any) that prevents stake-program mutation of accounts while `EpochRewardStatus::Active`, and add an explicit test asserting the assert can never be reached even with a mid-distribution feature activation.

## Proof of Concept
I could not construct a concrete triggering sequence within this investigation — doing so requires tracing `FeatureSet` activation-epoch semantics and the (unlocated) stake-program mutation restriction during `EpochRewardStatus::Active`, which is beyond what the available read-only code search could confirm. A Devin session with build/test access could:
1. Write a unit test extending `test_build_updated_stake_reward` in `distribution.rs` that constructs a `partitioned_stake_reward` computed as if `adjust_delegations_for_rent == false`, then calls `build_updated_stake_reward` with `adjust_delegations_for_rent = true` (or vice versa) with a stake account balance that only satisfies one of the two branches' expectations.
2. Confirm whether the `assert_eq!` at line 289-293 panics under that condition, and separately confirm whether any runtime check actually prevents a real feature activation from landing mid-distribution-window with such mismatched inputs.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L241-274)
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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L137-149)
```rust
        let distribution_end_exclusive =
            distribution_starting_block_height + partition_rewards.partition_indices.len() as u64;

        assert!(
            self.epoch_schedule.get_slots_in_epoch(self.epoch)
                > partition_rewards.partition_indices.len() as u64
        );

        if height >= distribution_starting_block_height && height < distribution_end_exclusive {
            let partition_index = height - distribution_starting_block_height;

            self.distribute_epoch_rewards_in_partition(partition_rewards, partition_index);
        }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L284-294)
```rust
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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L336-345)
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/mod.rs (L403-428)
```rust
    /// # stake accounts to store in one block during partitioned reward interval
    pub(super) fn partitioned_rewards_stake_account_stores_per_block(&self) -> u64 {
        self.partitioned_rewards_stake_account_stores_per_block
    }

    /// Calculate the number of blocks required to distribute rewards to all stake accounts.
    pub(super) fn get_reward_distribution_num_blocks(
        &self,
        rewards: &PartitionedStakeRewards,
    ) -> u64 {
        let total_stake_accounts = rewards.num_rewards();
        if self.epoch_schedule.warmup && self.epoch < self.first_normal_epoch() {
            1
        } else {
            const MAX_FACTOR_OF_REWARD_BLOCKS_IN_EPOCH: u64 = 10;
            let num_chunks = total_stake_accounts
                .div_ceil(self.partitioned_rewards_stake_account_stores_per_block() as usize)
                as u64;

            // Limit the reward credit interval to 10% of the total number of slots in a epoch
            num_chunks.clamp(
                1,
                (self.epoch_schedule.slots_per_epoch / MAX_FACTOR_OF_REWARD_BLOCKS_IN_EPOCH).max(1),
            )
        }
    }
```
