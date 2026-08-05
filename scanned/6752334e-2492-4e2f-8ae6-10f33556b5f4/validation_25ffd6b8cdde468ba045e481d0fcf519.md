Confirmed: `set_owner` requires `is_zeroed(self.get_data())` before allowing an owner change [1](#0-0) . This means the stake/vote program itself would have to zero out an account's data (which wipes the delegation/vote state) before reassigning it to a different owner — a path that only the owning program can take, and which the stake/vote programs' current instruction handlers do not expose to end users, but which is a legitimate low-level capability. Given this constraint, the practical trigger for the stale-cache scenario is one where an account's deserialized state becomes invalid/zeroed *without* lamports going to zero (e.g., via `CreateAccount`/realloc-style calls, or a partial state transition that a program can legally perform), which the code's `Err(_)` branch already handles for the "still owned by stake/vote program" case. The genuinely open gap flagged by the code's own `TODO` is specifically about **owner changing away from the stake/vote program while lamports remain non-zero**, and it is unclear from local code alone whether any current instruction path (system/stake/vote programs) actually reaches this in practice, since owner reassignment additionally requires the data to be zeroed first (which itself would just as often go through the `Err(_)` deserialization-failure branch already handled). I was not able to conclusively find a caller that changes a stake/vote account's owner while leaving nonzero lamports and non-zeroed, still-parseable data — this would need dynamic/CPI-level verification (e.g., via `bank_client`/SVM integration tests) beyond what static reading can confirm.

### Title
Stale stake/vote delegation cache entries on account owner change are never evicted — `StakesCache::check_and_store` (`runtime/src/stakes.rs`)

### Summary
`StakesCache::check_and_store`, called from `Bank::update_stakes_cache` after every transaction, only evicts a cached stake delegation or vote account when the account's lamports drop to zero, or when the account is still owned by the stake/vote program but fails to deserialize into a valid `StakeAccount`/`VoteAccount` [2](#0-1) . The code contains an explicit acknowledgment of the gap: "TODO: If the account is already cached as a vote or stake account but the owner changes, then this needs to evict the account from the cache" [3](#0-2) . If an account's owner is changed away from `stake_program`/`solana_vote_program` while its lamports remain nonzero, `check_and_store` falls through both the `lamports == 0` branch and the `vote_program`/`stake_program` owner-match branches, leaving the previous delegation/vote entry in `Stakes<StakeAccount>` (`stake_delegations`, `vote_accounts`, `delegated_stakes`) untouched and permanently stale [4](#0-3) .

### Finding Description
`Bank::update_stakes_cache` iterates every account touched by a successfully-processed transaction and calls `check_and_store` with the account's *current* owner [5](#0-4) . Inside `check_and_store`, eviction is only performed in two cases: (1) `account.lamports() == 0` for vote/stake owned accounts, and (2) the account is still owned by `stake_program`/`solana_vote_program` but its `StakeAccount`/`VoteAccount` deserialization fails [6](#0-5) . There is no `else` branch handling "owner is neither vote program nor stake program, and lamports > 0" — this is exactly the case the inline `TODO` calls out.

This is the direct structural analog of the reported Morpho Blue bug: a checkpoint/cache keyed by an entity's identity (here, `Pubkey`) is only refreshed on specific "deposit/withdraw"-like code paths (`upsert_stake_delegation`/`upsert_vote_account`/lamports-zero removal) and is never invalidated on the generic "ownership transferred elsewhere" event, exactly as the wrapper token's balance checkpoint was never invalidated by ERC20 `transfer` because `_update` wasn't overridden.

The account-owner-change guard in `BorrowedInstructionAccount::set_owner` requires the current owning program to have already zeroed the account's data before changing owner [1](#0-0) , which constrains — but does not eliminate — the reachability of this state; it depends on the stake/vote programs' own internal state-machine behavior around data zeroing combined with owner handoff, which I could not fully trace from static reads alone.

### Impact Explanation
`Stakes<StakeAccount>` backs consensus-critical bookkeeping: delegated stake amounts used for vote-weight/stake-weight calculations are derived directly from this cache [7](#0-6) . A stale, un-evicted delegation entry could cause a validator to retain phantom delegated stake for a pubkey that is no longer a valid stake account, corrupting stake-weighted calculations that feed into fork-choice/leader-schedule and vote-account bookkeeping — a false-execution/false-acceptance class issue rather than a simple bookkeeping nuisance.

### Likelihood Explanation
Likelihood is uncertain and hinges entirely on whether any real instruction path can produce "owner changed to non-stake/non-vote program AND lamports > 0 AND (if data was zeroed first) is not already caught by the deserialization-failure branch." The `set_owner` zero-data precondition [8](#0-7)  means this can only be reached through a state transition that the stake/vote program's own instruction set would have to perform (zero data, keep lamports, reassign owner) — no test coverage or call site confirming this exists was found in the indexed code. The authors' own `TODO` treats the gap as real and open, but its exploit path is not concretely demonstrated in local code.

### Recommendation
Add an explicit `else` branch (or equivalent check comparing against a previous cached owner) in `check_and_store` to evict `pubkey` from `stake_delegations`/`vote_accounts` whenever the account's *current* owner is neither `stake_program::id()` nor `solana_vote_program::id()`, regardless of lamports, closing the gap the `TODO` describes [9](#0-8) .

### Proof of Concept
Not fully constructible from static analysis alone: reaching the vulnerable branch requires demonstrating a concrete instruction sequence where a stake- or vote-program-owned account (a) has its data zeroed by its owning program, (b) has ownership legitimately reassigned away from `stake_program`/`solana_vote_program` per the `set_owner` rules, and (c) retains nonzero lamports throughout — none of which is exposed by any call site found in the indexed portion of the codebase. This would require dynamic testing (e.g., a targeted `BankClient`/SVM integration test simulating such a transition) to confirm reachability, which is beyond what the static index can verify with confidence.

### Citations

**File:** transaction-context/src/instruction_accounts.rs (L90-111)
```rust
    /// Assignes the owner of this account (transaction wide)
    pub fn set_owner(&mut self, pubkey: &[u8]) -> Result<(), InstructionError> {
        // Only the owner can assign a new owner
        if !self.is_owned_by_current_program() {
            return Err(InstructionError::ModifiedProgramId);
        }
        // and only if the account is writable
        if !self.is_writable() {
            return Err(InstructionError::ModifiedProgramId);
        }
        // and only if the data is zero-initialized or empty
        if !is_zeroed(self.get_data()) {
            return Err(InstructionError::ModifiedProgramId);
        }
        // don't touch the account if the owner does not change
        if self.get_owner().to_bytes() == pubkey {
            return Ok(());
        }
        self.touch()?;
        self.account.copy_into_owner_from_slice(pubkey);
        Ok(())
    }
```

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

**File:** runtime/src/stakes.rs (L219-223)
```rust
    /// current effective stake delegated to each vote account pubkey
    #[cfg_attr(feature = "frozen-abi", stable_abi_sample(with = "Default::default()"))]
    #[serde(skip)]
    #[wincode(skip)]
    delegated_stakes: DelegatedStakes,
```

**File:** runtime/src/bank.rs (L5776-5791)
```rust
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
