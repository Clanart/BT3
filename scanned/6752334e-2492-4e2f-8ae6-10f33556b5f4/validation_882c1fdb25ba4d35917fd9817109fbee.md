## Analog identified: hard `assert_eq!` panic on stake-delegation consistency during partitioned epoch-rewards distribution

### Title
Reachable `assert_eq!` panic in `Bank::build_updated_stake_reward` when a stake account's delegation diverges from its epoch-boundary snapshot during multi-block reward distribution - (File: `runtime/src/bank/partitioned_epoch_rewards/distribution.rs`)

### Summary
The reported LatentSwap bug is fundamentally an *unenforced invariant*: a value (`baseTokenSupply`) that downstream code assumes is consistent with another tracked value (Synth supply) can silently diverge through normal operation, and the code that "protects" the invariant instead turns the divergence into an unconditional revert/DoS. The closest Agave analog is in the partitioned epoch-rewards distribution path, where `build_updated_stake_reward` hard-asserts that a stake account's on-disk delegation equals a value computed from a stale, epoch-boundary snapshot plus the previously calculated reward delta [1](#0-0) . Reward distribution is partitioned over up to 10% of an epoch's slots [2](#0-1) , during which normal stake-program transactions continue to be processed. Unlike the LatentSwap bug which merely reverts one call, here divergence between the snapshot and the live account manifests as a Rust `assert_eq!` panic — a hard process abort during deterministic state-transition, not a recoverable error.

### Finding Description
`store_stake_accounts_in_partition` iterates the indices assigned to the current partition and calls `build_updated_stake_reward` for each pending stake reward, feeding it the *current* stake account from `stakes_cache_accounts` at distribution time [3](#0-2) .

Inside `build_updated_stake_reward`, when the `relax_post_exec_min_balance_check` feature is not active, the code computes an `expected_delegation` by adding the reward amount that was computed back at the epoch-boundary calculation phase to the delegation recorded at that same calculation time (`stake.delegation.stake`, taken from the live cache entry), and asserts it equals `new_stake.delegation.stake`, which comes from `partitioned_stake_reward.inflation.stake.delegation.stake` — a value fixed when rewards were calculated/recalculated:

```rust
let expected_delegation = stake
    .delegation
    .stake
    .saturating_add(partitioned_stake_reward.inflation.stake_reward);
assert_eq!(
    expected_delegation, new_stake.delegation.stake,
    "stake reward delegation must be consistent with the updated stake account \
     lamport balance"
);
``` [1](#0-0) 

This mirrors the LatentSwap `E_LEX_InvalidMarketState()` check in structure: both are late "sanity checks" that assume two independently-tracked quantities can never drift apart, and both fail hard when they do. The difference is severity of the failure mode — Solidity `revert()` vs Rust `assert_eq!` panic, which aborts the validator process during block replay.

The code comment right above `store_stake_accounts_in_partition` explicitly documents the assumption being relied on: *"Because stake accounts are checked in calculation, and further state mutation prevents by stake-program restrictions, there should never be rewards burned."* [4](#0-3)  This acknowledges the intended safety net is (a) recomputation via `recalculate_partitioned_rewards_if_active`/`recalculate_stake_rewards`, which re-derives stake rewards from the live `StakesCache` before each distribution block [5](#0-4) , and (b) an assumption that the stake program restricts further mutation of the account during the reward window. I was not able to locate, within the indexed portion of this repo, the specific stake-program-side guard that fully blocks all delegation-affecting instructions (deactivate/withdraw/split/merge/redelegate) for every stake account while its reward is still pending distribution in a later partition of the same epoch-boundary window; the epoch-rewards "active" gate found (`get_reward_interval`/`EpochRewardStatus::Active`) governs bank-level scheduling of distribution, not stake-instruction admission control. Given the multi-block distribution window and the recalculation logic existing specifically to reconcile forks/rewrites, if any legitimate account state transition can leave the stake account's calculation-time delegation and its distribution-time delegation inconsistent by anything other than exactly the reward delta (e.g. an operation applied between recalculation and store, or an account update to a pubkey whose reward was already fixed in an un-recalculated partition on some code path), the `assert_eq!` fires and panics.

### Impact Explanation
Because this code executes during Bank state transition (replicated identically by every validator processing the block), a triggerable panic here is not a single-node crash but a deterministic, network-wide event: every honest validator that replays or produces the affected slot hits the same `assert_eq!` and aborts. This falls squarely in the "false execution/rooting/acceptance, consensus halt" impact category for unprivileged runtime/accounts code, since ordinary stake accounts controlled by unprivileged stakers feed directly into this computation. A hard panic during block production/replay is strictly worse than the LatentSwap revert-based DoS: it does not just block a single instruction, it can halt block production/validation for the cluster.

### Likelihood Explanation
Likelihood is assessed as **uncertain/low-to-moderate** rather than confirmed, because:
- I could not verify from the indexed code whether the stake program fully forbids delegation-changing instructions on a stake account for the entire duration its reward is pending distribution (this guard, if present, would be the load-bearing mitigation, analogous to what M-02's remediation recommends: "prevent this scenario by checking the state after each operation").
- The `recalculate_partitioned_rewards_if_active` mechanism is specifically designed to keep the calculation window and any live changes reconciled across forks, which reduces (but per the code's own comments, does not eliminate by construction) the chance of drift; the `assert_eq!` is defense-in-depth against an unexpected mismatch, not a proven-unreachable check.
- Reproducing this concretely would require constructing a sequence of legitimate stake operations (e.g., merge/split/withdraw/redelegate) landing in the specific block window between reward calculation/recalculation and the block that stores that stake account's reward, which needs local reproduction against the stake program's actual guards — something outside what static code search alone can confirm.

### Recommendation
1. Confirm (via runtime/validator testing) that the stake program unconditionally rejects every delegation-affecting instruction (`Deactivate`, `Withdraw`, `Split`, `Merge`, `Redelegate`) on any stake account with an outstanding, uncredited `PartitionedStakeReward` for the whole span between calculation and its assigned distribution partition, on every fork.
2. If any gap exists, close it by re-validating (not merely asserting) the delegation consistency in `build_updated_stake_reward`: on mismatch, route through `DistributionError` and burn/skip the reward gracefully (as already done for `AccountNotFound`/`ArithmeticOverflow`) rather than calling `assert_eq!`, converting a potential panic into a handled, auditable error path.
3. Add fuzz/property tests that inject arbitrary legitimate stake-account mutations between the calculation phase and each distribution partition across multiple forks, asserting the code never panics regardless of ordering.

### Proof of Concept
Not independently reproduced. A conceptual PoC (requiring a running validator/localnet, which I do not have tool access to execute) would:
1. Create a stake account, delegate, and let it earn epoch credits so that a stake reward is calculated for it at the epoch boundary (`begin_partitioned_rewards`) with `num_partitions > 1` [6](#0-5) .
2. In a block that lands after calculation but before the block that stores this account's own partition, issue a legitimate stake instruction (e.g., `Split` or `Merge`) that changes `delegation.stake` in a way not reconciled by `recalculate_stake_rewards`/`adjust_delegation_for_rent`.
3. Observe whether, at the block that calls `build_updated_stake_reward` for that stake pubkey, `expected_delegation != new_stake.delegation.stake`, triggering the `assert_eq!` panic in `distribution.rs` lines 289–293 rather than a graceful `DistributionError`.

This PoC cannot be confirmed complete from static analysis alone — verifying it requires either full access to the stake-program instruction guards for the reward-pending window (not indexed here) or a running Devin session with terminal/build access to the full `agave--008` repo to attempt a live reproduction test in `runtime/src/bank/partitioned_epoch_rewards/distribution.rs`'s test module.

### Citations

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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L330-332)
```rust
    /// Because stake accounts are checked in calculation, and further state
    /// mutation prevents by stake-program restrictions, there should never be
    /// rewards burned.
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L361-393)
```rust
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/mod.rs (L408-428)
```rust
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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L241-296)
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

        datapoint_info!(
            "epoch-rewards-status-update",
            ("start_slot", slot, i64),
            ("calculation_block_height", self.block_height(), i64),
            ("active", 1, i64),
            ("parent_slot", parent_slot, i64),
            ("parent_block_height", parent_block_height, i64),
        );
        distributed_lamports
            + rewards_calculation
                .stake_rewards
                .total_stake_rewards_lamports
    }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1011-1043)
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
```
