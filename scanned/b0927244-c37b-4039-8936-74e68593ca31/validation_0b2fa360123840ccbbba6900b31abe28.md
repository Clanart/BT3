### Title
Panic-on-underflow in stake delegation bookkeeping when effective-stake math mode changes across cache updates - ([File: runtime/src/stakes.rs])

### Summary
The original report's bug class is: two independently-computed quantities (total minted vs. sum of per-token used amounts) are subtracted with a plain `.sub()` that reverts/underflows when the two bookkeeping paths disagree, permanently bricking repay/liquidate. Agave's `Stakes` cache maintains an analogous *aggregate-minus-per-item* invariant for `delegated_stakes`, and enforces it with `.expect()`/`panic!()` instead of a graceful/saturating fallback. If the two computations of "effective stake" for the same delegation ever disagree, the cache update panics instead of erroring, which — because `Stakes` is part of consensus-critical bank state that every validator computes identically — would crash every validator processing the same block.

### Finding Description
`StakesCache::check_and_store` incrementally adds/removes/updates per-account entries into an aggregate `delegated_stakes: ImblHashMap<Pubkey, u64>` map keyed by vote-account pubkey: [1](#0-0) 

The per-delegation "effective stake" contribution is computed via `delegation_effective_stake`, which dispatches between two *different* implementations depending on the `use_fixed_point_stake_math` flag — a deprecated floating-point-based `stake()` and a new fixed-point `stake_v2()`: [2](#0-1) 

`use_fixed_point_stake_math` is derived from feature-activation epoch and is passed into every `check_and_store`/`upsert_stake_delegation`/`remove_stake_delegation`/`upsert_stake_delegation` call individually, rather than being a fixed, versioned property stored alongside the cached delegated-stake value itself: [3](#0-2) 

When an existing delegation is removed or replaced, the code re-derives what it believes the *previously added* effective-stake value was by recomputing `delegation_effective_stake` at the current call time and then subtracts that recomputed value from the aggregate using `sub_delegated_stake`, which panics rather than saturating or erroring if the subtraction would underflow: [4](#0-3) 

```
fn sub_delegated_stake(&mut self, voter_pubkey: &Pubkey, stake: u64) {
    ...
    *current_stake = current_stake
        .checked_sub(stake)
        .expect("subtraction value exceeds delegated stake");
```

The invariant this code silently assumes is: *"the effective-stake value recomputed now for this delegation is bit-for-bit identical to whatever value was actually added into the aggregate earlier."* That invariant only holds if `delegation_effective_stake` is guaranteed to return the exact same number every time it's invoked for the same delegation/epoch, across every call site and every point in time it might be re-derived (including across the float→fixed-point (`stake()` → `stake_v2()`) math-mode transition). Nothing in `check_and_store`, `upsert_stake_delegation`, or `remove_stake_delegation` stores the value that was actually added; the "add" and "remove" amounts are each independently recomputed from scratch using whatever math mode is active at the time of the call, which is exactly the "independent bookkeeping paths that are assumed to always agree" pattern flagged in the original report (`totalMintedAmount` vs. sum of `usedMintedAmount`). A parallel, already-hardened instance of the same panic-on-underflow pattern exists for node stake bookkeeping: [5](#0-4) 

### Impact Explanation
`Stakes`/`StakesCache` update happens during normal bank account processing for every transaction that touches a stake or vote account and is executed identically by every validator (it's part of the deterministic state-transition function). A `panic!`/`.expect()` triggered here is not merely a local crash — because all correct validators evaluate the same code against the same account states, hitting this path would panic every validator processing the block simultaneously, producing a full network consensus halt (not merely a single-node RPC crash). That matches the "false execution/rooting/acceptance, consensus halt" impact category rather than a benign local error.

### Likelihood Explanation
This is a lower-confidence, structural finding rather than a demonstrated end-to-end exploit. I was not able to fully trace, within the available tool budget, a concrete unprivileged instruction sequence that forces `delegation_effective_stake` to return two different numeric values for the *same* delegation at two points where the cache expects them to match (e.g., straddling the `use_fixed_point_stake_math` feature-activation boundary, or interacting with `new_rate_activation_epoch` warmup/cooldown-rate changes that are also passed independently per call). Within a single epoch the flag appears to be derived consistently, and at epoch boundaries `calculate_delegated_stakes`/`refresh_vote_accounts` fully rebuild the aggregate map from scratch (which would mask a mismatch at that specific point). The residual risk is in the *incremental* per-transaction `upsert_stake_delegation`/`remove_stake_delegation` path that runs between epoch boundaries and never rebuilds from scratch — but confirming a concrete divergence there (e.g., proving `stake_v2()` can return a strictly larger value than `stake()` for some parameter combination reachable by an ordinary stake-program instruction) requires deeper numeric analysis of `Delegation::stake()` vs. `Delegation::stake_v2()` that I could not complete here.

### Recommendation
- Do not treat `.expect()`/`panic!` on stake-bookkeeping underflow as acceptable in consensus-critical code paths; replace with a `saturating_sub` plus a logged/metric-recorded warning (mirroring the recommendation to "minimize losses rather than revert/crash").
- Store the effective-stake value that was actually added to `delegated_stakes` alongside the delegation (or otherwise version-tag it with the math mode/epoch used), so `remove`/`upsert` operations subtract the *exact* previously-added value instead of re-deriving a value that could differ due to a different math mode or rate-activation parameter.
- Add fuzzing/property tests asserting `delegation_effective_stake` is stable and idempotent across repeated calls with the same delegation/epoch inputs, specifically across the `use_fixed_point_stake_math` feature-activation transition.

### Proof of Concept
No working proof-of-concept transaction sequence was constructed; this finding is based on static code-path analysis of `runtime/src/stakes.rs` (`sub_delegated_stake`, `remove_stake_delegation`, `upsert_stake_delegation`) and `runtime/src/stake_delegation.rs` showing the panic-on-underflow pattern and the two independently-selectable stake-math implementations feeding the same aggregate. Further Devin-session investigation (deep-diving `solana_stake_interface::state::Delegation::stake()` vs `stake_v2()` implementations and exact call ordering around feature activation) would be needed to confirm a concrete triggering sequence before this can be escalated beyond a structural/latent-risk finding.

### Citations

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

**File:** runtime/src/stakes.rs (L562-601)
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

    fn remove_vote_account(&mut self, vote_pubkey: &Pubkey) -> Option<VoteAccount> {
        self.vote_accounts.remove(vote_pubkey).map(|(_, a)| a)
    }

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

**File:** vote/src/vote_account.rs (L401-421)
```rust
    fn do_sub_node_stake(
        staked_nodes: &mut Arc<HashMap<Pubkey, u64>>,
        stake: u64,
        node_pubkey: &Pubkey,
    ) {
        if stake == 0u64 {
            return;
        }

        let staked_nodes = Arc::make_mut(staked_nodes);
        let current_stake = staked_nodes
            .get_mut(node_pubkey)
            .expect("this should not happen");
        match (*current_stake).cmp(&stake) {
            Ordering::Less => panic!("subtraction value exceeds node's stake"),
            Ordering::Equal => {
                staked_nodes.remove(node_pubkey);
            }
            Ordering::Greater => *current_stake -= stake,
        }
    }
```
