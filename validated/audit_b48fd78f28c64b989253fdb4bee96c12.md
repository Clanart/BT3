### Title
Reward-commission distribution reads live account balances instead of the calculation-time snapshot, letting an unprivileged account owner corrupt the reward invariant used to bound minted lamports - (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

### Summary
The external report's broken invariant is: a value used to compute a financial output (share price / `totalAssets()`) is calculated from the location of funds at one point in time, but the funds can move to/from that location before the value is actually used, corrupting the derived output. The Agave analog is in the partitioned epoch-rewards pipeline: `RewardCommission.commission_lamports` is computed once, during the **calculation** phase, from a `point_value`/commission-bps snapshot, but the account that will receive it is only loaded and mutated at **distribution** time — potentially many blocks later — via `load_and_reward_commission_accounts()`. The code explicitly documents that it is designed to read "the latest balances — including any intervening account mutations" between calculation and distribution. [1](#0-0) 

### Finding Description
`load_and_reward_commission_accounts` loads the *current* on-chain state of the commission account with `get_account_with_fixed_root_no_cache` and simply adds the pre-computed `commission_lamports` on top of whatever balance exists at that later block: [2](#0-1) 

Because `commission_lamports` was fixed during calculation (based on `point_value.rewards`/`points` and `commission_bps` at that earlier point), and the account is only re-read at distribution time, any lamports transferred into or out of that account in the intervening blocks are silently absorbed into the "post_balance" used for the `RewardInfo` and for bank accounting. The comment on `distribute_reward_commissions` even states this is intentional: “This is intentionally deferred from calculation time so that any intervening account mutations (e.g. VAT burns in `update_epoch_stakes`) are reflected.” [3](#0-2) 

The regression/behavior test `test_load_and_reward_commission_accounts_reflects_vat_burn` proves the mechanism directly: a commission is *planned* against a pre-burn balance, the account is *mutated* (burned) before distribution, and the code then applies the commission on top of the *post-burn* balance rather than the balance that existed when the commission was computed: [4](#0-3) 

An analogous divergence exists on the stake side: `store_stake_accounts_in_partition` / `build_updated_stake_reward` reads the stake account's *current* lamports at distribution time (`account.lamports()`) after adding the pre-computed inflation/block reward, and when `adjust_delegations_for_rent` is active it recomputes the *delegated stake* from that current, possibly-externally-modified balance via `adjust_delegation_for_rent`. The accompanying test explicitly demonstrates that lamports can be transferred into a stake account **after** reward calculation but **before** distribution, and the delegation ends up recomputed against that injected balance: [5](#0-4) [6](#0-5) 

The only global guard against this is the assertion in `distribute_reward_commissions`, which checks that the sum of everything actually distributed/burned across the whole epoch does not exceed the epoch-wide `point_value.rewards` ceiling: [7](#0-6) 

This guard bounds the *aggregate* payout, but it does **not** prevent per-account corruption of the commission/stake accounting: because `commission_lamports`/`stake_reward` are frozen at calculation time while the account's actual balance is read live at distribution time, an account owner (stake authority, custom commission collector under `custom_commission_collector`, or any holder of the vote/stake account's withdraw authority) can freely move funds in/out of the target account during the gap between calculation and distribution. The resulting `post_balance` recorded in `RewardInfo`/`StakeRewardInfo` — and, for stake accounts under `adjust_delegations_for_rent`, the recomputed delegated `stake` amount itself — no longer reflects the value the protocol actually intended to credit, since it is computed from a balance the account owner controls at a time of their choosing.

### Impact Explanation
This does not directly let an attacker steal lamports outright (the epoch-wide cap still holds), but it corrupts the meaning of the per-account reward/commission bookkeeping and, on the stake side, the *delegated stake* amount that feeds directly into consensus-relevant state (stake weight used for future leader schedule/voting power calculations). Because delegation amounts derived from a manipulable "current balance" snapshot can diverge from what was actually earned, this falls into the "false execution/rooting/acceptance" category of impact: a validator's effective stake weight (and thus its influence in consensus) can end up inconsistent with the actual rewards distributed, purely through fund movement timed by the unprivileged account owner between the fixed calculation phase and the multi-block-later distribution phase.

### Likelihood Explanation
Likelihood is Low: the account owner (staker/withdrawer/custom commission collector) must deliberately time a transfer into or out of their own stake/commission account during the narrow window between the epoch's reward-calculation block and their specific partition's distribution block (a window of up to `slots_per_epoch/10` blocks). This requires no privileged access and no cooperation from validators, matching the "unprivileged" criterion, but it does require an attacker to actively track epoch-boundary calculation and time an ordinary transfer transaction, which is a common but non-trivial adversarial action.

### Recommendation
Snapshot the exact pre-image account state (or at minimum the exact lamports figure used for the `expected_delegation`/`post_balance` invariant) at calculation time, and validate at distribution time that the stored state has not been mutated by an unrelated transfer, or explicitly re-derive the reward amount from the balance actually present at distribution rather than assuming the two phases can be silently blended. At minimum, tighten the `adjust_delegation_for_rent` path so it cannot use externally-injected lamports to alter the *delegated stake* figure that other consensus logic depends on.

### Proof of Concept
The existing unit tests already constitute a proof of concept of the underlying mechanism:
- `test_load_and_reward_commission_accounts_reflects_vat_burn` shows a commission planned against a pre-burn balance being credited on top of a *different*, post-mutation balance. [8](#0-7) 
- `test_delegation_adjustment_at_distribution` shows an attacker-controlled transfer of `1_000_000_000` lamports into a stake account *after* reward calculation but *before* distribution changes what the stored delegation ends up reflecting. [9](#0-8) 

**Caveat / uncertainty:** I was unable to load the full definitions of `adjust_delegation_for_rent` and `delegation_may_need_adjustment` (in `runtime/src/inflation_rewards/mod.rs`) before running out of tool iterations, so I could not fully confirm the exact bounds/clamping applied to the recomputed delegation, which would determine precisely how far the delegated-stake figure can be pushed by an injected transfer. This should be verified by a follow-up session with full file access before treating this as a confirmed, high-confidence exploit path rather than a documented behavioral gap.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L369-382)
```rust
        // Load the commission accounts and apply their rewards.
        // This is intentionally deferred from calculation time so that any
        // intervening account mutations (e.g. VAT burns in
        // `update_epoch_stakes`) are reflected.
        let (reward_commission_accounts, load_and_reward_commission_accounts_us) =
            measure_us!(self.load_and_reward_commission_accounts(reward_commissions, thread_pool));
        rewards_metrics.load_and_reward_commission_accounts_us =
            load_and_reward_commission_accounts_us;
        info!(
            "load_and_reward_commission_accounts: input_count={} output_count={} elapsed_us={}",
            reward_commissions.len(),
            reward_commission_accounts.accounts_with_rewards.len(),
            load_and_reward_commission_accounts_us,
        );
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L392-408)
```rust
        let StakeRewardCalculation {
            total_stake_rewards_lamports,
            ..
        } = stake_rewards;

        // verify that we didn't pay any more than we expected to
        assert!(
            point_value.rewards
                >= distributed_lamports
                    + distributed_to_incinerator_lamports
                    + burned_lamports
                    + total_stake_rewards_lamports,
            "point_value={point_value:?}, distributed_lamports={distributed_lamports}, \
             distributed_to_incinerator_lamports={distributed_to_incinerator_lamports} \
             burned_lamports={burned_lamports}, \
             total_stake_rewards_lamports={total_stake_rewards_lamports}"
        );
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1097-1106)
```rust
    /// Load each planned commission account from the store and apply its
    /// reward. This is the single point where commission account data is
    /// fetched, ensuring we always see the latest balances — including any
    /// intervening account mutations (e.g. VAT burns in `update_epoch_stakes`)
    /// that happen between calculation and distribution.
    fn load_and_reward_commission_accounts(
        &self,
        reward_commissions: &RewardCommissions,
        thread_pool: &ThreadPool,
    ) -> RewardCommissionAccounts {
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1128-1162)
```rust
                        let maybe_commission_account =
                            self.get_account_with_fixed_root_no_cache(commission_pubkey);
                        let mut commission_account = if custom_commission_collector {
                            // If the account doesn't exist, the vote commission
                            // may be enough lamports to cover rent-exemption
                            // and properly create the commission account.
                            maybe_commission_account.unwrap_or_default()
                        } else {
                            // Before SIMD-0232, commission accounts were always
                            // vote accounts, which cannot be closed unless the
                            // account hasn't voted for at least a full epoch.
                            // This means that `maybe_commission_account` should
                            // always exist.
                            let Some(commission_account) = maybe_commission_account else {
                                debug!(
                                    "commission account {commission_pubkey} missing at \
                                     distribution time"
                                );
                                return None;
                            };
                            commission_account
                        };
                        if *burned_lamports != 0 {
                            total_non_incinerator_burned_lamports
                                .fetch_add(*burned_lamports, Relaxed);
                        }
                        let pre_lamports = commission_account.lamports();
                        if let Err(err) =
                            commission_account.checked_add_lamports(*commission_lamports)
                        {
                            debug!("reward redemption failed for {commission_pubkey}: {err:?}");
                            total_non_incinerator_burned_lamports
                                .fetch_add(*commission_lamports, Relaxed);
                            return None;
                        }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L3643-3693)
```rust
    #[test]
    fn test_load_and_reward_commission_accounts_reflects_vat_burn() {
        let (genesis_config, _mint_keypair) = create_genesis_config(1_000 * LAMPORTS_PER_SOL);
        let bank = Bank::new_for_tests(&genesis_config);
        let thread_pool = ThreadPoolBuilder::new().num_threads(1).build().unwrap();
        let pubkey = solana_pubkey::new_rand();

        let pre_burn_balance = 10 * crate::bank::DEFAULT_VAT_TO_BURN_PER_EPOCH;
        let commission_lamports = 12_345;

        // Commission is planned against the pre-burn account state.
        let mut commission_account = AccountSharedData::default();
        commission_account.set_lamports(pre_burn_balance);
        bank.store_account_and_update_capitalization(&pubkey, &commission_account);
        let mut reward_commissions = RewardCommissions::default();
        reward_commissions.insert(
            pubkey,
            RewardCommission {
                commission_bps: Some(500),
                commission_lamports,
                burned_lamports: 0,
                is_vote_account: true,
            },
        );

        // Simulate the VAT burn that would run in `update_epoch_stakes`
        // between reward calculation and distribution.
        let post_burn_balance = pre_burn_balance - crate::bank::DEFAULT_VAT_TO_BURN_PER_EPOCH;
        let mut burned_account = commission_account.clone();
        burned_account.set_lamports(post_burn_balance);
        bank.store_account_and_update_capitalization(&pubkey, &burned_account);

        let result = bank.load_and_reward_commission_accounts(&reward_commissions, &thread_pool);

        assert_eq!(result.accounts_with_rewards.len(), 1);
        let (pubkey_result, reward_info, account) = &result.accounts_with_rewards[0];
        assert_eq!(*pubkey_result, pubkey);
        // Commission is credited on top of the post-burn balance, not the
        // pre-burn snapshot captured at calculation time.
        let expected_post_balance = post_burn_balance + commission_lamports;
        assert_eq!(account.lamports(), expected_post_balance);
        assert_eq!(
            *reward_info,
            RewardInfo {
                reward_type: RewardType::Voting,
                lamports: commission_lamports as i64,
                post_balance: expected_post_balance,
                commission_bps: Some(500),
            }
        );
        assert_eq!(result.amounts.distributed_lamports, commission_lamports);
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L239-298)
```rust
    fn build_updated_stake_reward(
        distribution_epoch: u64,
        stake_history: &StakeHistory,
        new_warmup_cooldown_rate_epoch: Option<Epoch>,
        stakes_cache_accounts: &imbl::HashMap<Pubkey, StakeAccount<Delegation>>,
        partitioned_stake_reward: &PartitionedStakeReward,
        rent: &Rent,
        adjust_delegations_for_rent: bool,
        use_fixed_point_stake_math: bool,
    ) -> Result<StakeReward, DistributionError> {
        let stake_account = stakes_cache_accounts
            .get(&partitioned_stake_reward.stake_pubkey)
            .ok_or(DistributionError::AccountNotFound)?
            .clone();

        let (mut account, stake_state): (AccountSharedData, StakeStateV2) = stake_account.into();
        let StakeStateV2::Stake(meta, stake, flags) = stake_state else {
            // StakesCache only stores accounts where StakeStateV2::delegation().is_some()
            unreachable!(
                "StakesCache entry {:?} failed StakeStateV2 deserialization",
                partitioned_stake_reward.stake_pubkey
            )
        };
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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L1246-1292)
```rust
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
```
