## Title
Stale stakes-cache entries after account owner reassignment away from the Stake/Vote program - (`runtime/src/stakes.rs`)

## Summary
`StakesCache::check_and_store` — the function that keeps the bank's in-memory `Stakes<StakeAccount>` (vote-account stakes / delegated-stake totals used for leader schedule, rewards, and stake-weighted consensus calculations) synchronized with accounts-db after every transaction — has a documented, unresolved gap: if a previously-cached stake or vote account's *owner* changes to a program other than the Stake or Vote program (while the account keeps non-zero lamports), the function does nothing at all. It neither evicts the stale entry nor updates it, leaving the boost-analogous aggregate (`delegated_stakes` / `vote_accounts`) permanently out of sync with the real on-chain state, exactly mirroring the "missing `_updateBoostState()`" pattern in the report: a state-mutating operation that should propagate into an aggregate is silently skipped along one code path.

## Finding Description
`StakesCache::check_and_store` is called by `Bank::update_stakes_cache` for every account touched by every successfully-executed transaction: [1](#0-0) 

Inside `check_and_store`, the dispatch is based solely on the account's *current* owner: [2](#0-1) [3](#0-2) 

The code contains its own acknowledgment of the gap:

```
// TODO: If the account is already cached as a vote or stake account
// but the owner changes, then this needs to evict the account from
// the cache. see:
// https://github.com/solana-labs/solana/pull/24200#discussion_r849935444
``` [4](#0-3) 

Walking the branches: if `account.lamports() == 0`, the code removes cache entries for the vote or stake program branches. If `lamports() != 0` and `owner` matches the vote program or the stake program, the cache is updated (upsert or, on deserialization failure, removed). But if `owner` is neither the vote program nor the stake program (i.e., the account was previously cached as a stake/vote delegation and its owner has since changed to some *other* program, while keeping non-zero lamports), **none of the branches execute** — the function falls straight through with no cache mutation at all. The previous `upsert_stake_delegation`/`upsert_vote_account` entry (and its contribution to `delegated_stakes` and `VoteAccounts`' cached stake totals) remains in the `Stakes<StakeAccount>` structure exactly as it was before the ownership change.

This is the same broken invariant as the veRAACToken report: a mutation of underlying state (owner reassignment invalidating the account as a stake/vote delegation) that should immediately propagate into a derived aggregate (`_boostState` there; `Stakes.delegated_stakes` / `VoteAccounts` here) is instead skipped on this specific path, leaving the aggregate stale relative to the real account state.

## Impact Explanation
`Stakes<StakeAccount>` (via `StakesCache`) directly feeds:
- `calculate_activated_stake` / `refresh_delegated_stakes`, which produce `delegated_stakes` and the epoch's `VoteAccounts`, consumed at epoch boundaries to compute leader schedules and stake-weighted voting/consensus data [5](#0-4) 
- Stake-reward calculations via `RewardEpochDelegatedStakes` / inflation reward math that use `delegated_stakes` as the denominator for reward distribution [6](#0-5) 

If a stale, "phantom" delegation continues to be counted after its backing account has actually been reassigned away from the Stake program, delegated-stake totals for the affected vote account are inflated relative to the true on-chain state. This corrupts:
- the effective/total-stake denominator used in stake-weighted reward math (`total_stake` in `calculate_alpenglow_points`), skewing reward payouts to other stakers of the same validator,
- leader-schedule/stake-weight calculations derived from `vote_accounts`/`delegated_stakes` at epoch boundaries.

Because these values feed bank-level state used identically by all validators replaying the same transactions, any divergence would be a determinism/consensus concern rather than a one-node crash — but note the "reachability" caveat below significantly limits how far this can actually be exploited in practice.

## Likelihood Explanation
The exploitability hinges entirely on whether an account that is presently owned by the Stake or Vote program, holding non-zero lamports, can have its `owner` field changed to a third, non-stake/non-vote program without going through the zero-lamports/deserialization-failure paths that already trigger cleanup. Solana's runtime account-ownership-change rule generally restricts owner reassignment to the current owning program, and typically only when the account data is zeroed out — and a zeroed stake/vote account would already fail `StakeAccount::try_from` / `VoteStateVersions::is_correct_size_and_initialized`, routing into the existing `remove_*` cleanup branches. I was not able to confirm, within the available indexed code (`program-runtime/src/cpi.rs`, `svm/src/account_loader.rs`, `svm/src/transaction_account_state_info.rs` all showed relevant hits but their exact owner-change enforcement logic was not fully inspected due to iteration limits), whether there exists any legitimate path where owner changes to a non-vote/non-stake program while data remains non-empty/non-zeroed and the account is still classified with non-zero lamports. This is the key open question that determines whether this TODO is a live, reachable bug or a defense-in-depth gap that is currently unreachable given other runtime invariants.

## Recommendation
- In `StakesCache::check_and_store`, add an explicit `else` branch (when `owner` is neither the vote program nor the stake program) that evicts any existing cached entry for `pubkey` from both `stake_delegations`/`delegated_stakes` and `vote_accounts`, mirroring the cleanup already done in the zero-lamports and deserialization-failure paths.
- Resolve the referenced upstream TODO (`solana-labs/solana#24200`, discussion r849935444) by confirming under which conditions owner reassignment away from Stake/Vote can occur with non-zero lamports, and add a regression test analogous to `test_stakes_not_delegate`/`test_stakes_vote_account_disappear_reappear` that exercises an owner change instead of a lamports-to-zero or malformed-data transition.

## Proof of Concept
A concrete PoC could not be constructed with confidence from the indexed code alone, because it requires demonstrating a legitimate on-chain instruction sequence that changes a stake/vote account's owner to an unrelated program while keeping lamports non-zero and data non-zeroed (bypassing the two existing cleanup branches). The structural bug — the missing `else` branch in `check_and_store` — is directly shown in the cited code; a Devin session with runtime access would be needed to attempt constructing an actual owner-reassignment transaction (e.g. via a custom program that CPIs `set_owner`-equivalent behavior, or via `system_instruction::assign`-style semantics) against a stake/vote account to confirm reachability before treating this as a full end-to-end exploit.

### Citations

**File:** runtime/src/bank.rs (L5756-5792)
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
    }
```

**File:** runtime/src/stakes.rs (L87-116)
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
```

**File:** runtime/src/stakes.rs (L117-164)
```rust
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

**File:** runtime/src/stakes.rs (L434-502)
```rust
    pub(crate) fn calculate_activated_stake(
        &self,
        next_epoch: Epoch,
        thread_pool: &ThreadPool,
        new_rate_activation_epoch: Option<Epoch>,
        stake_delegations: &[(&Pubkey, &StakeAccount)],
        use_fixed_point_stake_math: bool,
    ) -> (
        StakeHistory,
        VoteAccounts,
        DelegatedStakes,
        RewardEpochDelegatedStakes,
    ) {
        // Wrap up the prev epoch by adding new stake history entry for the
        // prev epoch.
        let (stake_history_entry, effective_delegated_stakes) = thread_pool.install(|| {
            stake_delegations
                .par_iter()
                .fold(
                    || (StakeActivationStatus::default(), HashMap::default()),
                    |(acc, mut delegated_stakes), (_stake_pubkey, stake_account)| {
                        let delegation = stake_account.delegation();
                        let activation_status = delegation_activation_status(
                            delegation,
                            self.epoch,
                            &self.stake_history,
                            new_rate_activation_epoch,
                            use_fixed_point_stake_math,
                        );
                        *delegated_stakes.entry(delegation.voter_pubkey).or_default() +=
                            activation_status.effective;
                        (acc + activation_status, delegated_stakes)
                    },
                )
                .reduce(
                    || (StakeActivationStatus::default(), HashMap::default()),
                    |(activation_status_a, delegated_stakes_a),
                     (activation_status_b, delegated_stakes_b)| {
                        (
                            activation_status_a + activation_status_b,
                            merge_delegated_stakes(delegated_stakes_a, delegated_stakes_b),
                        )
                    },
                )
        });
        let mut stake_history = self.stake_history.clone();
        stake_history.add(self.epoch, stake_history_entry);
        // Refresh the stake distribution of vote accounts for the next epoch,
        // using new stake history.
        let (vote_accounts, delegated_stakes) = refresh_vote_accounts(
            thread_pool,
            next_epoch,
            &self.vote_accounts,
            stake_delegations,
            &stake_history,
            new_rate_activation_epoch,
            use_fixed_point_stake_math,
        );
        let reward_epoch_delegated_stakes = RewardEpochDelegatedStakes {
            epoch: self.epoch,
            delegated_stakes: effective_delegated_stakes,
        };
        (
            stake_history,
            vote_accounts,
            delegated_stakes,
            reward_epoch_delegated_stakes,
        )
    }
```

**File:** runtime/src/inflation_rewards/points.rs (L280-301)
```rust
    let earned_points = if earned_credits == 0 || stake_amount == 0 {
        0
    } else {
        let Some(total_stake) = reward_epoch_delegated_stakes
            .delegated_stakes
            .get(&stake.delegation.voter_pubkey)
            .copied()
            .filter(|stake| *stake != 0)
        else {
            record_error(format!(
                "AG delegated stake denominator for vote_pubkey={} in epoch={} failed",
                stake.delegation.voter_pubkey, reward_epoch_delegated_stakes.epoch
            ));
            return Err(CalculatedStakePoints {
                tower_points: 0,
                ag_points: 0,
                new_credits_observed,
                force_credits_update_with_skipped_reward: true,
            });
        };
        earned_credits * stake_amount / total_stake as u128
    };
```
