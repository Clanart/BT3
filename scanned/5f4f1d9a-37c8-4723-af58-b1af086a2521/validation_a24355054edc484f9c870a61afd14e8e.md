## Title
Stale `StakesCache` entries when a cached stake/vote account's owner changes without going to zero lamports - (File: `runtime/src/stakes.rs`)

### Summary
The reported ConvexYieldWrapper bug is a lazy-accounting bug class: a wrapper caches an account's "balance" (`_getDepositedBalance`) keyed by owner, and the cache is never invalidated/checkpointed when the underlying vault ownership actually changes, so two different keys can simultaneously claim credit for the same underlying collateral. Agave's `StakesCache::check_and_store` in [1](#0-0)  has the same structural weakness: it is a lazily-maintained, per-pubkey cache of vote/stake account state that drives reward and stake-weight accounting, and its own author-acknowledged `TODO` states it does not evict an entry when the underlying account's owner changes away from the vote/stake program while lamports remain non-zero.

### Finding Description
`StakesCache` maintains `Stakes<StakeAccount>`, containing `vote_accounts` and `stake_delegations` maps keyed by pubkey [2](#0-1) . After every successfully executed transaction, `Bank::update_stakes_cache` walks the touched accounts and calls `StakesCache::check_and_store` for each `(pubkey, account)` pair [3](#0-2) .

`check_and_store` only has two live branches: "lamports == 0" (removes cache entries for vote/stake owners) and "owner is vote_program"/"owner is stake_program" (upserts the cache). If the account's lamports are non-zero but the owner is neither the vote program nor the stake program, **no branch executes**, and any stale cached `VoteAccount`/`StakeAccount` entry for that pubkey is left untouched: [1](#0-0) 

The comment directly above the owner check is an explicit acknowledgment of this gap: [4](#0-3) 

This is the exact analog of the ConvexYieldWrapper bug: `_getDepositedBalance()`/`user_checkpoint()` never re-validate that the vault it's summing collateral for is still actually owned by the queried account, so a lazily-cached balance survives an ownership change of the underlying resource. Here, the "resource" is the delegation/vote state cached by pubkey, and the "ownership" is the account's `owner` field; `check_and_store` is only invoked opportunistically from the write path and never re-derives cache membership from the ground-truth account state when `owner` transitions to something outside `{vote_program, stake_program}` (with lamports still nonzero).

### Impact Explanation
If a pubkey that is cached in `StakesCache` as a delegated stake account or vote account is subsequently reassigned to a different program owner while nonzero, the runtime's `Stakes<StakeAccount>` cache continues to count that pubkey's delegation/vote weight in:
- `calculate_activated_stake` / `Bank::compute_new_epoch_caches_and_rewards`, which drives `EpochStakes`, leader-schedule stake weighting, and reward distribution [5](#0-4) .
- `recalculate_stake_rewards`, which reads `self.stakes_cache.stakes()` directly to recompute stake rewards [6](#0-5) .

A stale entry that is not removed means capital that has actually left stake/vote-program custody can still be treated as delegated stake for reward and leader-schedule-weight purposes, i.e. false/incorrect stake accounting derived from data the cache no longer reflects the truth of — a divergence between the accounting cache and ground-truth account state, analogous to the double-claim invariant break in the ConvexYieldWrapper report.

### Likelihood Explanation
This is gated behind whether an owner transition away from `stake_program`/`vote_program` while lamports > 0 is actually reachable for accounts whose data was previously a valid `StakeStateV2`/`VoteStateVersions` payload. Agave's account-owner-change model in the SVM generally restricts owner reassignment to accounts whose data is entirely zeroed (this constraint exists specifically to prevent aliasing a foreign program's data layout after ownership hand-off), which is the standard defense against this exact class of attack. I was not able to conclusively verify from the indexed code within the available iterations whether every code path that can flip an account's owner (e.g., `Assign`, CPI-driven owner handoff in `program-runtime/src/cpi.rs`, or program-specific instructions) enforces the "data must be zero" precondition in all cases, nor whether any stake/vote-program instruction itself permits relinquishing ownership without first zeroing/draining the account. Because of this, I can identify the exact corrupted cache/invariant and the exact code location where the guard is missing (`check_and_store`'s owner-branch fallthrough), but cannot confirm from local evidence alone that an unprivileged actor can currently trigger the owner transition while lamports remain non-zero and stake data intact. This should be verified against the full account-owner-change enforcement logic before treating it as a confirmed, exploitable path.

### Recommendation
Make `StakesCache::check_and_store` authoritative rather than owner-conditional: on every account update, check whether the pubkey has an existing cache entry (`vote_accounts` or `stake_delegations`) and, if the current owner no longer matches the program that produced that entry, unconditionally evict it (mirroring the `lamports == 0` removal branches), regardless of what the new owner is. This closes the gap the existing `TODO` documents and ensures the cache is checkpointed on every ownership-affecting mutation instead of only on writes that happen to still look like vote/stake program data.

### Proof of Concept
Conceptual PoC (not fully verified as exploitable given the caveats above):
1. Create and delegate a stake account `S` to vote account `V`; `StakesCache::upsert_stake_delegation` caches `S -> Delegation(V, stake=N)` [7](#0-6) .
2. Perform whatever sequence of instructions is required to change `S.owner` away from `stake_program` while `S.lamports() > 0` and its data still deserializes as a valid `StakeStateV2` (this is the step requiring further verification against the SVM's owner-change enforcement).
3. Because `check_and_store` only removes/updates cache entries on `lamports == 0` or `owner ∈ {vote_program, stake_program}`, the stale `S -> Delegation(V, N)` entry remains in `Stakes<StakeAccount>` even though the ground-truth account is no longer a stake account.
4. Subsequent epoch-boundary calculations (`compute_new_epoch_caches_and_rewards`, `recalculate_stake_rewards`) continue to count `N` lamports of stake toward `V`'s delegated stake and reward calculations, diverging from the true on-chain state.

### Citations

**File:** runtime/src/stakes.rs (L87-164)
```rust
    pub(crate) fn check_and_store(
        &self,
        pubkey: &Pubkey,
        account: &impl ReadableAccount,
        new_rate_activation_epoch: Option<Epoch>,
        use_fixed_point_stake_math: bool,
    ) {
        // TODO: If the account is already cached as a vote or stake account
        // but the owner changes, then this needs to evict the account from
        // the cache. see:
        // https://github.com/solana-labs/solana/pull/24200#discussion_r849935444
        let owner = account.owner();
        // Zero lamport accounts are not stored in accounts-db
        // and so should be removed from cache as well.
        if account.lamports() == 0 {
            if solana_vote_program::check_id(owner) {
                let _old_vote_account = {
                    let mut stakes = self.0.write().unwrap();
                    stakes.remove_vote_account(pubkey)
                };
            } else if stake_program::check_id(owner) {
                let mut stakes = self.0.write().unwrap();
                stakes.remove_stake_delegation(
                    pubkey,
                    new_rate_activation_epoch,
                    use_fixed_point_stake_math,
                );
            }
            return;
        }
        debug_assert_ne!(account.lamports(), 0u64);
        if solana_vote_program::check_id(owner) {
            if VoteStateVersions::is_correct_size_and_initialized(account.data()) {
                match VoteAccount::try_from(create_account_shared_data(account)) {
                    Ok(vote_account) => {
                        // drop the old account after releasing the lock
                        let _old_vote_account = {
                            let mut stakes = self.0.write().unwrap();
                            stakes.upsert_vote_account(pubkey, vote_account)
                        };
                    }
                    Err(_) => {
                        // drop the old account after releasing the lock
                        let _old_vote_account = {
                            let mut stakes = self.0.write().unwrap();
                            stakes.remove_vote_account(pubkey)
                        };
                    }
                }
            } else {
                // drop the old account after releasing the lock
                let _old_vote_account = {
                    let mut stakes = self.0.write().unwrap();
                    stakes.remove_vote_account(pubkey)
                };
            };
        } else if stake_program::check_id(owner) {
            match StakeAccount::try_from(create_account_shared_data(account)) {
                Ok(stake_account) => {
                    let mut stakes = self.0.write().unwrap();
                    stakes.upsert_stake_delegation(
                        *pubkey,
                        stake_account,
                        new_rate_activation_epoch,
                        use_fixed_point_stake_math,
                    );
                }
                Err(_) => {
                    let mut stakes = self.0.write().unwrap();
                    stakes.remove_stake_delegation(
                        pubkey,
                        new_rate_activation_epoch,
                        use_fixed_point_stake_math,
                    );
                }
            }
        }
    }
```

**File:** runtime/src/stakes.rs (L207-233)
```rust
pub struct Stakes<T: Clone> {
    /// vote accounts
    vote_accounts: VoteAccounts,

    /// stake_delegations
    #[cfg_attr(
        feature = "frozen-abi",
        stable_abi_sample(with = "sample_collection_sized(rng, SequenceLenMax(1))")
    )]
    #[wincode(with = "FromIntoIterator<ImblHashMap<Pubkey, T>, BincodeLen>")]
    stake_delegations: ImblHashMap<Pubkey, T>,

    /// current effective stake delegated to each vote account pubkey
    #[cfg_attr(feature = "frozen-abi", stable_abi_sample(with = "Default::default()"))]
    #[serde(skip)]
    #[wincode(skip)]
    delegated_stakes: DelegatedStakes,

    /// unused
    unused: u64,

    /// current epoch, used to calculate current stake
    epoch: Epoch,

    /// history of staking levels
    stake_history: StakeHistory,
}
```

**File:** runtime/src/stakes.rs (L620-660)
```rust
    fn upsert_stake_delegation(
        &mut self,
        stake_pubkey: Pubkey,
        stake_account: StakeAccount,
        new_rate_activation_epoch: Option<Epoch>,
        use_fixed_point_stake_math: bool,
    ) {
        debug_assert_ne!(stake_account.lamports(), 0u64);
        let delegation = stake_account.delegation();
        let voter_pubkey = delegation.voter_pubkey;
        let stake = delegation_effective_stake(
            delegation,
            self.epoch,
            &self.stake_history,
            new_rate_activation_epoch,
            use_fixed_point_stake_math,
        );
        match self.stake_delegations.insert(stake_pubkey, stake_account) {
            None => {
                self.add_delegated_stake(voter_pubkey, stake);
                self.vote_accounts.add_stake(&voter_pubkey, stake);
            }
            Some(old_stake_account) => {
                let old_delegation = old_stake_account.delegation();
                let old_voter_pubkey = old_delegation.voter_pubkey;
                let old_stake = delegation_effective_stake(
                    old_delegation,
                    self.epoch,
                    &self.stake_history,
                    new_rate_activation_epoch,
                    use_fixed_point_stake_math,
                );
                if voter_pubkey != old_voter_pubkey || stake != old_stake {
                    self.sub_delegated_stake(&old_voter_pubkey, old_stake);
                    self.add_delegated_stake(voter_pubkey, stake);
                    self.vote_accounts.sub_stake(&old_voter_pubkey, old_stake);
                    self.vote_accounts.add_stake(&voter_pubkey, stake);
                }
            }
        }
    }
```

**File:** runtime/src/bank.rs (L1750-1813)
```rust
    /// Returns updated stake history and vote accounts that includes new
    /// activated stake from the last epoch.
    fn compute_new_epoch_caches_and_rewards(
        &self,
        thread_pool: &ThreadPool,
        rewarded_epoch: Epoch,
        reward_calc_tracer: Option<impl RewardCalcTracer>,
        rewards_metrics: &mut RewardsMetrics,
    ) -> NewEpochBundle {
        // Add new entry to stakes.stake_history, set appropriate epoch and
        // update vote accounts with warmed up stakes before saving a
        // snapshot of stakes in epoch stakes
        let stakes = self.stakes_cache.stakes();
        let stake_delegations = stakes.stake_delegations_vec();
        let (
            (
                stake_history,
                unfiltered_distribution_vote_accounts,
                delegated_stakes,
                reward_epoch_delegated_stakes,
            ),
            calculate_activated_stake_time_us,
        ) = measure_us!(stakes.calculate_activated_stake(
            self.epoch(),
            thread_pool,
            self.new_warmup_cooldown_rate_epoch(),
            &stake_delegations,
            self.use_fixed_point_stake_math(),
        ));
        debug_assert_eq!(reward_epoch_delegated_stakes.epoch, rewarded_epoch);

        // Apply stake rewards and commission using the VAT-filtered distribution
        // vote-account snapshot.
        let filtered_distribution_vote_accounts = unfiltered_distribution_vote_accounts
            .clone_and_filter_for_vat(
                MAX_ALPENGLOW_VOTE_ACCOUNTS,
                self.minimum_vote_account_balance_for_vat(),
            );
        if AlpenglowEpochType::is_alpenglow_or_migration_epoch(self, rewarded_epoch) {
            reward_epoch_delegated_stakes.set(self, &filtered_distribution_vote_accounts);
        }
        let cached_vote_accounts =
            self.get_cached_vote_accounts(rewarded_epoch, &filtered_distribution_vote_accounts);
        let (rewards_calculation, update_rewards_with_thread_pool_time_us) =
            measure_us!(self.calculate_rewards(
                &stake_history,
                stake_delegations,
                cached_vote_accounts,
                rewarded_epoch,
                reward_epoch_delegated_stakes,
                reward_calc_tracer,
                thread_pool,
                rewards_metrics,
            ));
        NewEpochBundle {
            stake_history,
            unfiltered_distribution_vote_accounts,
            delegated_stakes,
            filtered_distribution_vote_accounts,
            rewards_calculation,
            calculate_activated_stake_time_us,
            update_rewards_with_thread_pool_time_us,
        }
    }
```

**File:** runtime/src/bank.rs (L5756-5791)
```rust
    fn update_stakes_cache(
        &self,
        txs: &[impl SVMMessage],
        processing_results: &[TransactionProcessingResult],
    ) {
        debug_assert_eq!(txs.len(), processing_results.len());
        let new_warmup_cooldown_rate_epoch = self.new_warmup_cooldown_rate_epoch();
        let use_fixed_point_stake_math = self.use_fixed_point_stake_math();
        txs.iter()
            .zip(processing_results)
            .filter_map(|(tx, processing_result)| {
                processing_result
                    .processed_transaction()
                    .map(|processed_tx| (tx, processed_tx))
            })
            .filter_map(|(tx, processed_tx)| {
                processed_tx
                    .executed_transaction()
                    .map(|executed_tx| (tx, executed_tx))
            })
            .filter(|(_, executed_tx)| executed_tx.was_successful())
            .flat_map(|(tx, executed_tx)| {
                let num_account_keys = tx.account_keys().len();
                let loaded_tx = &executed_tx.loaded_transaction;
                loaded_tx.accounts.iter().take(num_account_keys)
            })
            .for_each(|(pubkey, account)| {
                // note that this could get timed to: self.rc.accounts.accounts_db.stats.stakes_cache_check_and_store_us,
                //  but this code path is captured separately in ExecuteTimingType::UpdateStakesCacheUs
                self.stakes_cache.check_and_store(
                    pubkey,
                    account,
                    new_warmup_cooldown_rate_epoch,
                    use_fixed_point_stake_math,
                );
            });
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
