### Title
Un-guarded `assert_eq!` on stake delegation during partitioned epoch-reward distribution can panic every validator - (`runtime/src/bank/partitioned_epoch_rewards/distribution.rs`)

### Summary
Agave's partitioned epoch-reward mechanism splits reward payout into two temporally separated phases: a one-block **calculation** phase that snapshots stake delegations and computes the post-reward delegation for every stake account, and a multi-block **distribution** phase that, block by block, re-reads the *live* stake account from `StakesCache` and merges in the pre-computed reward. In the non-rent-adjustment code path, `build_updated_stake_reward` asserts that the live delegation plus the reward exactly equals the delegation value computed during calculation. If any legitimate, unprivileged stake-owner transaction (e.g. `Split`, `Withdraw`, `Merge`, `Deactivate`) changes that stake account's `delegation.stake` between the calculation block and the later block in which its partition is distributed, the assertion fails and the validator panics while replaying an otherwise valid block, deterministically crashing every honest validator that processes it (analogous to how the original LaunchEvent report describes an operation in phase 1 invalidating an assumption relied on unconditionally in phase 2, causing an unrecoverable failure).

### Finding Description
`Bank::begin_partitioned_rewards` calculates rewards once at the epoch boundary and caches `PartitionedStakeReward` entries containing a fully-formed `Stake` (`inflation.stake`) computed from the delegation snapshot taken at that moment: [1](#0-0) 

Distribution then spans multiple subsequent blocks, driven by `distribute_partitioned_epoch_rewards`, which processes one partition of stake accounts per block: [2](#0-1) 

For each stake account in the current block's partition, `store_stake_accounts_in_partition` fetches the account from the **live** `StakesCache` (not the calculation-time snapshot) and calls `build_updated_stake_reward`: [3](#0-2) 

Inside `build_updated_stake_reward`, when the `relax_post_exec_min_balance_check` feature's rent-adjustment branch is not taken, the function hard-asserts that the live delegation (`stake.delegation.stake`) plus the pre-computed reward equals the delegation value computed during the calculation phase: [4](#0-3) 

The surrounding comment concedes the underlying assumption explicitly: *"Because stake accounts are checked in calculation, and further state mutation prevents by stake-program restrictions, there should never be rewards burned."* [5](#0-4) 

However, no such restriction was found in the stake program itself — a search of `programs/stake*` for any check against `EpochRewardStatus`, `EpochRewards` sysvar "active" state, or a `RewardInterval` gate returned no matches, and `RewardInterval` only appears in `runtime/src/bank/partitioned_epoch_rewards/mod.rs` and test helpers, not in any instruction-processing/transaction-locking code path that would block writable stake-account transactions during the distribution window. That means an unprivileged stake authority can submit a completely ordinary `Split`, `Withdraw`, partial `Deactivate`, or `Merge` instruction on their own stake account in any block between the calculation block and the block where that account's partition is scheduled for distribution, changing `delegation.stake` out from under the cached reward. When that account's turn comes up in `store_stake_accounts_in_partition`, `expected_delegation` (computed from the now-mutated live delegation) will not match `new_stake.delegation.stake` (computed from the stale calculation-time delegation), and the `assert_eq!` panics.

Unlike the other three failure modes in this function (`DistributionError::AccountNotFound`, `ArithmeticOverflow`, `UnableToSetState`), which are surfaced as recoverable `Err` values that get logged and treated as a burned reward by the caller, the delegation-consistency check is enforced via a raw `assert_eq!`, i.e. a process abort rather than a graceful error path. This is the same underlying invariant-break pattern as the original bug report: an action legitimately available to an unprivileged actor in an intervening phase invalidates a "frozen" assumption baked into an earlier phase, and the code that consumes that assumption in a later phase has no fallback — it fails hard instead of degrading gracefully, but here the consequence is far worse than fund loss: because block execution must be deterministic and reproducible for consensus, every validator replaying that exact block will hit the identical panic, taking down the cluster rather than a single account holder losing funds.

### Impact Explanation
A panic inside `Bank::distribute_partitioned_epoch_rewards`, which runs on every block during the (multi-block) reward-distribution window at every epoch boundary, is executed identically by every validator replaying that block. A deterministic crash triggered by an ordinary, unprivileged stake transaction in this consensus-critical runtime path constitutes a network-wide consensus halt / non-RPC remote crash, which is categorically more severe than the original report's fund-loss scenario.

### Likelihood Explanation
The precondition is a stake-owner submitting a normal stake-program instruction (`Split`/`Withdraw`/partial `Deactivate`/`Merge`) that changes their own stake account's `delegation.stake` value in the small window between the epoch-boundary calculation block and the specific later block assigned to that account's partition (this window can span many blocks, since distribution is spread over `num_partitions` blocks). No malicious peer, validator, or leaked key is required — the attacker only needs an ordinary stake account with any nonzero delegation and standard funds to pay fees, and needs their transaction to land in a normal block within the distribution window, which is entirely within an unprivileged user's control. I was unable to conclusively rule out that some other layer (e.g. account-locking / scheduler-level filtering not covered by the searches performed) blocks writable stake-program transactions during this interval; no such enforcement was found in `programs/stake*` or via `RewardInterval` usage outside `runtime/src/bank/partitioned_epoch_rewards/mod.rs` and test files, but a Devin session with full-repository access (including the transaction-scheduling/locking code in `runtime`/`svm`, which is only partially indexed here) should verify this before treating the analog as a fully confirmed, currently-exploitable defect.

### Recommendation
Replace the `assert_eq!` in `build_updated_stake_reward` (`runtime/src/bank/partitioned_epoch_rewards/distribution.rs`, lines 284-294) with a recoverable path (e.g. a new `DistributionError` variant) mirroring the existing rent-adjustment branch's graceful reconciliation, so that a stake account mutated between calculation and distribution has its reward safely dropped/burned and logged rather than aborting the validator process. Additionally, confirm and, if absent, add an explicit enforcement (at either the stake-program instruction level or the transaction-account-locking level) that rejects writable stake-program transactions on any stake account with a pending, uncredited partitioned reward, closing the gap the code comment assumes already exists.

### Proof of Concept
1. Wait for (or induce, in a local test cluster) an epoch boundary where the local validator's stake account, `S`, receives a nonzero inflation reward via `begin_partitioned_rewards`; note the block height at which `S`'s partition will be distributed (a later block, since `num_partitions` spans multiple blocks).
2. Before that later block is produced, submit an ordinary `StakeInstruction::Split` (or partial `Withdraw`/`Deactivate`) from the authorized staker of `S`, reducing `S`'s `delegation.stake`.
3. When the block containing `S`'s scheduled partition is processed, `store_stake_accounts_in_partition` → `build_updated_stake_reward` computes `expected_delegation` from the now-reduced live delegation plus the stale cached reward, which no longer equals `new_stake.delegation.stake` from the calculation-time snapshot, triggering the `assert_eq!` panic in every validator replaying that block. [6](#0-5)

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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L78-149)
```rust
impl Bank {
    /// Process reward distribution for the block if it is inside reward interval.
    pub(in crate::bank) fn distribute_partitioned_epoch_rewards(&mut self) {
        let EpochRewardStatus::Active(status) = &self.epoch_reward_status else {
            return;
        };

        let distribution_starting_block_height = match &status {
            EpochRewardPhase::Calculation(status) => status.distribution_starting_block_height,
            EpochRewardPhase::Distribution(status) => status.distribution_starting_block_height,
        };

        let height = self.block_height();
        if height < distribution_starting_block_height {
            return;
        }

        if let EpochRewardPhase::Calculation(status) = &status {
            // epoch rewards have not been partitioned yet, so partition them now
            // This should happen only once immediately on the first rewards distribution block, after reward calculation block.
            let epoch_rewards_sysvar = self.get_epoch_rewards_sysvar();
            let (partition_indices, partition_us) = measure_us!({
                epoch_rewards_hasher::hash_rewards_into_partitions(
                    &status.all_stake_rewards,
                    &epoch_rewards_sysvar.parent_blockhash,
                    epoch_rewards_sysvar.num_partitions as usize,
                )
            });

            // update epoch reward status to distribution phase
            self.set_epoch_reward_status_distribution(
                distribution_starting_block_height,
                Arc::clone(&status.all_stake_rewards),
                partition_indices,
            );

            datapoint_info!(
                "epoch-rewards-status-update",
                ("slot", self.slot(), i64),
                ("block_height", height, i64),
                ("partition_us", partition_us, i64),
                (
                    "distribution_starting_block_height",
                    distribution_starting_block_height,
                    i64
                ),
            );
        }

        let EpochRewardStatus::Active(EpochRewardPhase::Distribution(partition_rewards)) =
            &self.epoch_reward_status
        else {
            // We should never get here.
            unreachable!(
                "epoch rewards status is not in distribution phase, but we are trying to \
                 distribute rewards"
            );
        };

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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L327-335)
```rust
    /// Store stake rewards in partition
    /// Returns DistributionResults containing the sum of all the rewards
    /// stored, the sum of all rewards burned, and the updated StakeRewards.
    /// Because stake accounts are checked in calculation, and further state
    /// mutation prevents by stake-program restrictions, there should never be
    /// rewards burned.
    ///
    /// Note: even if staker's reward is 0, the stake account still needs to be
    /// stored because credits observed has changed
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L336-393)
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
```
