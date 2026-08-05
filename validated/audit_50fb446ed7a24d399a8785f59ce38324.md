Based on the investigation, the closest verified analog to the Sherlock bug (a value snapshotted in a "step 1" assumed frozen until "step 2," but changed by a legitimate side path in between, breaking a downstream accounting invariant) is in Agave's partitioned epoch-rewards distribution code.

### Title
Stale delegation-snapshot assumption in `build_updated_stake_reward` can trigger a deterministic validator panic (`assert_eq!`) during partitioned epoch-reward distribution - (File: `runtime/src/bank/partitioned_epoch_rewards/distribution.rs`)

### Summary
Agave computes stake rewards once at the epoch boundary and pays them out over many subsequent blocks ("partitioned epoch rewards"). Between the calculation snapshot and the actual payout block for a given stake account, `build_updated_stake_reward` assumes the account's `delegation.stake` field has not changed except by the reward itself. This assumption is enforced with a hard `assert_eq!` (not a recoverable error) when the "adjust delegations for rent" code path is inactive.

### Finding Description
`build_updated_stake_reward` re-reads the stake account from the live `stakes_cache` at distribution time and compares it against the value that was captured during the earlier calculation phase: [1](#0-0) 

If `adjust_delegations_for_rent` (gated by the `relax_post_exec_min_balance_check` feature) is *not* active, the code takes the `else` branch and hard-asserts that `stake.delegation.stake + stake_reward == new_stake.delegation.stake`, where `stake` is the **current** delegation read from the cache and `new_stake` is the delegation that was **computed during the calculation phase**, potentially many blocks earlier [2](#0-1) .

This mirrors exactly the pattern in the Sherlock report: a value (`allocatedBalances[partyA]` there, `delegation.stake` here) is captured at "step 1" (liquidation trigger / reward calculation) and a later "step 2" (`deferredSetSymbolsPrice` / `build_updated_stake_reward`) blindly assumes it is unchanged, using it in an equation that has no tolerance for drift.

The code's own comment acknowledges this reliance on an external invariant it cannot itself verify: [3](#0-2) 

i.e., the correctness of this assert (and the whole "no burned reward" invariant) depends entirely on the stake program disallowing any state mutation to `delegation.stake` for accounts holding a not-yet-distributed reward. This repository does not contain the stake program's instruction processor (`programs/stake` is empty in this index, and no `EpochRewards`/`reward interval` guard was found anywhere in the indexed code), so I could not independently confirm that such a block exists or covers every stake-mutating instruction (e.g., `Merge`, `Split`, `Deactivate`, `Redelegate`) for the entire distribution window, especially across the `recalculate_partitioned_rewards_if_active` path used when the bank tree is re-rooted mid-distribution, which recomputes rewards from a **fresh** `stakes_cache` snapshot rather than replaying the original one: [4](#0-3) [5](#0-4) 

Anyone can permissionlessly send lamports directly to a stake account (confirmed by an existing test, `test_rewards_period_system_transfer`) without affecting `delegation.stake` [6](#0-5) , and the existing regression test `test_delegation_adjustment_at_distribution` explicitly demonstrates the "adjust_delegations_for_rent = true" branch tolerating such drift [7](#0-6) . However, the `else`/non-adjusted branch has no such tolerance and panics on any mismatch, however it arises (legitimate stake-authority operation racing with an in-flight but not-yet-paid reward, a bug/gap in the stake-program's reward-interval restriction, or a discrepancy introduced by the `recalculate_partitioned_rewards_if_active` re-snapshot path on fork switch).

### Impact Explanation
The `assert_eq!` is unconditional Rust code executed by every validator processing the same block deterministically (it is not behind a `Result`/graceful error path like the `AccountNotFound`/`ArithmeticOverflow`/`UnableToSetState` cases, which are caught and only burn the reward). If the invariant is violated, **every validator hits the panic at the same block**, which is effectively a synchronized crash of the network rather than a fork or a single node's degradation — this falls into the "false execution/rooting/acceptance" / "consensus halt" impact category, since block production/validation for the whole cluster would stop simultaneously rather than just one client degrading.

### Likelihood Explanation
This is a **conditional/legacy-path** issue, not a directly attacker-triggerable bug from local evidence alone:
- It only fires when `relax_post_exec_min_balance_check` (SIMD-0392) is *not* active, i.e., on a cluster (or older Agave version / devnet) where that feature has not yet been activated. If this feature is already permanently active on the target cluster, this exact assert is dead code.
- Its trigger requires `delegation.stake` for a specific pending-reward account to diverge from the calculation-time value, which — per the code comment — is supposed to be prevented entirely by the stake program. I could not verify the completeness of that guard in this codebase (the stake program source isn't present in this index), nor whether the `recalculate_partitioned_rewards_if_active` re-snapshot path (used on bank re-rooting during the distribution window) can itself introduce a stale-vs-fresh mismatch when combined with `build_updated_stake_reward`'s separate re-fetch from `stakes_cache`.
- Because of these unknowns, I cannot assert this is exploitable purely from local code; it is best characterized as a structural fragility (a hard, non-recoverable `assert_eq!` guarding an invariant the local code cannot itself enforce) rather than a confirmed, concretely reachable path.

### Recommendation
- Replace the `assert_eq!` in the `else` branch of `build_updated_stake_reward` with a recoverable `DistributionError` (mirroring the `ArithmeticOverflow`/`UnableToSetState` handling) so any invariant violation results in a burned/logged reward instead of a cluster-wide panic.
- Independently verify (in the stake program source, not present in this index) that every stake-mutating instruction (`Split`, `Merge`, `Deactivate`, `DeactivateDelinquent`, `Redelegate`, `Withdraw`) is unconditionally blocked for any stake account with an outstanding partitioned reward for the entire distribution window, including across the `recalculate_partitioned_rewards_if_active` re-snapshot path.

### Proof of Concept
Not constructible with certainty from local repository evidence alone: the stake-program-side enforcement that this invariant depends on is not present in this index, so I cannot demonstrate a concrete transaction sequence that mutates `delegation.stake` during the vulnerable window. The existing unit tests `test_build_updated_stake_reward` and `test_delegation_adjustment_at_distribution` [8](#0-7) [9](#0-8)  confirm the assert exists and is reachable under `adjust_delegations_for_rent = false`, but do not exercise a scenario where `delegation.stake` itself (not just lamports) diverges between calculation and distribution — recommend a Devin/engineering follow-up to pull in the stake program source and attempt to construct such a divergence (e.g., via `recalculate_partitioned_rewards_if_active` racing a same-epoch `Merge`/`Split`) before treating this as a confirmed, exploitable finding.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L249-256)
```rust
        let stake_account = stakes_cache_accounts
            .get(&partitioned_stake_reward.stake_pubkey)
            .ok_or(DistributionError::AccountNotFound)?
            .clone();

        let (mut account, stake_state): (AccountSharedData, StakeStateV2) = stake_account.into();
        let StakeStateV2::Stake(meta, stake, flags) = stake_state else {
            // StakesCache only stores accounts where StakeStateV2::delegation().is_some()
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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L327-336)
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
    fn store_stake_accounts_in_partition(
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L768-833)
```rust
    #[test_case(true; "adjust_delegations_for_rent")]
    #[test_case(false; "no_adjust_delegations_for_rent")]
    fn test_build_updated_stake_reward(adjust_delegations_for_rent: bool) {
        let (genesis_config, _mint_keypair) = create_genesis_config(1_000_000 * LAMPORTS_PER_SOL);
        let bank = Bank::new_for_tests(&genesis_config);
        // add an entry so we can get full deactivation one epoch later
        let mut stake_history = StakeHistory::default();
        stake_history.add(
            0,
            StakeHistoryEntry {
                effective: 1_000_000 * LAMPORTS_PER_SOL,
                activating: LAMPORTS_PER_SOL,
                deactivating: LAMPORTS_PER_SOL,
            },
        );

        let distribution_epoch = bank.epoch + 1;
        let new_warmup_cooldown_rate_epoch = bank.new_warmup_cooldown_rate_epoch();
        let mut rent = bank.rent_collector.rent.clone();
        let rent_exempt_reserve = rent.minimum_balance(StakeStateV2::size_of());

        // Adjust rent down, no impact at all
        if adjust_delegations_for_rent {
            rent.lamports_per_byte /= 2;
        }

        let voter_pubkey = Pubkey::new_unique();
        let new_stake = Stake {
            delegation: Delegation {
                voter_pubkey,
                stake: 55_555,
                ..Delegation::default()
            },
            credits_observed: 42,
        };
        let stake_reward = 100;
        let block_reward = 10_000;
        let commission_bps = 4_200;

        let nonexistent_account = Pubkey::new_unique();
        let partitioned_stake_reward = PartitionedStakeReward {
            stake_pubkey: nonexistent_account,
            inflation: InflationReward {
                stake: new_stake,
                stake_reward,
                commission_bps: Some(commission_bps),
            },
            block_reward,
        };
        let stakes_cache = bank.stakes_cache.stakes();
        let stakes_cache_accounts = stakes_cache.stake_delegations();
        assert_eq!(
            Bank::build_updated_stake_reward(
                distribution_epoch,
                &stake_history,
                new_warmup_cooldown_rate_epoch,
                stakes_cache_accounts,
                &partitioned_stake_reward,
                &rent,
                adjust_delegations_for_rent,
                true,
            )
            .unwrap_err(),
            DistributionError::AccountNotFound
        );
        drop(stakes_cache);
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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1035-1046)
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1063-1088)
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/mod.rs (L1082-1120)
```rust
    /// Test that lamports can be sent to stake accounts regardless of rewards period.
    #[test]
    fn test_rewards_period_system_transfer() {
        let validator_vote_keypairs = ValidatorVoteKeypairs::new_rand();
        let validator_keypairs = vec![&validator_vote_keypairs];
        let GenesisConfigInfo {
            mut genesis_config,
            mint_keypair,
            ..
        } = create_genesis_config_with_vote_accounts(
            1_000_000_000,
            &validator_keypairs,
            vec![1_000_000_000; 1],
        );

        // Add stake account to try to mutate
        let vote_key = validator_keypairs[0].vote_keypair.pubkey();
        let vote_account = genesis_config
            .accounts
            .iter()
            .find(|(address, _)| **address == vote_key)
            .map(|(_, account)| account)
            .unwrap()
            .clone();

        let new_stake_signer = Keypair::new();
        let new_stake_address = new_stake_signer.pubkey();
        let new_stake_account = Account::from(stake_utils::create_stake_account(
            &new_stake_address,
            &vote_key,
            &vote_account.into(),
            &genesis_config.rent,
            2_000_000_000,
        ));
        genesis_config
            .accounts
            .extend(vec![(new_stake_address, new_stake_account)]);

        let (mut previous_bank, bank_forks) = Bank::new_with_bank_forks_for_tests(&genesis_config);
```
