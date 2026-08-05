### Title
Stale-snapshot mismatch between reward-commission calculation and distribution allows post-balance corruption in `load_and_reward_commission_accounts` - (File: runtime/src/bank/partitioned_epoch_rewards/calculation.rs)

### Summary
This is a genuine Agave analog of the reported bug class ("using a value snapshotted at an earlier point in time — for HYPE, the start-of-block precompile state — to compute a total that can be invalidated by intervening state changes before it is consumed"). In Agave's partitioned epoch-rewards path, commission lamport amounts are computed against a `RewardCommission`/`RewardCommissionAccounts` structure produced during the calculation phase (which reads vote-account state cached at an earlier epoch boundary), but the actual account balance mutated at distribution time is loaded from the *current* bank, which may have changed since calculation (e.g., due to the VAT burn process running via `update_epoch_stakes` between calculation and distribution). The code itself documents this inconsistency as a known correctness hazard.

### Finding Description
`get_epoch_params_for_recalculation` explicitly notes that stake/vote data used for reward recalculation is sourced from cached snapshots taken at an earlier point (VAT-filtered vote-account snapshot from `epoch_stakes`, not the live current-block bank state): [1](#0-0) 

The recalculation code that reuses this snapshot explicitly documents that the commission accounts it computes are **not safe to use**, because the commission account is reloaded from the *current* bank rather than the epoch-start snapshot that was used to derive the reward/commission amounts: [2](#0-1) 

This is corroborated by the test `test_load_and_reward_commission_accounts_reflects_vat_burn`, which demonstrates that a commission calculated against a pre-burn account balance is applied on top of the *post-burn* balance captured later — i.e., the commission amount and the base balance it is credited onto come from two different points in time (pre-burn snapshot vs. post-burn live state): [3](#0-2) 

The parallel with the Hyperliquid report is direct: `Overseer.getNewSupply()` combined balances read from L1 precompiles reflecting "start of block" state with actions (interim-address transfers, staking module moves) that could occur later in the same block, producing an inconsistent total. Here, `calculate_rewards_for_partitioning`/`recalculate_stake_rewards` combine a **cached, earlier snapshot** of vote/stake state (used to compute the reward and commission *amount*) with the **live current-bank account state** (used as the base balance the commission is credited onto), while intervening mutations (e.g., the VAT burn `crate::bank::DEFAULT_VAT_TO_BURN_PER_EPOCH`, or any other write to the commission account between calculation and distribution) can change the account in between. The code comment at lines 1069–1075 acknowledges the resulting `RewardCommissionAccounts::post_lamport` field will be wrong whenever the commission destination is not the vote account itself, and states it "should NOT be used ever" — indicating the invariant is known to be broken rather than guarded against structurally.

### Impact Explanation
If a caller of `recalculate_stake_rewards` (e.g., a snapshot-restore/post-restart recalculation path, per `initialize_after_snapshot_restore` and `test_initialize_after_snapshot_restore`) were to rely on the `RewardCommissionAccounts`/post-balance data derived here instead of discarding it, this would let stale-vs-live inconsistency feed into on-chain lamport accounting for commission distribution, corrupting `post_balance` reported in `RewardInfo` and potentially the actual account write if consumed downstream. Because reward distribution directly moves real lamports and updates bank capitalization, an accounting mismatch here is a fund-accuracy issue analogous to the original report's total-supply miscalculation (share price / total-supply distortion). Impact is capped by the fact that the current call sites (`recalculate_stake_rewards`) only reuse `stake_rewards`, explicitly discarding the commission portion of the result, per the safety comment in `calculation.rs` lines 1063–1076.

### Likelihood Explanation
Low. Exploitation is not currently reachable because the sole recalculation caller intentionally discards `RewardCommissionAccounts` (as the in-code comment states), so the corrupted data is not written back to state in this snapshot-restore path today. However, the underlying invariant — that reward-commission computation mixes cached epoch-boundary snapshot data with live current-bank state without atomicity guarantees — is a systemic hazard that could resurface if new code paths (or future refactors) consume `RewardCommissionAccounts` from `recalculate_stake_rewards` without preserving the current safeguard, since nothing at the type level prevents misuse; only a comment does.

### Recommendation
- Do not expose `RewardCommissionAccounts`/`post_lamport` from `recalculate_stake_rewards` at all (e.g., return only `StakeRewardCalculation` from that function) so the unsafe data cannot be accidentally consumed by future callers, rather than relying on a "should NOT be used ever" comment.
- If commission recomputation is ever required after snapshot restore, source both the reward amount and the destination account's base balance from the same consistent point in time (e.g., re-derive both from the epoch-start snapshot, or defer entirely to the already-distributed on-chain record) instead of mixing snapshot-epoch amounts with live-bank balances.
- Add an explicit assertion/type-level marker (e.g., a wrapper type that cannot be stored without an accompanying "verified consistent" flag) to prevent silent reintroduction of the stale/live mismatch this code currently guards against only via documentation.

### Proof of Concept
The existing unit test already demonstrates the mismatch mechanically: [3](#0-2) 
1. A commission account is stored with `pre_burn_balance`.
2. A `RewardCommission` is computed against that pre-burn snapshot (`commission_lamports`).
3. The bank state is mutated in between (`post_burn_balance = pre_burn_balance - DEFAULT_VAT_TO_BURN_PER_EPOCH`), simulating the VAT burn that runs in `update_epoch_stakes` between calculation and distribution.
4. `load_and_reward_commission_accounts` credits the commission on top of the post-burn (live) balance rather than the pre-burn (snapshot) balance used to justify the commission amount, producing a `post_balance`/`RewardInfo` that reflects two different points in time being combined — the same "combining stale snapshot state with intervening live mutations" defect described in the original report.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L580-601)
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
