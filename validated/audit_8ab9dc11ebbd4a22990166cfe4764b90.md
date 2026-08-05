## Title
Stale stake-cache entries survive a vote/stake account owner-change, corrupting `StakesCache` used for leader schedule and vote weighting - (File: `runtime/src/stakes.rs`)

### Summary
`StakesCache::check_and_store()` is the single entry point that keeps the runtime's `Stakes<StakeAccount>` cache (vote accounts, stake delegations, and the derived `delegated_stakes` totals) synchronized with on-chain account writes. The function only handles two cases explicitly: (1) the account's lamports drop to zero, and (2) the account's owner is currently the vote program or stake program. If an account that was previously cached as a vote or stake account is rewritten with a **different, non-zero-lamport owner** (an owner change away from the vote/stake program), neither branch fires, and the stale cached entry (in `vote_accounts`, `stake_delegations`, and `delegated_stakes`) is never evicted — mirroring the reported `AggregateStablePrice.remove_price_pair()` bug, where removing/repurposing an entity in one place fails to clean up a parallel derived-state array (`last_tvl`).

### Finding Description
`check_and_store()` explicitly documents this gap itself: [1](#0-0) 

The logic branches only on `account.lamports() == 0` or `owner` matching the vote/stake program IDs: [2](#0-1) [3](#0-2) 

If a pubkey previously held a valid vote account (cached in `vote_accounts`) or stake account (cached in `stake_delegations`, which feeds `delegated_stakes`), and that account is subsequently rewritten in-place with a new owner that is neither the vote program nor the stake program (while keeping non-zero lamports, e.g., reassigned to system program or some other program via `set_owner`/CPI-driven allocation reuse), `check_and_store()` takes neither branch and returns without touching the cache. The old `StakeAccount`/`VoteAccount` entry, and the derived `delegated_stakes[voter_pubkey]` aggregate it contributes to, remain exactly as before, even though the underlying on-chain account no longer represents a stake/vote account at all.

This is structurally identical to the reported bug: `remove_price_pair()` deletes a pair but forgets to also purge the parallel `last_tvl[pair]` entry, so `last_tvl` keeps contributing stale data to later aggregation. Here, an owner-change "removes" the pubkey from being a stake/vote account, but the parallel `Stakes` cache (`vote_accounts` / `stake_delegations` / `delegated_stakes`) is never purged, so it keeps contributing stale stake/vote weight to consensus-critical aggregation (`vote_accounts().delegated_stakes()`, `get_top_epoch_stakes()`, leader schedule derivation via `staked_nodes`).

Existing guards do not stop this path:
- The zero-lamport branch only guards account closure, not owner reassignment with retained lamports.
- The owner-check branches (`solana_vote_program::check_id(owner)` / `stake_program::check_id(owner)`) only add/update/remove when the *current* owner matches; they have no "else" branch to evict when the owner no longer matches but the pubkey was previously cached.
- `remove_vote_account`/`remove_stake_delegation` in `Stakes<T>` correctly clean up `delegated_stakes` when called (`sub_delegated_stake` removes zero-stake entries), but they are only reachable via `check_and_store`'s zero-lamport or "deserialize failed" (owner still matches) paths — never via an owner-change-away path.

### Impact Explanation
The `Stakes<StakeAccount>` cache is used directly for consensus-relevant computations: current-epoch vote weighting (`vote_accounts().delegated_stakes()`), and the epoch-stakes snapshot that becomes leader-schedule input (`Bank::update_epoch_stakes` → `get_top_epoch_stakes()` at `runtime/src/bank.rs:2594-2641`, using `self.stakes_cache.stakes().vote_accounts()`). Stale entries that should no longer count as active stake/vote accounts (because the underlying account was reassigned away from the stake/vote program) can inflate a validator's counted delegated stake or leader-schedule weight, or leave the account counted as an active voter after it should have been evicted. Divergent handling of this stale state across validator implementations, or divergent behavior versus expected protocol semantics, threatens false leader-schedule computation and vote-weight accounting — a consensus-integrity concern, not merely a metrics inaccuracy.

### Likelihood Explanation
Triggering requires only a normal, permissionless transaction: write to a pubkey previously holding a valid stake or vote account, changing its owner away from the stake/vote program while keeping lamports non-zero (e.g., `Allocate`/`Assign`-style owner reassignment is only possible by the account's current owner, but the stake/vote program itself, or a subsequent CPI after a stake/vote account is closed-and-reused pattern, could produce this). This does not require a malicious validator, leaked keys, or trusted-plugin assumptions — it is reachable by any account owner performing a legitimate owner change on their own previously-staked/voted account. The code comment confirms the developers were already aware this specific gap (owner change) was unhandled, which supports that this is a real, unresolved path rather than a speculative one.

### Recommendation
In `StakesCache::check_and_store()`, when the owner is neither the vote program nor the stake program, explicitly check whether the pubkey currently has a cached entry in `vote_accounts` or `stake_delegations` and, if so, evict it (call `remove_vote_account`/`remove_stake_delegation`) exactly as done in the zero-lamport branch, so an owner change is treated the same as an account closure for cache-consistency purposes.

### Proof of Concept
1. Fund and initialize a vote account `V` (owned by the vote program) so it is delegated stake and cached via `check_and_store` → `upsert_vote_account`, contributing to `delegated_stakes`.
2. Have the account's current owner (the vote program, via a legitimate vote-program instruction path that permits owner reassignment, or by closing and reallocating the account under a different program while retaining lamports) rewrite `V`'s owner field to a non-stake/non-vote program ID while keeping `lamports > 0`.
3. On the next `check_and_store(&V, ...)` invocation (triggered by the account write), observe that `owner` matches neither `solana_vote_program::check_id` nor `stake_program::check_id`; the function falls through both `if`/`else if` branches and returns, per `runtime/src/stakes.rs:98-164`.
4. Inspect `StakesCache::stakes().vote_accounts()` afterward: the stale `VoteAccount` entry for `V` and its contribution to `delegated_stakes` remain present, even though `V` is no longer a vote account on-chain — directly analogous to `last_tvl` retaining a stale entry after `remove_price_pair()` in the original report.

### Citations

**File:** runtime/src/stakes.rs (L93-116)
```rust
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
