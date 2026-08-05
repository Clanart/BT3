### Title
Panic-inducing inconsistency between `delegated_stakes` and `VoteAccounts` stake bookkeeping during stake account upsert/removal - (File: `runtime/src/stakes.rs`)

### Summary
`Stakes<T>` maintains two separate, redundantly-tracked aggregates of stake per validator vote account: the `delegated_stakes` map and the per-entry stake counters inside `VoteAccounts` (`vote_accounts`). Every time a stake account is processed via `check_and_store` → `upsert_stake_delegation`/`remove_stake_delegation`, both aggregates must be updated in lock-step with matching add/sub operations, exactly the "complicated state updates" pattern flagged in the Audius report (multiple operations to increase/decrease related counters that must remain complete, consistent, and complementary).

### Finding Description
`upsert_stake_delegation` and `remove_stake_delegation` each perform a pair of update operations on two different data structures for what is conceptually a single state transition: [1](#0-0) 

```
match self.stake_delegations.insert(stake_pubkey, stake_account) {
    None => {
        self.add_delegated_stake(voter_pubkey, stake);
        self.vote_accounts.add_stake(&voter_pubkey, stake);
    }
    Some(old_stake_account) => {
        ...
        if voter_pubkey != old_voter_pubkey || stake != old_stake {
            self.sub_delegated_stake(&old_voter_pubkey, old_stake);
            self.add_delegated_stake(voter_pubkey, stake);
            self.vote_accounts.sub_stake(&old_voter_pubkey, old_stake);
            self.vote_accounts.add_stake(&voter_pubkey, stake);
        }
    }
}
```

`sub_delegated_stake` on the `delegated_stakes` map is a hard invariant-checked operation: it panics via `.expect("subtraction from missing delegated stake")` and `.expect("subtraction value exceeds delegated stake")` if the accounting is off: [2](#0-1) 

By contrast, `VoteAccounts::sub_stake` silently does nothing if the target vote account is not present in `vote_accounts` (`if let Some(...) = vote_accounts.get_mut(pubkey)`), but if present, it also unconditionally panics on underflow: [3](#0-2) 

`remove_stake_delegation` performs the same complementary pair of decrements: [4](#0-3) 

Both `delegated_stakes` and `vote_accounts` stake fields are populated from independent code paths: `delegated_stakes` is seeded/rebuilt wholesale via `calculate_delegated_stakes`/`refresh_delegated_stakes` (which iterates `stake_delegations` and sums `delegation_effective_stake` per voter), while `vote_accounts`'s per-account stake is seeded lazily at insertion time via the `calculate_delegated_stake` closure that reads back from `delegated_stakes` at `upsert_vote_account` time: [5](#0-4) 

Because these two aggregates are derived and mutated through different call sites (`check_and_store` for stake accounts vs. vote accounts, `activate_epoch`/`refresh_delegated_stakes` for whole-map rebuilds), any timing/ordering skew between when a vote account is inserted (which snapshots `delegated_stakes` at that instant into `vote_accounts`) and subsequent incremental `add_delegated_stake`/`sub_delegated_stake` calls on the `delegated_stakes` map creates the exact "complicated, error-prone, multi-operation state update" pattern called out in the seed report — a class of bug where partial correctness (updating one structure and not the complementary one, or updating with a mismatched delta) is easy to introduce and hard to statically verify, and here the fallback for an inconsistency is an `unwrap`/`expect` panic rather than a controlled error path.

### Impact Explanation
If the two derived aggregates can diverge (e.g., due to an ordering bug between vote-account insertion snapshotting `delegated_stakes` and a subsequent `sub_delegated_stake`/`add_delegated_stake` on a re-delegated or removed stake account), the `.expect()` calls in `sub_delegated_stake` will panic the validator process during `check_and_store`/`upsert_stake_delegation`, which runs on the runtime/accounts hot path during normal block processing. A panic in this code path on a live validator constitutes a crash on the node processing the block — if the underlying inconsistency is reachable by an ordinary (unprivileged) sequence of stake-delegate/undelegate operations across epoch boundaries, this could cause a non-RPC crash impacting consensus availability rather than a benign, contained error.

### Likelihood Explanation
This is a structural risk rather than a demonstrated concrete trigger: I was not able to construct, from local code alone, a definitive sequence of user-controlled stake/vote account transactions that provably desynchronizes `delegated_stakes` from `vote_accounts`' stake counters within a single epoch (both aggregates appear designed to be rebuilt in full at epoch boundaries via `activate_epoch`/`refresh_vote_accounts`, and the same effective-stake computation function is used everywhere). The likelihood therefore depends on whether there exists an intra-epoch ordering (e.g., stake account update processed before/after the corresponding vote account's own upsert within the same batch, or interleaved with a validator's own vote-account modification) that leaves the two maps out of sync before the next full rebuild — this could not be conclusively confirmed or ruled out with the available index/tool access.

### Recommendation
Follow the same remediation pattern used in the referenced audius-protocol fix (PR #539): encapsulate the paired stake increment/decrement operations across `delegated_stakes` and `vote_accounts` into a single internal function that updates both structures atomically and is unit-tested in isolation for every combination of insert/update/remove/voter-change; replace panicking `.expect()`s in `sub_delegated_stake`/`VoteAccounts::sub_stake` with either a saturating operation plus a metric/error-log, or an explicit invariant check performed once per epoch (post-rebuild) that can detect and safely repair divergence rather than panicking mid-block-processing on the validator's hot path. Additionally, add a debug/test-only cross-check (e.g., recomputing `delegated_stakes` from `stake_delegations` and asserting equality against `vote_accounts`' per-pubkey stake) exercised in CI to catch any future incremental-update mismatch before it reaches production.

### Proof of Concept
No concrete, fully-verified PoC transaction sequence could be constructed from local code inspection alone within the available time; the analysis instead demonstrates the structural analog (dual redundant counters updated via separate, unchecked-vs-checked increment/decrement helper functions spread across two files/modules) directly matching the seed report's "complicated state updates" bug class. A background engineer with full repository/test access should attempt to construct a minimal `Stakes<StakeAccount>` unit test that: (1) inserts a vote account (snapshotting `delegated_stakes` via `upsert_vote_account`), (2) upserts a stake account delegating to that voter, (3) changes the same stake account's `voter_pubkey` or effective stake in a way that exercises `upsert_stake_delegation`'s `Some(old_stake_account)` branch under different epoch/`stake_history` inputs, and (4) asserts whether `delegated_stakes.get(voter_pubkey)` and the vote account's internal stake counter in `vote_accounts` ever diverge or trigger the `.expect()` panics in `sub_delegated_stake` / `VoteAccounts::sub_stake`.

### Citations

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

**File:** runtime/src/stakes.rs (L582-601)
```rust
    fn remove_stake_delegation(
        &mut self,
        stake_pubkey: &Pubkey,
        new_rate_activation_epoch: Option<Epoch>,
        use_fixed_point_stake_math: bool,
    ) {
        if let Some(stake_account) = self.stake_delegations.remove(stake_pubkey) {
            let removed_delegation = stake_account.delegation();
            let removed_stake = delegation_effective_stake(
                removed_delegation,
                self.epoch,
                &self.stake_history,
                new_rate_activation_epoch,
                use_fixed_point_stake_math,
            );
            self.sub_delegated_stake(&removed_delegation.voter_pubkey, removed_stake);
            self.vote_accounts
                .sub_stake(&removed_delegation.voter_pubkey, removed_stake);
        }
    }
```

**File:** runtime/src/stakes.rs (L603-618)
```rust
    fn upsert_vote_account(
        &mut self,
        vote_pubkey: &Pubkey,
        vote_account: VoteAccount,
    ) -> Option<VoteAccount> {
        debug_assert_ne!(vote_account.lamports(), 0u64);

        let calculate_delegated_stake = || {
            self.delegated_stakes
                .get(vote_pubkey)
                .copied()
                .unwrap_or_default()
        };
        self.vote_accounts
            .insert(*vote_pubkey, vote_account, calculate_delegated_stake)
    }
```

**File:** runtime/src/stakes.rs (L637-659)
```rust
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
```

**File:** vote/src/vote_account.rs (L359-368)
```rust
    pub fn sub_stake(&mut self, pubkey: &Pubkey, delta: u64) {
        let vote_accounts = Arc::make_mut(&mut self.vote_accounts);
        if let Some((stake, vote_account)) = vote_accounts.get_mut(pubkey) {
            *stake = stake
                .checked_sub(delta)
                .expect("subtraction value exceeds account's stake");
            let vote_account = vote_account.clone();
            self.sub_node_stake(delta, &vote_account);
        }
    }
```
