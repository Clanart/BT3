## Analysis

The GMX bug class is: an action is *decided* based on a stale snapshot of state, and by the time the action actually *executes*, the underlying state has changed — but the code never re-validates the target is still eligible for that specific action, so it applies stale-state assumptions to fresh state.

The closest Agave analog is in the partitioned epoch-rewards distribution pipeline, where the amount of stake a reward should be added to is *calculated once* at the epoch boundary and *applied later* — across up to 10% of the epoch's slots — to whatever the stake account's state happens to be at that later block, with a hard `assert_eq!` that assumes nothing changed in between.

### Title
Partitioned stake-reward distribution assumes the calculation-time stake snapshot still matches the account at distribution time, causing a deterministic panic (chain halt) if it doesn't - (`runtime/src/bank/partitioned_epoch_rewards/distribution.rs`)

### Summary
`Bank::begin_partitioned_rewards` computes each stake account's reward and its expected post-reward delegation (`partitioned_stake_reward.inflation.stake`) once, at the epoch boundary, from a snapshot of `StakesCache` [1](#0-0) . Actual distribution to accounts happens later, spread over many subsequent blocks (`distribute_partitioned_epoch_rewards` / `distribute_epoch_rewards_in_partition`) [2](#0-1) . When each account is finally paid, `build_updated_stake_reward` re-reads the *current* (possibly changed) stake account from `stakes_cache_accounts` and asserts that its `delegation.stake` plus the previously calculated reward equals the previously calculated `new_stake.delegation.stake`: [3](#0-2) 
There is no re-validation or graceful handling for the case where the stake account's delegation has legitimately changed between the calculation snapshot and the distribution block — it is a hard `assert_eq!`, not an `if`/error path (unlike the `adjust_delegations_for_rent` branch just above it, which explicitly tolerates such changes).

### Finding Description
This is structurally the same defect as the GMX ADL report: a decision (here, "this account's post-reward delegation must be X") is made from a global/stale snapshot, and is applied unconditionally at a later point without checking whether the specific target (the stake account) is still in the state the snapshot assumed.

- The reward/point calculation and the resulting expected `new_stake.delegation.stake` are computed at the epoch boundary from the `StakesCache` snapshot captured via `get_epoch_params_for_recalculation` / `calculate_stake_rewards_and_commissions` [4](#0-3) .
- Distribution for a given partition can occur many blocks later — the distribution window can span up to 10% of the slots in an epoch [5](#0-4) .
- At the actual distribution block, `store_stake_accounts_in_partition` reads the *live* `stakes_cache_accounts` for the account [6](#0-5) , and `build_updated_stake_reward` compares the live `stake.delegation.stake` against the value implied by the stale calculation-time snapshot with a hard assert [3](#0-2) .
- The code's own comment acknowledges this is an *assumption*, not an enforced invariant: "Because stake accounts are checked in calculation, and further state mutation prevents by stake-program restrictions, there should never be rewards burned" [7](#0-6) . This is exactly the GMX pattern — the check is global/at-decision-time, and the code trusts that nothing invalidates the decision by execution time, rather than re-verifying the specific account's eligibility/state at execution time.
- This branch (the `assert_eq!`) is only taken when `adjust_delegations_for_rent` (the `relax_post_exec_min_balance_check` feature) is **not** active [8](#0-7) , i.e. it is a live code path on any cluster/config where that feature hasn't been activated.

I was not able to fully verify within the indexed code whether every stake-program instruction (`Split`, `Merge`, `Redelegate`, `Withdraw`, `DeactivateDelinquent`, etc.) is explicitly blocked from mutating a stake account's `delegation.stake` while that account has a pending, not-yet-distributed reward. No `EpochRewardStatus`/reward-interval check was found being consulted from the stake program instruction processors in the indexed code — the `RewardInterval`/`get_reward_interval` gating logic that exists in `partitioned_epoch_rewards/mod.rs` appears to be used only internally by Bank's own epoch-boundary bookkeeping, not surfaced as a restriction inside stake-instruction execution. If any legitimate, unprivileged stake-authority action (e.g. `Merge`, which changes a destination account's lamports/delegation from a normal user transaction) can alter `delegation.stake` for an account with a pending, uncalculated-for reward before its partition's distribution block, this `assert_eq!` will fire.

### Impact Explanation
An `assert_eq!` panic in `Bank::store_stake_accounts_in_partition`/`build_updated_stake_reward` occurs deterministically for every validator processing the same block (all validators execute identical logic on identical state), so this would not cause a fork/consensus divergence in the traditional sense, but it causes every validator to panic in lockstep while processing a specific block — i.e., a chain-wide halt requiring a coordinated fix/restart. This matches the "consensus halt" impact category for unprivileged runtime/accounts bugs, triggered purely by ordinary, unprivileged user stake-management transactions landing during a many-block reward-distribution window.

### Likelihood Explanation
Likelihood depends on whether stake-program instructions are actually blocked from mutating a pending-reward account's delegation during the (multi-block) distribution window on the affected feature-set profile (i.e., without `relax_post_exec_min_balance_check` active). The reward-distribution window is wide by design (10% of epoch slots), which maximizes the chance that ordinary stake operations (merge, split, redelegate, withdraw) execute on an account before its specific partition is processed. Given the code comment explicitly flags this as an assumption rather than an enforced guarantee, and the assert is a hard panic rather than a handled error (contrast with the `Err(DistributionError::...)` handling used elsewhere in the same function), this warrants direct verification against the stake program's instruction-level checks, which could not be fully confirmed from the indexed code.

### Recommendation
Replace the hard `assert_eq!` in `build_updated_stake_reward` with the same defensive handling used for `AccountNotFound`/`ArithmeticOverflow` — return a `DistributionError` and burn/skip that specific reward instead of panicking the whole node when the live delegation no longer matches the calculation-time snapshot. Additionally, explicitly verify (and, if missing, add) a check in the stake program's instruction processors that rejects delegation-mutating instructions (`Merge`, `Split`, `Redelegate`, `Withdraw` below rent-exempt, etc.) against a stake account while it has a pending, undistributed partitioned reward, so the invariant currently only assumed in a comment is actually enforced.

### Proof of Concept
1. Advance a bank to an epoch boundary with `PartitionedEpochRewardsConfig` forcing multiple distribution blocks (as in `create_reward_bank_with_specific_stakes` in the existing test suite) [9](#0-8) , so that at least one stake account's reward is calculated but its distribution partition lands several blocks later.
2. Before that account's distribution block height is reached, execute an ordinary, unprivileged stake-authority transaction that changes the account's `delegation.stake` (e.g., a `Merge` of another stake account into it, or a partial `Withdraw`/`Redelegate`), analogous to how the existing `test_delegation_adjustment_at_distribution` test manually mutates lamports on the account before distribution [10](#0-9)  — but here with `relax_post_exec_min_balance_check` inactive so `adjust_delegations_for_rent` is `false`.
3. Advance to the account's distribution block and call `distribute_partitioned_epoch_rewards`; `build_updated_stake_reward`'s `assert_eq!(expected_delegation, new_stake.delegation.stake, ...)` fires because the live delegation no longer matches the value implied by the stale calculation-time snapshot [3](#0-2) , panicking every validator that processes the block.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L580-608)
```rust
    /// Retrieves stake history and delegations for stake reward recalculation
    /// after snapshot restore.
    fn get_epoch_params_for_recalculation<'a>(
        &'a self,
        rewarded_epoch: Epoch,
        stakes: &'a Stakes<StakeAccount<Delegation>>,
    ) -> EpochRewardCalculateParamInfo<'a> {
        // Use `stakes` for stake-related info
        let stake_history = stakes.history().clone();
        let stake_delegations = stakes.stake_delegations_vec();

        // Use the VAT-filtered vote-account snapshot from epoch_stakes.
        // Recalculation should match the vote-account admission policy used for
        // distribution.
        let leader_schedule_epoch = self.epoch_schedule().get_leader_schedule_epoch(self.slot());
        let distribution_epoch_vote_accounts = self
            .epoch_stakes(leader_schedule_epoch)
            .expect("calculation should always run after Bank::update_epoch_stakes()")
            .stakes()
            .vote_accounts();
        let cached_vote_accounts =
            self.get_cached_vote_accounts(rewarded_epoch, distribution_epoch_vote_accounts);

        EpochRewardCalculateParamInfo {
            stake_history,
            stake_delegations,
            cached_vote_accounts,
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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L2939-2954)
```rust
    #[test]
    fn test_initialize_after_snapshot_restore() {
        let expected_num_stake_rewards = 4;
        let num_rewards_per_block = 2;
        // Distribute 4 rewards over 2 blocks
        let stakes = vec![
            100_000_000,   // valid delegation
            2_000_000_000, // valid delegation
            3_000_000_000, // valid delegation
            4_000_000_000, // valid delegation
        ];
        let (RewardBank { bank, .. }, bank_forks) = create_reward_bank_with_specific_stakes(
            stakes,
            num_rewards_per_block,
            SLOTS_PER_EPOCH - 1,
        );
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L80-149)
```rust
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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L341-345)
```rust
        let feature_snapshot = self.feature_set.snapshot();
        // Name intentionally doesn't match -- "adjust delegations for rent" is
        // part of relaxing post-exec min balance checks.
        let adjust_delegations_for_rent = feature_snapshot.relax_post_exec_min_balance_check;
        let use_fixed_point_stake_math = feature_snapshot.upgrade_bpf_stake_program_to_v5_1;
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L360-366)
```rust
        let mut updated_stake_rewards = Vec::with_capacity(indices.len());
        let stakes_cache = self.stakes_cache.stakes();
        let stakes_cache_accounts = stakes_cache.stake_delegations();
        let stake_history = stakes_cache.history();
        let new_warmup_cooldown_rate_epoch = self.new_warmup_cooldown_rate_epoch();
        let rent = &self.rent_collector.rent;
        for index in indices {
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L1214-1265)
```rust
    #[test]
    fn test_delegation_adjustment_at_distribution() {
        let (mut genesis_config, _mint_keypair) =
            create_genesis_config(1_000_000 * LAMPORTS_PER_SOL);
        genesis_config.epoch_schedule = EpochSchedule::custom(432000, 432000, false);
        let bank = Bank::new_for_tests(&genesis_config);

        // Set up epoch_rewards sysvar with rewards with 10e9 lamports to distribute.
        let total_rewards = 10 * LAMPORTS_PER_SOL;
        let block_rewards = 0;
        let num_partitions = 2; // num_partitions is arbitrary and unimportant for this test
        let total_points = (total_rewards * 42) as u128; // total_points is arbitrary for the purposes of this test
        bank.create_epoch_rewards_sysvar(
            0,
            42,
            num_partitions,
            &PointValue {
                rewards: total_rewards,
                points: total_points,
            },
            block_rewards,
        );
        let pre_epoch_rewards_account = bank.get_account(&sysvar::epoch_rewards::id()).unwrap();
        let expected_balance =
            bank.get_minimum_balance_for_rent_exemption(pre_epoch_rewards_account.data().len());
        // Expected balance is the sysvar rent-exempt balance
        assert_eq!(pre_epoch_rewards_account.lamports(), expected_balance);

        // Use lower lamports per byte for creating, bank has higher amount
        let mut lower_rent = bank.rent_collector.rent.clone();
        lower_rent.lamports_per_byte /= 10;

        // Below new minimum, small reward, should normally be destaked
        let reward_lamports = 1;
        let reward = PartitionedStakeReward::new_with_lamport_amounts(reward_lamports, 0, 1);
        let rewards_to_distribute = reward.inflation.stake_reward;
        let stake_pubkey = reward.stake_pubkey;
        let stake_rewards = [reward];
        populate_starting_stake_accounts_from_stake_rewards(&bank, &lower_rent, &stake_rewards);
        let mut stake_account = bank.get_account(&stake_pubkey).unwrap();

        let expected_num = 1;

        let partitioned_rewards = StartBlockHeightAndPartitionedRewards {
            distribution_starting_block_height: bank.block_height() + REWARD_CALCULATION_NUM_BLOCKS,
            all_stake_rewards: Arc::new(stake_rewards.into_iter().collect()),
            partition_indices: vec![(0..expected_num).collect::<Vec<_>>()],
        };

        // But we transfer in more lamports before distribution time
        stake_account.checked_add_lamports(1_000_000_000).unwrap();
        bank.store_account(&stake_pubkey, &stake_account);
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
