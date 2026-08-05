### Title
Reward commission calculated against pre-burn (stale) account balance but applied on top of post-burn balance during epoch reward distribution - ([File: runtime/src/bank/partitioned_epoch_rewards/calculation.rs])

### Summary
The Move report's core flaw is that a validation/estimation step uses a value captured before a state-mutating update (scaled balance) and then compares/combines it with a value computed after the update (unscaled balance), producing an inconsistent result. The Agave analog is `load_and_reward_commission_accounts`, which loads a vote/commission account, computes the credited reward, and stores the account with `commission + current_balance` — but the "current balance" it reads can already reflect an unrelated concurrent mutation (the per-epoch VAT burn), while the commission amount itself was computed earlier against the balance from *before* that burn.

### Finding Description
`calculate_stake_rewards_and_commissions` computes `RewardCommission { commission_lamports, ... }` from state snapshotted at reward-calculation time [1](#0-0) . Later, `load_and_reward_commission_accounts` loads the *current* on-chain account (which may have already been mutated by the epoch's VAT burn) and adds the previously-computed commission on top of it, explicitly acknowledging in a code comment that the commission account's snapshot state can diverge from what is loaded at distribution time: "the RewardCommissionAccounts will NOT have a correct post_lamport amount if the commission account is NOT the vote account, because the commission account is loaded from the current bank, and not the start of the epoch" [2](#0-1) .

The accompanying test `test_load_and_reward_commission_accounts_reflects_vat_burn` demonstrates the exact broken invariant: a commission of `12_345` lamports is planned against the account's pre-burn balance, the simulated VAT burn (`DEFAULT_VAT_TO_BURN_PER_EPOCH`) then reduces the on-chain balance, and `load_and_reward_commission_accounts` is shown to add the commission on top of the *post-burn* balance rather than reconciling against the balance the commission was computed from [3](#0-2) . This exactly mirrors the reported bug class: a quantity computed under one state ("scaled"/pre-mutation) is combined with a quantity read under a different, already-updated state ("unscaled"/post-mutation), and the code has no reconciliation step to make the two consistent.

Additionally, `recalculate_stake_rewards`/`recalculate_partitioned_rewards_if_active` recompute stake rewards from `EpochRewards` sysvar totals and `StakesCache`/`EpochStakes` snapshots taken at an arbitrary later point, while assuming reward commissions were "already... calculated and delivered" from an earlier pass [4](#0-3) . This split calculation-vs-delivery model, with no invariant enforcing that the two operate on the same account state, is structurally identical to the Move validators mixing scaled and unscaled balances across a state-update boundary.

### Impact Explanation
If the VAT burn (or any other epoch-boundary account mutation touching the commission-receiving account) happens between commission calculation and commission delivery, the resulting `post_balance`/`lamports` recorded in `RewardInfo` and the actual on-chain balance change do not correspond to the amount that was supposed to be credited relative to a consistent baseline. This can silently misstate rewards issued during epoch-reward distribution — a form of "false execution/acceptance" of the reward transaction with respect to protocol-intended token issuance, which under repeated/large-scale epochs affects consensus-critical capitalization accounting (`store_account_and_update_capitalization`) [5](#0-4) .

### Likelihood Explanation
This is not attacker-triggered in the traditional sense (no malicious peer/validator input required); it is a deterministic ordering bug tied to the validator's own epoch-boundary processing (VAT burn vs. reward calculation/delivery), so it would manifest for *every* validator running the same code path once VAT burn and epoch rewards for a given account fall in the same epoch boundary — a systemic, not opportunistic, condition. However, I could not fully trace `DEFAULT_VAT_TO_BURN_PER_EPOCH`'s exact call site and ordering relative to `recalculate_partitioned_rewards_if_active`/`load_and_reward_commission_accounts` within `bank.rs` in the time available (tool budget exhausted), so I cannot confirm whether the burn and reward-delivery paths are actually reachable within the same epoch transition in production control flow, or whether a guard elsewhere prevents this from being observable outside of tests.

### Recommendation
Snapshot the commission-target account's balance once, at commission-calculation time, and reuse that same snapshot (or an explicit delta) when applying the credit at delivery time, rather than reloading "current" bank state and adding on top of it — mirroring the report's own remediation of moving the validation/computation to occur atomically around the state update so that all values used together come from a single consistent point in time.

### Proof of Concept
The existing repo test itself constitutes a proof of concept of the inconsistency: [6](#0-5)  plans a commission of `commission_lamports` against `pre_burn_balance`, applies a burn to reach `post_burn_balance`, then shows `load_and_reward_commission_accounts` returns `account.lamports() == post_burn_balance + commission_lamports` — i.e., the commission is added to a balance baseline that differs from the one it was computed against, with the divergence being exactly `DEFAULT_VAT_TO_BURN_PER_EPOCH`.

**Caveat:** I was not able to fully confirm (within the tool-call budget) the exact production call ordering between the VAT burn and reward commission delivery in `bank.rs`, nor view the full `load_and_reward_commission_accounts` implementation. This may be a known/handled discrepancy documented intentionally (the code comment suggests awareness), so this should be treated as a documented-and-possibly-accepted design tradeoff rather than a confirmed zero-day unless further review of `runtime/src/bank.rs` and `runtime/src/block_component_processor/vote_reward.rs` shows otherwise.

### Citations

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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1069-1075)
```rust
        // NOTE: the `RewardCommissionAccounts` will NOT have a correct
        // post_lamport amount if the commission account is NOT the vote account,
        // because the commission account is loaded from the current bank, and
        // not the start of the epoch. We don't have a snapshot of all commission
        // accounts from the start of the epoch. For this reason, the
        // `RewardCommissionAccounts` calculated in this function call should
        // NOT be used ever.
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1076-1087)
```rust
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L3643-3694)
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
    }
```
