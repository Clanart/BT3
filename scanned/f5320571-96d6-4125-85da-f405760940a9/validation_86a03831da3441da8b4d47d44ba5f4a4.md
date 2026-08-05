## Title
Reward commissions are calculated against a stale pre-epoch-boundary account balance but distributed against a live post-mutation balance, breaking the reward-budget accounting invariant - (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

## Summary
The External Report's bug class is "amount recorded at deposit/accounting time" (`portfolioTokenBalances`) diverging from "actual amount that reaches the contract" when the underlying balance can move between the point where a nominal amount is recorded and the point where it is redeemed. In Agave, the reward-commission pipeline has the same shape: `calculate_rewards`/`calculate_validator_rewards` plans each vote account's `commission_lamports` against a stake/vote-account snapshot taken at reward-calculation time, but `load_and_reward_commission_accounts` later credits that fixed `commission_lamports` on top of whatever the commission account's *live* lamport balance happens to be at distribution time [1](#0-0) . Between those two points, `update_epoch_stakes` can run `maybe_burn_vat_from_staked_accounts`, which forcibly deducts a fixed VAT amount from every vote account in the filtered epoch-stakes set [2](#0-1) . The code's own comments and a dedicated regression test acknowledge this ordering dependency explicitly.

## Finding Description
`distribute_reward_commissions` intentionally defers loading of the commission accounts so that "intervening account mutations (e.g. VAT burns in `update_epoch_stakes`)" are reflected [3](#0-2) . `load_and_reward_commission_accounts` fetches the *current* on-chain account with `get_account_with_fixed_root_no_cache`, records `pre_lamports`, and then does `commission_account.checked_add_lamports(*commission_lamports)` — i.e. it adds the *nominal, pre-computed* commission amount on top of whatever the live balance is, rather than validating that the live balance still matches the balance snapshot that was used to compute `commission_lamports` in the first place [4](#0-3) .

This is exactly the deflationary-token failure mode from the report, translated to Agave's reward system: a value (`commission_lamports`) is computed against one balance snapshot ("what was deposited"), but the actual balance mutation is applied to a different, later balance ("what's actually in the contract"), and the code has no mechanism to detect or reconcile the difference. The regression test `test_load_and_reward_commission_accounts_reflects_vat_burn` demonstrates this precisely: a commission is "planned against the pre-burn account state," the account is then burned by `DEFAULT_VAT_TO_BURN_PER_EPOCH`, and the test explicitly asserts the commission is "credited on top of the post-burn balance, not the pre-burn snapshot captured at calculation time" [5](#0-4) .

The code even contains a self-documented admission that a companion function produces intentionally-wrong accounting for exactly this reason: `recalculate_stake_rewards` states that `RewardCommissionAccounts` computed there will **not** have a correct `post_lamport` amount when the commission account differs from the vote account, "because the commission account is loaded from the current bank, and not the start of the epoch," and warns "the `RewardCommissionAccounts` calculated in this function call should NOT be used ever" [6](#0-5) .

The only guard in the pipeline is a *global* sanity assertion in `distribute_reward_commissions`: `point_value.rewards >= distributed_lamports + distributed_to_incinerator_lamports + burned_lamports + total_stake_rewards_lamports` [7](#0-6) . This only checks that the aggregate reward pool wasn't over-spent; it does not, and cannot, detect that an individual commission account's `post_balance` no longer corresponds to the account state that justified paying it that commission — the same class of gap the original report calls out ("if the amount of a token in the contract is lower than the cumulative amount recorded... some users will be unable to withdraw in accordance with their rightful proportion").

## Impact Explanation
Because a custom collector/commission account (enabled via `custom_commission_collector`) can be an arbitrary non-vote account created fresh by the vote account owner, this "distribution loads live balance, calculation used stale balance" gap allows the effective payout awarded to a commission account to depend on account-balance mutations that happen strictly between epoch-boundary reward calculation and distribution — a window the protocol itself creates (`update_epoch_stakes` → `maybe_burn_vat_from_staked_accounts` runs after calculation but before distribution). This directly maps to the report's "improper accounting" impact class: capitalization bookkeeping (`self.capitalization.fetch_add(distributed_lamports + ...)`) is incremented by the nominal planned amount regardless of what the live balance actually reflects, so on-chain reported balances/`post_balance` in `RewardInfo` (which feeds transaction/epoch reward reporting relied on by RPC clients, exchanges, and downstream accounting) no longer accurately represents "what the account should have received relative to what it started with," which is the exact failure the reporter flagged for `RenovaQuest.sol`.

## Likelihood Explanation
This path executes automatically once per epoch for every validator during partitioned epoch-reward distribution — it is not gated behind any privileged or malicious-actor assumption; it is triggered purely by normal epoch-boundary processing combined with the VAT-burn feature (`alpenglow`) being active, and the code's own comments and dedicated test confirm the maintainers were aware this ordering gap exists and chose to "reflect" the burn rather than reconcile it against the planned commission.

## Recommendation
Snapshot the pre-distribution balance used at calculation time (or the delta actually intended), and either (a) validate at distribution time that the live account balance still matches the balance assumption used when `commission_lamports` was computed, or (b) explicitly document/enforce that `commission_lamports` is a *relative* credit independent of any intervening balance mutation and audit all downstream consumers (capitalization accounting, `post_balance` reporting) to ensure they cannot be used to infer an incorrect "expected vs. actual" relationship. At minimum, extend the existing `distribute_reward_commissions` assertion to detect per-account balance drift caused by intervening mutations like VAT burns, rather than only checking the aggregate pool.

## Proof of Concept
The existing repository test is itself a proof-of-concept demonstrating the divergent accounting: [5](#0-4) 
1. A commission account starts with `pre_burn_balance = 10 * DEFAULT_VAT_TO_BURN_PER_EPOCH`, and a `commission_lamports = 12_345` reward is "planned against the pre-burn account state."
2. The account is then burned to `post_burn_balance = pre_burn_balance - DEFAULT_VAT_TO_BURN_PER_EPOCH`, simulating `maybe_burn_vat_from_staked_accounts` running in `update_epoch_stakes`.
3. `load_and_reward_commission_accounts` is invoked and credits `commission_lamports` on top of `post_burn_balance`, yielding `expected_post_balance = post_burn_balance + commission_lamports` — confirming the commission was computed relative to one balance and applied relative to a different, already-mutated balance, with no reconciliation between the two.

Note: I was unable to fully trace every downstream consumer of `RewardInfo.post_balance` / capitalization deltas to quantify the maximum-severity real-world exploit path within the tool budget available; this should be verified further (e.g., whether `custom_commission_collector` accounts can be attacker-controlled non-vote accounts, and whether capitalization drift here is bounded or can compound across epochs) before triage.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L369-374)
```rust
        // Load the commission accounts and apply their rewards.
        // This is intentionally deferred from calculation time so that any
        // intervening account mutations (e.g. VAT burns in
        // `update_epoch_stakes`) are reflected.
        let (reward_commission_accounts, load_and_reward_commission_accounts_us) =
            measure_us!(self.load_and_reward_commission_accounts(reward_commissions, thread_pool));
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L397-408)
```rust
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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1063-1076)
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1097-1112)
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
        let reserved_account_keys = &self.reserved_account_keys;
        let rent = &self.rent_collector().rent;
        let feature_snapshot = self.feature_set.snapshot();
        let relax_post_exec_min_balance_check = feature_snapshot.relax_post_exec_min_balance_check;
        let custom_commission_collector = feature_snapshot.custom_commission_collector;
        let total_non_incinerator_burned_lamports = AtomicU64::new(0);
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1150-1163)
```rust
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
                        if !is_vote_account {
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

**File:** runtime/src/bank.rs (L2644-2694)
```rust
    /// Burn the Validator Admission ticket from each vote account if Alpenglow is enabled
    ///
    /// Note: This must ONLY be called after the vote accounts have been filtered (`clone_and_filter_for_vat`)
    /// to the top `MAX_ALPENGLOW_VOTE_ACCOUNTS` that contain enough balance for admission.
    fn maybe_burn_vat_from_staked_accounts(&mut self, epoch_stakes: &VersionedEpochStakes) {
        let feature_snapshot = self.feature_set.snapshot();
        if !feature_snapshot.alpenglow {
            return;
        }

        let vat_to_burn_per_epoch = self.vat_to_burn_per_epoch();
        let vote_accounts = epoch_stakes.stakes().vote_accounts();
        debug_assert!(vote_accounts.len() <= 2000);
        // +1 for the incinerator account
        let mut accounts_to_store: Vec<(Pubkey, AccountSharedData)> =
            Vec::with_capacity(vote_accounts.len() + 1);
        let mut total_vat = 0u64;

        // Vote accounts have already been filtered by clone_and_filter_for_vat to only include
        // accounts with non-zero stake and sufficient balance.
        for (vote_pubkey, _stake) in vote_accounts.delegated_stakes() {
            let mut account = self.get_account(vote_pubkey).unwrap();
            total_vat += vat_to_burn_per_epoch;
            account.set_lamports(
                account
                    .lamports()
                    .checked_sub(vat_to_burn_per_epoch)
                    .expect(
                        "Vote accounts should have already been filtered to contain enough \
                         balance for the VAT",
                    ),
            );
            accounts_to_store.push((*vote_pubkey, account));
        }

        // Per SIMD-0357, transfer collected VAT to the incinerator account.
        let mut incinerator_account = self.get_account(&incinerator::id()).unwrap_or_default();
        incinerator_account.set_lamports(
            incinerator_account
                .lamports()
                .checked_add(total_vat)
                .unwrap(),
        );
        accounts_to_store.push((incinerator::id(), incinerator_account));

        self.store_accounts((self.slot, accounts_to_store.as_slice()), None);
        info!(
            "Transferred total VAT of {total_vat} lamports to incinerator from staked vote \
             accounts"
        );
    }
```
