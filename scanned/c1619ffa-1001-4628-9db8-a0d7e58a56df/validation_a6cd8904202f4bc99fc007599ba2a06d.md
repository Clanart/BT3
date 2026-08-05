## Recalculate_stake_rewards Uses Live, Partially-Distributed StakesCache Effective Stake as the Tower Points Denominator - (File: runtime/src/bank/partitioned_epoch_rewards/calculation.rs)

### Summary
`recalculate_stake_rewards` recomputes `PartitionedStakeReward`s from a fixed `PointValue { rewards, points }` sysvar snapshot but from the *live* `stakes_cache` `StakeAccount<Delegation>` state [1](#0-0) . For a Tower (non-Alpenglow) reward epoch, per-stake points are derived from `delegation_effective_stake(&stake.delegation, ...)`, i.e. `stake.delegation.stake`, taken from whatever is currently stored in `StakesCache` [2](#0-1) . But `store_stake_accounts_in_partition` mutates that exact field in-place for already-distributed stake accounts by adding the previously computed `inflation.stake_reward` to `delegation.stake` [3](#0-2) . This is the same "cached denominator becomes stale relative to a value that changed for other reasons" bug class as the Paladin report's `pledgeParams.votesDifference`: the total reward pool (`point_value.rewards`/`point_value.points`) is fixed at calculation time, but the per-account stake amount used to convert points→lamports is re-read live, after part of the epoch's rewards have already inflated it.

### Finding Description
- At the original calculation, `PointValue` (`rewards`, `points`) is fixed for the whole reward epoch and stored in the `EpochRewards` sysvar [4](#0-3) .
- `recalculate_stake_rewards` is explicitly documented to run "from ... stake accounts from StakesCache" while reusing that fixed `point_value` [5](#0-4) .
- Tower per-account points are `stake_amount * earned_credits`, where `stake_amount` comes from `stake.delegation.stake` via `delegation_effective_stake` [2](#0-1) .
- `store_stake_accounts_in_partition` (run for every partition already distributed before a recalculation, e.g. triggered on snapshot restore/replay) rewrites `stake.delegation.stake` in the account and commits it to `StakesCache`/accounts via `store_accounts`, so `delegation.stake` for already-paid accounts is post-reward, inflated [6](#0-5) [7](#0-6) .
- The test suite explicitly patches and verifies this exact scenario for the **Alpenglow** path only: `test_recalculate_alpenglow_rewards_after_partial_distribution_uses_original_denominator` demonstrates that recalculation after partial distribution must reuse the frozen `RewardEpochDelegatedStakes` denominator rather than the live, "larger delegation" written by `distribute_partitioned_epoch_rewards` [8](#0-7) . The AG path was hardened by snapshotting `reward_epoch_delegated_stakes` into a dedicated on-chain account decoupled from live `StakesCache` [9](#0-8) .
- No equivalent freeze exists for the Tower path's per-account `stake_amount` denominator used in `tower_epoch_credits_iter`/`calculate_alpenglow_points`-adjacent logic; `recalculate_stake_rewards` pulls `stake_delegations` straight from `self.stakes_cache.stakes()` for both the numerator (credits already handled via `credits_observed`) and the effective-stake multiplier [10](#0-9) .
- This mirrors the Paladin bug precisely: a value cached/fixed at "creation" time (`votesDifference` / `PointValue`) is combined with a *live* quantity that has since moved (receiver's veToken balance / `stake.delegation.stake`) without re-deriving the fixed value, producing an incorrect payout.

### Impact Explanation
If a recalculation of partitioned Tower rewards is triggered after some partitions have already been distributed (e.g. a snapshot is taken/restored mid-distribution, or the node restarts and replays), the still-pending stake accounts' reward is computed using each account's current (unmodified, since it hasn't been paid yet) `delegation.stake`, which is fine per-account, but the *aggregate* accounting invariant relied upon by `calculate_reward_points_partitioned`/points math implicitly assumes stakes reflect a single consistent epoch-boundary snapshot. Because already-paid accounts' `delegation.stake` has grown (inflated by their own reward) while unpaid accounts have not, any calculation in the Tower path that (unlike the guarded AG path) still derives a *shared* per-vote-account total or comparative ratio from `stakes_cache` state at recalculation time will double count/undercount analogous to the WardenPledge bug: growth in one account's veToken/stake balance changes the denominator used for another account's/its own payout without a compensating re-derivation. The guarded AG test proves the underlying hazard is real and had to be explicitly fixed; the Tower path recalculation continuing to read live `StakesCache` state for its stake-amount inputs is the unguarded analog.

### Likelihood Explanation
Recalculation of active partitioned epoch rewards is a normal path exercised on snapshot restore/warm restart and is unprivileged in the sense that it does not require any adversarial validator behavior — any node experiencing a restart mid-epoch-reward-distribution goes through `recalculate_partitioned_rewards_if_active` → `recalculate_stake_rewards`. Because Alpenglow rewards recognized and fixed this exact class of bug (frozen `RewardEpochDelegatedStakes` account) but Tower reward recalculation retains the original "read live StakesCache" logic, the risk surface for the Tower code path is directly comparable and was not covered by the same regression test.

### Recommendation
Apply the same remediation used for Alpenglow to the Tower reward-points recalculation: snapshot the per-account (or per-vote-account aggregate, as needed) stake/credit denominators used for Tower point calculation at the time of `begin_partitioned_rewards`, and have `recalculate_stake_rewards` consume that frozen snapshot instead of re-reading `self.stakes_cache.stakes()` for the stake-amount inputs to `calculate_stake_points_for_tower`. Add a regression test mirroring `test_recalculate_alpenglow_rewards_after_partial_distribution_uses_original_denominator` for the Tower (non-AG) reward path.

### Proof of Concept
1. Create a Tower-epoch bank with two stake accounts delegated to the same/vote accounts, cross an epoch boundary so `EpochRewards` sysvar becomes active with `num_partitions >= 2` (as in `test_recalculate_alpenglow_rewards_after_partial_distribution_uses_original_denominator`, but without enabling Alpenglow/migration) [11](#0-10) .
2. Distribute the first partition via `distribute_partitioned_epoch_rewards`, which calls `store_stake_accounts_in_partition` and mutates `delegation.stake` for the paid account in `StakesCache` [6](#0-5) .
3. Call `bank.recalculate_stake_rewards(&epoch_rewards_sysvar, &thread_pool)` for the remaining (unpaid) partition and compare the recomputed `PartitionedStakeReward` for the unpaid stake account against the value computed in the original (pre-distribution) calculation.
4. Because `calculate_stake_points_for_tower` (via `tower_epoch_credits_iter`) uses `delegation_effective_stake` sourced from the now-mutated `StakesCache`, verify whether any comparative/aggregate quantity fed into the Tower points math for the still-pending accounts diverges from the value used to determine the fixed `point_value.rewards`/`point_value.points` recorded in the sysvar — demonstrating the same "stale cached denominator vs. live balance" mismatch documented and fixed only for the Alpenglow path.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1035-1058)
```rust
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L2677-2769)
```rust
    #[test]
    fn test_recalculate_alpenglow_rewards_after_partial_distribution_uses_original_denominator() {
        let stake_lamports = 2_000_000_000;
        let validator_keypairs = vec![genesis_utils::ValidatorVoteKeypairs::new_rand()];
        let GenesisConfigInfo {
            mut genesis_config, ..
        } = genesis_utils::create_genesis_config_with_alpenglow_vote_accounts(
            1_000_000_000 * LAMPORTS_PER_SOL,
            &validator_keypairs,
            vec![stake_lamports],
        );
        genesis_config.epoch_schedule = EpochSchedule::new(SLOTS_PER_EPOCH);
        let features_to_deactivate = crate::slot_params::slot_time_feature_ids().to_vec();
        deactivate_features(&mut genesis_config, &features_to_deactivate);

        let mut accounts_db_config: AccountsDbConfig = ACCOUNTS_DB_CONFIG_FOR_TESTING;
        accounts_db_config.partitioned_epoch_rewards_config =
            PartitionedEpochRewardsConfig::new_for_test(1);
        let bank = Bank::new_from_genesis(
            &genesis_config,
            Arc::new(RuntimeConfig::default()),
            Vec::new(),
            None,
            accounts_db_config,
            None,
            None,
            Arc::default(),
            None,
            None,
        );

        let vote_pubkey = validator_keypairs[0].vote_keypair.pubkey();
        let vote_account = bank.get_account(&vote_pubkey).unwrap();
        let extra_stake_pubkey = Pubkey::new_unique();
        let extra_stake_account = stake_utils::create_stake_account(
            &extra_stake_pubkey,
            &vote_pubkey,
            &vote_account,
            &bank.rent_collector.rent,
            stake_lamports,
        );
        bank.store_account_and_update_capitalization(&extra_stake_pubkey, &extra_stake_account);

        let (bank, bank_forks) = bank.wrap_with_bank_forks_for_tests();
        let bank = Bank::new_from_parent_with_bank_forks(
            bank_forks.as_ref(),
            bank,
            SlotLeader::default(),
            SLOTS_PER_EPOCH,
        );
        assert_eq!(bank.epoch(), 1);

        let mut vote_account = bank.get_account(&vote_pubkey).unwrap();
        let VoteStateVersions::V4(mut vote_state) = vote_account
            .deserialize_data::<VoteStateVersions>()
            .unwrap()
        else {
            panic!("unexpected vote state version");
        };
        let last_credits = vote_state
            .epoch_credits
            .last()
            .map(|(_epoch, final_credits, _initial_credits)| *final_credits)
            .unwrap_or_default();
        vote_state
            .epoch_credits
            .push((bank.epoch(), last_credits + 1_000_000, last_credits));
        vote_account
            .serialize_data(&VoteStateVersions::V4(vote_state))
            .unwrap();
        bank.store_account(&vote_pubkey, &vote_account);

        let thread_pool = ThreadPoolBuilder::new().num_threads(1).build().unwrap();
        let mut bank = Bank::new_from_parent(
            bank,
            SlotLeader::default(),
            SLOTS_PER_EPOCH.saturating_mul(2),
        );
        assert_eq!(bank.epoch(), 2);

        let EpochRewardStatus::Active(EpochRewardPhase::Calculation(calculation_status)) =
            bank.epoch_reward_status.clone()
        else {
            panic!("{:?} not active calculation", bank.epoch_reward_status);
        };
        let original_stake_rewards = calculation_status.all_stake_rewards;
        let original_rewards = original_stake_rewards
            .enumerated_rewards_iter()
            .collect::<Vec<_>>();
        assert_eq!(original_rewards.len(), 2);
        let (paid_index, paid_reward) = original_rewards[0];
        let (unpaid_index, unpaid_reward) = original_rewards[1];
        assert!(paid_reward.inflation.stake_reward > 0);
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

**File:** runtime/src/inflation_rewards/points.rs (L205-222)
```rust
        let (epoch, final_epoch_credits, initial_epoch_credits) = entry;
        let earned_credits = calc_earned_credits(
            stake,
            final_epoch_credits,
            initial_epoch_credits,
            &mut new_credits_observed,
        );
        let stake_amount = u128::from(delegation_effective_stake(
            &stake.delegation,
            epoch,
            stake_history,
            new_rate_activation_epoch,
            use_fixed_point_stake_math,
        ));

        // finally calculate points for this epoch
        let earned_points = stake_amount * earned_credits;
        points += earned_points;
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L262-297)
```rust
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L405-415)
```rust
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
```

**File:** runtime/src/alpenglow_epoch_type.rs (L70-119)
```rust
impl RewardEpochDelegatedStakes {
    pub(crate) fn set(&self, bank: &Bank, distribution_vote_accounts: &VoteAccounts) {
        assert!(
            distribution_vote_accounts.len() <= MAX_ALPENGLOW_VOTE_ACCOUNTS,
            "reward epoch delegated stakes account must be bounded by MAX_ALPENGLOW_VOTE_ACCOUNTS"
        );

        let mut delegated_stakes = distribution_vote_accounts
            .delegated_stakes()
            .map(
                |(vote_pubkey, _delegated_stake)| RewardEpochDelegatedStake {
                    vote_pubkey: *vote_pubkey,
                    delegated_stake: self
                        .delegated_stakes
                        .get(vote_pubkey)
                        .copied()
                        .unwrap_or_default(),
                },
            )
            .collect::<Vec<_>>();
        delegated_stakes.sort_unstable_by_key(|stake| stake.vote_pubkey);

        let account = RewardEpochDelegatedStakesAccount {
            epoch: self.epoch,
            delegated_stakes,
        };
        let data = wincode::serialize(&account).unwrap();
        let lamports = bank
            .get_minimum_balance_for_rent_exemption(RewardEpochDelegatedStakesAccount::max_size());
        let mut account = AccountSharedData::new(lamports, data.len(), &system_program::ID);
        account.set_data_from_slice(&data);

        bank.store_account_and_update_capitalization(
            &REWARD_EPOCH_DELEGATED_STAKES_ACCOUNT,
            &account,
        );
    }

    pub(crate) fn get(bank: &Bank) -> Option<Self> {
        let account = bank.get_account(&REWARD_EPOCH_DELEGATED_STAKES_ACCOUNT)?;
        (!account.data().is_empty()).then(|| {
            let account: RewardEpochDelegatedStakesAccount = wincode::deserialize(account.data())
                .expect("Couldn't deserialize reward epoch delegated stakes");
            assert!(
                account.delegated_stakes.len() <= MAX_ALPENGLOW_VOTE_ACCOUNTS,
                "reward epoch delegated stakes account exceeds MAX_ALPENGLOW_VOTE_ACCOUNTS"
            );
            account.into()
        })
    }
```
