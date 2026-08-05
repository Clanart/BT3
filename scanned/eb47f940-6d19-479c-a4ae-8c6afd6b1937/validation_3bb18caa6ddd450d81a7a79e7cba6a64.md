Based on my investigation, I found a concrete, code-documented analog of the SAM bug class in Agave's stake/vote caching layer. The core parallel: SAM trusted a one-time check performed at `create()` and never re-validated it on later state-changing calls, letting stale/forged trust persist. Agave's `StakesCache` has the same structural flaw — an owner check is only re-applied conditionally, and the code itself documents (via TODO comments and a test that hard-codes the unfixed behavior) that a changed owner does not evict stale cached stake/vote data.

### Title
Stale/forged stake and vote delegation entries can persist in `StakesCache` after an account's owner changes, because `check_and_store` never evicts entries once the owner check no longer matches - ([File: runtime/src/stakes.rs])

### Summary
`StakesCache::check_and_store` re-derives an account's role (vote account vs. stake account) purely from `account.owner()` on every update, and only takes action (upsert or remove) when the *current* owner matches the vote or stake program ID. If a previously-cached account's owner is changed away from `solana_vote_program`/`stake_program`, the function's `if`/`else if` chain falls through with no branch executed, so the old cached delegation/vote entry is never evicted. [1](#0-0) 

### Finding Description
The check-then-cache pattern is explicitly acknowledged as broken in the code's own TODO: [2](#0-1) 

`check_and_store` only clears a cache entry when the account has zero lamports (treated as a close) via the `solana_vote_program::check_id(owner)` / `stake_program::check_id(owner)` branches, or upserts it when the owner still matches the expected program. There is no `else` branch to handle the case where an account previously cached as a stake/vote account is updated with lamports > 0 and a *different* owner — the update is silently dropped and the stale entry remains in `Stakes<StakeAccount>` (`vote_accounts` / `stake_delegations`). [3](#0-2) 

This mirrors the SAM root cause exactly: a security-critical decision (whether this pubkey should be trusted as a stake/vote source of truth) is derived once and then never re-validated when the underlying entity's identity/ownership actually changes, letting the cached (and therefore trusted) view diverge from ground truth in accounts-db.

This exact scenario is validated by a dedicated regression test that intentionally reassigns the owner of a staked vote account and a delegated stake account to bogus program IDs, then asserts the stale/invalid entries are *still* returned as valid by the loader that reads from this cache path: [4](#0-3) 

The test driver explicitly documents this as a known, unresolved hardening gap referencing an old upstream PR discussion, and asserts `check_owner_change: false`, i.e. the current code does **not** correctly react to an owner change: [5](#0-4) 

There is also a feature flag named `evict_invalid_stakes_cache_entries` in the feature set, suggesting this gap was identified as needing a dedicated fix/feature-gated correction, but I could not find any code in `runtime/src/stakes.rs` or `runtime/src/bank.rs` that actually gates behavior on this feature ID — it only appears in the feature-set registration/description, not wired into `check_and_store` or any eviction path. [6](#0-5) 

### Impact Explanation
If a mechanism exists to reassign a previously-delegated stake account's or voted-for vote account's owner away from the stake/vote program while keeping lamports > 0 (or feeding a stale, unevicted cache entry into consensus-relevant computations), the bank would continue to count that pubkey's old cached delegation/vote weight in `stakes_cache.stakes()`, which feeds leader schedule, vote weighting for fork choice, and reward calculation (`_load_vote_and_stake_accounts`, `bank.vote_accounts()`). This is a "false execution/rooting/acceptance"-class impact: stale/forged trust is used to compute consensus-relevant stake weight rather than accounts-db ground truth.

### Likelihood Explanation
I could not find, in the available local code, a currently-supported *permissionless* instruction path that lets an account owned by the stake or vote program be reassigned to a different owner while retaining nonzero lamports (Agave's generic `set_owner` guard requires the account to already be owned by the calling program **and** zero-initialized data before an owner change is permitted, per `transaction-context/src/instruction_accounts.rs`). I was not able to locate such an owner-reassignment instruction inside `programs/stake-*` or `programs/vote-*` in this index within my search budget, so I cannot confirm a concrete, currently-reachable trigger for an unprivileged attacker today. What is confirmed and unambiguous from local evidence is that the invariant-re-validation gap itself exists in `check_and_store`, is explicitly called out as unresolved by the maintainers' own TODO comment, and is locked in by a test (`test_stake_vote_account_validity`) that asserts the *current* (broken) behavior rather than a fixed one. This makes it a real, documented latent defect rather than a fully demonstrated end-to-end exploit — I flag this uncertainty explicitly rather than asserting a confirmed unprivileged trigger. [7](#0-6) 

### Recommendation
Add an explicit `else` branch (or equivalent owner-mismatch check) in `StakesCache::check_and_store` that evicts any existing cache entry for `pubkey` whenever the account's current owner no longer matches the program that owned it at the time it was cached (i.e., implement the `evict_invalid_stakes_cache_entries` behavior the feature flag name implies), rather than relying solely on `check_id(owner)` branches that silently no-op on mismatch. Update `test_stake_vote_account_validity` to assert `check_owner_change: true` once fixed. [1](#0-0) 

### Proof of Concept
The existing test demonstrates the stale-cache condition directly (owner reassigned to `bogus_stake_program`/`bogus_vote_program`, followed by an assertion that the invalid entries are still returned as valid, i.e., `check_owner_change: false`): [8](#0-7) 

I was unable to construct or locate, within the indexed code, a concrete unprivileged instruction sequence that reaches this code path outside of directly calling `bank.store_account` (as the test does), so this should be treated as a confirmed latent defect requiring further investigation (ideally via a Devin session with full repository/build access) to determine whether any current stake/vote program instruction, CPI pattern, or account-reuse trick can trigger this owner change in production.

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

**File:** runtime/src/bank/tests.rs (L8478-8489)
```rust
#[test]
fn test_stake_vote_account_validity() {
    let thread_pool = ThreadPoolBuilder::new().num_threads(1).build().unwrap();
    // TODO: stakes cache should be hardened for the case when the account
    // owner is changed from vote/stake program to something else. see:
    // https://github.com/solana-labs/solana/pull/24200#discussion_r849935444
    check_stake_vote_account_validity(
        false, // check owner change
        |bank: &Bank| bank._load_vote_and_stake_accounts(&thread_pool, null_tracer()),
    );
}

```

**File:** runtime/src/bank/tests.rs (L8556-8589)
```rust
        &vote_account,
    );

    // Modify staked vote account owner; a vote account owned by another program could be
    // freely modified with malicious data
    let bogus_vote_program = Pubkey::new_unique();
    vote_account.set_lamports(original_lamports);
    vote_account.set_owner(bogus_vote_program);
    bank.store_account(
        &validator_vote_keypairs0.vote_keypair.pubkey(),
        &vote_account,
    );

    assert_eq!(bank.vote_accounts().len(), 1);

    // Modify stake account owner; a stake account owned by another program could be freely
    // modified with malicious data
    let bogus_stake_program = Pubkey::new_unique();
    let mut stake_account = bank
        .get_account(&validator_vote_keypairs1.stake_keypair.pubkey())
        .unwrap_or_default();
    stake_account.set_owner(bogus_stake_program);
    bank.store_account(
        &validator_vote_keypairs1.stake_keypair.pubkey(),
        &stake_account,
    );

    // Accounts must be valid stake and vote accounts
    let vote_and_stake_accounts = load_vote_and_stake_accounts(&bank);
    assert_eq!(
        vote_and_stake_accounts.len(),
        usize::from(!check_owner_change)
    );
}
```

**File:** feature-set/src/lib.rs (L1721-1724)
```rust
        (
            evict_invalid_stakes_cache_entries::id(),
            "evict invalid stakes cache entries on epoch boundaries",
        ),
```

**File:** transaction-context/src/instruction_accounts.rs (L91-111)
```rust
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
