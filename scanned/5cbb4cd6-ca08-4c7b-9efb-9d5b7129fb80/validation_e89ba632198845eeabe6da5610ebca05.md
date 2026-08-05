### Title
Stake-delegation cache accumulator (`delegated_stakes`) can panic or corrupt on mid-epoch feature-gate flip because `Stakes::upsert_stake_delegation` recomputes the "old" effective stake with the *current* math flag instead of the flag that was in effect when the stake was originally added - (`File: runtime/src/stakes.rs`)

### Summary
`Stakes::upsert_stake_delegation()` mirrors the FrankenDAO bug pattern exactly: it recomputes a cached "voting power" value (here, delegated stake) at write time using externally-mutable parameters (`new_rate_activation_epoch`, `use_fixed_point_stake_math`), rather than remembering the exact value that was added when the account was first cached. If those parameters flip between the "add" and a later "subtract-then-re-add" of the same delegation, the code subtracts a freshly recomputed value from an accumulator that was built with the old value, which can either panic (`expect("subtraction value exceeds delegated stake")`) or silently desynchronize the cached total.

### Finding Description
`Stakes::upsert_stake_delegation` computes the effective stake of a delegation via `delegation_effective_stake()`, which dispatches between the legacy `Delegation::stake()` and the new `Delegation::stake_v2()` formula based on a boolean flag `use_fixed_point_stake_math`: [1](#0-0) 

When an existing stake pubkey is updated (e.g., its lamports/`Delegation` state changes within the same epoch), the code recomputes the *old* stake amount from the previously cached `StakeAccount` using the flags passed in for the *current* call, then subtracts it from the running `delegated_stakes` accumulator before adding the new amount: [2](#0-1) 

The subtraction itself uses an `expect()` that will panic if the recomputed "old" value differs from what is actually stored in the accumulator: [3](#0-2) 

`new_rate_activation_epoch` and `use_fixed_point_stake_math` are threaded in from `Bank` on every single account write via `StakesCache::check_and_store`: [4](#0-3) 

These two parameters are feature-gated values recomputed per `Bank`/per-slot from `bank.feature_set`, not values pinned to the epoch or to the specific delegation record. Since Solana feature activations take effect at the first slot where sufficient stake has voted for them — not necessarily at an epoch boundary — it is possible for `use_fixed_point_stake_math` (or the warmup/cooldown rate activation epoch) to change value between two banks that are still in the *same* epoch. Unlike the epoch-boundary path (`calculate_activated_stake`/`activate_epoch`), which fully recomputes `delegated_stakes` from scratch every epoch and therefore self-heals any drift, `upsert_stake_delegation`'s intra-epoch incremental path has no such correction: it trusts that recomputing the "old" stake with the *current* flag reproduces the value that was actually added to the accumulator under the *previous* flag.

This is structurally identical to the FrankenDAO bug: `getTokenVotingPower()`/`delegation_effective_stake()` is a pure function of mutable global parameters and account state, and the code assumes calling it twice (once at "stake"/insert time, once at "unstake"/update time) yields the same result — but the report's root cause (admin can change `monsterMultiplier`/`baseVotes` between the two calls) maps to Agave's admin-free but still externally-mutable analog: feature-gate activation of `use_fixed_point_stake_math`/the warmup-cooldown rate epoch between the two calls, mid-epoch.

### Impact Explanation
If `sub_delegated_stake`'s `expect()` panics, every validator processing that block hits the identical panic (the transaction and the feature-activation slot are canonical/deterministic), which halts block production/replay network-wide — a consensus-halt condition. If, instead, the recomputed value under-subtracts (rather than triggering the panic), the cached `delegated_stakes`/`vote_accounts` stake totals become permanently wrong for that vote account until the next epoch boundary recomputation, which can skew leader-schedule-adjacent totals and reward point calculations that read from this cache during the epoch.

### Likelihood Explanation
This requires no malicious actor: it is triggered by the ordinary combination of (a) a normal user/validator submitting a stake-account-modifying transaction (delegate, redelegate, split/merge, deactivate) and (b) a feature-gate flip for `use_fixed_point_stake_math` or the warmup/cooldown "new rate activation epoch" landing between two updates to the same stake pubkey within one epoch. Feature activations are infrequent, and this is a boundary-crossing race condition, so likelihood is lower than an always-reachable bug, but it is a genuine invariant gap in code that is otherwise carefully guarded (the epoch-boundary path recomputes from scratch specifically to avoid this class of drift, while the intra-epoch incremental path does not).

### Recommendation
Do not recompute the "old" effective stake for `sub_delegated_stake`/`vote_accounts.sub_stake` using the *current* call's `new_rate_activation_epoch`/`use_fixed_point_stake_math`. Instead, cache the effective stake value alongside the `StakeAccount` entry when it is inserted/updated (analogous to the report's recommended `tokenVotingPower` mapping), and use that stored value when removing/replacing the entry, so add and subtract are always symmetric regardless of feature-gate transitions that occur between the two calls.

### Proof of Concept
1. Bank B1 (epoch E, slot N): feature governing `use_fixed_point_stake_math` is inactive. A stake account S is delegated to voter V; `upsert_stake_delegation` computes `stake1 = delegation.stake(...)` (legacy path) and adds `stake1` to `delegated_stakes[V]` and `vote_accounts` via [5](#0-4) .
2. Between slot N and slot N+1, the feature governing `use_fixed_point_stake_math` activates (this can happen at any slot, not just epoch boundaries).
3. Bank B2 (still epoch E, slot N+1): a legitimate transaction modifies stake account S again (e.g., merges lamports into it). `check_and_store` is invoked with `use_fixed_point_stake_math = true` this time, per [6](#0-5) .
4. `upsert_stake_delegation` recomputes `old_stake = delegation.stake_v2(...)` for the *old* stored `StakeAccount` (same underlying `Delegation`/epoch/history as step 1, but new formula) at [7](#0-6) . If `stake_v2` and legacy `stake` disagree on this delegation (the entire reason the feature exists), `old_stake != stake1`.
5. `sub_delegated_stake(&V, old_stake)` at [3](#0-2)  either panics (`old_stake > delegated_stakes[V]`) or silently leaves an incorrect residual balance (`old_stake < stake1`), corrupting the cached total for voter V for the remainder of the epoch.

### Citations

**File:** runtime/src/stake_delegation.rs (L9-23)
```rust
#[inline]
pub(crate) fn delegation_effective_stake<T: StakeHistoryGetEntry>(
    delegation: &Delegation,
    epoch: Epoch,
    history: &T,
    new_rate_activation_epoch: Option<Epoch>,
    use_fixed_point_stake_math: bool,
) -> u64 {
    if use_fixed_point_stake_math {
        delegation.stake_v2(epoch, history, new_rate_activation_epoch)
    } else {
        #[allow(deprecated)]
        delegation.stake(epoch, history, new_rate_activation_epoch)
    }
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

**File:** runtime/src/stakes.rs (L562-576)
```rust
    fn sub_delegated_stake(&mut self, voter_pubkey: &Pubkey, stake: u64) {
        if stake == 0 {
            return;
        }
        let current_stake = self
            .delegated_stakes
            .get_mut(voter_pubkey)
            .expect("subtraction from missing delegated stake");
        *current_stake = current_stake
            .checked_sub(stake)
            .expect("subtraction value exceeds delegated stake");
        if *current_stake == 0 {
            self.delegated_stakes.remove(voter_pubkey);
        }
    }
```

**File:** runtime/src/stakes.rs (L620-658)
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
```
