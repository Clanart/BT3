I found a concrete Agave analog to the reported bug class: a value snapshotted at "calculation time" is later applied against live state at "distribution time," and the code enforces the invariant with a hard `assert_eq!` rather than gracefully recomputing/failing safely — a deterministic panic on all nodes.

### Title
Stale stake-delegation invariant assertion in partitioned epoch-reward distribution can panic all validators - ([File: runtime/src/bank/partitioned_epoch_rewards/distribution.rs])

### Summary
Partitioned epoch rewards in Agave are calculated once at the epoch boundary and then applied to stake accounts over many subsequent blocks/partitions. `build_updated_stake_reward` re-derives the expected post-reward delegation from the *current* (distribution-time) stake account read out of `stakes_cache_accounts`, and asserts that it exactly equals `stake.delegation.stake + partitioned_stake_reward.inflation.stake_reward` computed with data captured at calculation time. If the stake account's delegation is legitimately mutated between calculation and the specific block where its partition is distributed (any window up to ~10% of an epoch's slots), the assert fails and panics, exactly mirroring the reported "value computed with stale conversion rate, then used later after state changed" pattern — except here the mismatch is enforced with a crash rather than silently wrong math.

### Finding Description
`calculate_stake_rewards_and_commissions` computes `PartitionedStakeReward` (including the new `Stake.delegation.stake` value) once, at the first block of the epoch, from a stakes-cache snapshot. [1](#0-0) 

That result is stored in `Bank::epoch_reward_status` and applied lazily, partition by partition, across up to `slots_per_epoch / 10` blocks via `distribute_partitioned_epoch_rewards` → `distribute_epoch_rewards_in_partition` → `store_stake_accounts_in_partition` → `build_updated_stake_reward`. [2](#0-1) [3](#0-2) 

Inside `build_updated_stake_reward`, when the `relax_post_exec_min_balance_check` feature (`adjust_delegations_for_rent`) is not active, the code takes the delegation value read live from `stakes_cache_accounts` at distribution time (`stake.delegation.stake`), adds the reward computed at calculation time, and asserts it equals the calculation-time `new_stake.delegation.stake`: [4](#0-3) 

This is the exact structural analog of the reported bug: the "conversion" (here, reward-to-delegation math) is fixed using state at time T1 (calculation), but applied/verified against state at T2 (distribution), and nothing in the transaction/block-processing pipeline prevents state at T1 and T2 from diverging. The code comment explicitly acknowledges the danger of this assumption but treats it as guaranteed by "stake-program restrictions" rather than proving it: [5](#0-4) 

The `Delegation::stake` field on an existing stake account can change between the calculation block and a later distribution block through ordinary, unprivileged user-signed stake-program instructions processed as normal transactions in intervening blocks (e.g. `Split`, `Merge`, `Withdraw`, `Deactivate`/`Redelegate`, `MergeStake`) — I could not find, within the searched code, any check in the stake program's instruction processing or in `Bank::get_reward_interval`/`RewardInterval` that rejects or defers such instructions while `EpochRewardStatus::Active` is set for that stake account. `get_reward_interval` only affects when the *bank* recomputes/distributes rewards; it does not appear to gate ordinary stake-program instruction execution against accounts with a pending reward. [6](#0-5) 

The existing regression tests only cover the case where `adjust_delegations_for_rent` is true (rent-driven adjustment) or where lamports are added without delegation change; none of the located tests exercise a legitimate user-driven delegation change (e.g. `Split`) landing between calculation and a stake account's specific distribution partition while `adjust_delegations_for_rent` is false, which is precisely the branch that hits the raw `assert_eq!` instead of the tolerant `adjust_delegation_for_rent` path. [7](#0-6) 

### Impact Explanation
This is not a silent fund-loss bug like the reported SY-token case; it is stronger in a validator context: hitting the `assert_eq!` panics bank/block processing. Because reward calculation and distribution are deterministic functions of on-chain state that every validator replays identically, every conforming validator that processes the block containing the affected partition would hit the exact same assertion and crash — a network-wide consensus halt (all nodes crash identically rather than diverging into different forks, but the cluster still stops producing/confirming blocks), which matches the "consensus halt" impact class explicitly listed as valid for this analog task.

### Likelihood Explanation
Likelihood depends on: (1) `relax_post_exec_min_balance_check` not being active for the branch that hits the raw assert instead of the tolerant rent-adjustment path, and (2) a staker performing an ordinary stake operation (`Split`, `Merge`, `Withdraw`, `Deactivate`) on their own stake account during the up-to-slots_per_epoch/10-block window between epoch-boundary calculation and that account's specific distribution partition. Because the distribution window can span many thousands of slots and stake operations are completely unprivileged/user-initiated, this is a plausible, low-cost, no-special-access trigger — not a malicious-peer, malicious-validator, or trusted-integration precondition. I was not able to fully verify whether the `relax_post_exec_min_balance_check` feature is unconditionally active on current mainnet-beta (this affects likelihood), and I could not conclusively rule out a stake-program-side restriction elsewhere in the codebase outside the indexed portions of this repository; this should be independently confirmed by a Devin session with full repo access before treating this as fully verified.

### Recommendation
Replace the hard `assert_eq!` in `build_updated_stake_reward` (the `!adjust_delegations_for_rent` branch) with a tolerant recomputation path analogous to `adjust_delegation_for_rent`: derive the post-reward delegation from the live, distribution-time stake state plus the stored reward amount, rather than asserting equality against a stale calculation-time delegation value. Alternatively/additionally, gate stake-modifying instructions (`Split`, `Merge`, `Withdraw`, `Deactivate`, `Redelegate`) so they cannot execute against a stake account that has a pending, not-yet-distributed partitioned reward, closing the window between calculation and distribution entirely.

### Proof of Concept
Conceptual reproduction (based on the located test harness patterns):
1. Configure a bank with `partitioned_epoch_rewards_config` such that the reward distribution spans multiple blocks, and ensure `relax_post_exec_min_balance_check` is inactive.
2. Advance to the epoch boundary so `calculate_stake_rewards_and_commissions` computes a `PartitionedStakeReward` for a given stake account, capturing its `Stake.delegation.stake` at that instant, per `calculate_stake_rewards_and_commissions`. [8](#0-7) 
3. Before the block corresponding to that account's distribution partition, submit an ordinary `StakeInstruction::Split` (or `Withdraw`) transaction from the stake authority that changes the account's live `delegation.stake` in `stakes_cache_accounts`.
4. Advance to the block that calls `distribute_partitioned_epoch_rewards` for that account's partition; `build_updated_stake_reward` computes `expected_delegation` from the now-changed live `stake.delegation.stake` and compares it to the pre-computed `new_stake.delegation.stake`, tripping the `assert_eq!` and panicking bank replay for every validator. [9](#0-8)

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L813-827)
```rust
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L859-871)
```rust
                            let stake_reward = inflation.stake_reward;
                            (
                                Some(PartitionedStakeReward {
                                    stake_pubkey: **stake_pubkey,
                                    inflation,
                                    block_reward,
                                }),
                                Some(RewardAccumulation {
                                    stake_reward,
                                    commission: Some((commission_pubkey, reward_commission)),
                                }),
                            )
                        }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L145-149)
```rust
        if height >= distribution_starting_block_height && height < distribution_end_exclusive {
            let partition_index = height - distribution_starting_block_height;

            self.distribute_epoch_rewards_in_partition(partition_rewards, partition_index);
        }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L175-190)
```rust
    fn distribute_epoch_rewards_in_partition(
        &self,
        partition_rewards: &StartBlockHeightAndPartitionedRewards,
        partition_index: u64,
    ) {
        let pre_capitalization = self.capitalization();
        let (
            DistributionResults {
                stake_reward_lamports_minted,
                stake_reward_lamports_burned,
                block_reward_lamports_distributed,
                block_reward_lamports_burned,
                updated_stake_rewards,
            },
            store_stake_accounts_us,
        ) = measure_us!(self.store_stake_accounts_in_partition(partition_rewards, partition_index));
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L283-294)
```rust
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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L1214-1293)
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

        // Distribute rewards
        let pre_cap = bank.capitalization();
        bank.distribute_epoch_rewards_in_partition(&partitioned_rewards, 0);
        let post_cap = bank.capitalization();
        let post_epoch_rewards_account = bank.get_account(&sysvar::epoch_rewards::id()).unwrap();

        // Assert that epoch rewards sysvar lamports balance does not change
        assert_eq!(post_epoch_rewards_account.lamports(), expected_balance);

        let epoch_rewards: sysvar::epoch_rewards::EpochRewards =
            from_account(&post_epoch_rewards_account).unwrap();
        assert_eq!(epoch_rewards.total_rewards, total_rewards);
        assert_eq!(epoch_rewards.distributed_rewards, rewards_to_distribute,);

        // Assert that the bank total capital changed by the amount of rewards
        // distributed
        assert_eq!(pre_cap + rewards_to_distribute, post_cap);

        // Check that delegation just gets rewards
        let post_account = bank.get_account(&stake_pubkey).unwrap();
        let post_stake_state: StakeStateV2 = post_account.state().unwrap();
        let pre_stake_state: StakeStateV2 = stake_account.state().unwrap();
        assert_eq!(
            post_stake_state.delegation().unwrap().stake,
            pre_stake_state.delegation().unwrap().stake + reward_lamports
        );
    }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/mod.rs (L534-564)
```rust
    #[derive(Debug, PartialEq, Eq, Copy, Clone)]
    enum RewardInterval {
        /// the slot within the epoch is INSIDE the reward distribution interval
        InsideInterval,
        /// the slot within the epoch is OUTSIDE the reward distribution interval
        OutsideInterval,
    }

    impl Bank {
        /// Return `RewardInterval` enum for current bank
        fn get_reward_interval(&self) -> RewardInterval {
            if matches!(self.epoch_reward_status, EpochRewardStatus::Active(_)) {
                RewardInterval::InsideInterval
            } else {
                RewardInterval::OutsideInterval
            }
        }

        fn is_calculated(&self) -> bool {
            matches!(
                self.epoch_reward_status,
                EpochRewardStatus::Active(EpochRewardPhase::Calculation(_))
            )
        }

        fn is_partitioned(&self) -> bool {
            matches!(
                self.epoch_reward_status,
                EpochRewardStatus::Active(EpochRewardPhase::Distribution(_))
            )
        }
```
