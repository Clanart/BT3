### Title
Underflow panic in `Stakes::sub_delegated_stake` / `VoteAccounts::sub_stake` when `use_fixed_point_stake_math` toggles mid-epoch causes recomputed stake to exceed cached value - (File: `runtime/src/stakes.rs`)

### Summary
The Malt bug is a case where code subtracts two values that are assumed to be ordered consistently (`latestSample >= priceTarget`), but the two values are actually derived from different sampling contexts (a point sample vs. an averaged/target value), so the subtraction can underflow/revert. Agave's `Stakes<StakeAccount>` cache has the same structural pattern: `delegated_stakes`/`vote_accounts` totals are maintained incrementally by adding/subtracting a freshly recomputed `delegation_effective_stake(...)` value, under the assumption that the value being subtracted always matches what was previously added for that same stake account. That assumption is not guarded, and the effective-stake computation depends on the external, mutable `use_fixed_point_stake_math` flag, which can change between the time a contribution was added to the cache and the time it is later removed/recomputed.

### Finding Description
`Stakes::upsert_stake_delegation` and `Stakes::remove_stake_delegation` (`runtime/src/stakes.rs:562-660`) maintain running totals in `delegated_stakes` (a `HashMap<Pubkey, u64>`) and in `VoteAccounts` (`vote/src/vote_account.rs`). Both add and subtract paths call `delegation_effective_stake(...)` (`runtime/src/stake_delegation.rs`) to compute the stake amount currently attributed to a delegation, using the bank's *current* `epoch`, `stake_history`, and the boolean `use_fixed_point_stake_math` flag: [1](#0-0) 

and: [2](#0-1) 

The subtraction is unchecked with respect to whether the value being removed actually matches what was originally contributed: [3](#0-2) 

The same brittle pattern exists one layer up in `VoteAccounts::sub_stake` and `do_sub_node_stake`, both of which `.expect()`/`panic!` on any mismatch: [4](#0-3) 

`use_fixed_point_stake_math` is a bank-level flag threaded through every one of these calls (`runtime/src/bank.rs`, `runtime/src/stakes.rs`) and is derived from feature-set activation state, which can change at a specific *slot* boundary that is not necessarily an *epoch* boundary. `Stakes::refresh_delegated_stakes` (which fully recomputes `delegated_stakes` from scratch for every stake account, restoring consistency) is only invoked explicitly at epoch boundaries / snapshot restore, e.g.: [5](#0-4) 

Between the slot where the math-mode flag flips and the next full `refresh_delegated_stakes`/epoch-boundary recompute, `self.epoch`/`self.stake_history` stay fixed but the boolean passed into `delegation_effective_stake` differs from what was used when the cached contribution was originally computed for a given stake account. `Bank::update_stakes_cache` (`runtime/src/bank.rs:5756-5792`) is invoked for *every* successfully-processed transaction that writes a stake account — this is a fully unprivileged code path: any user submitting a normal stake instruction (e.g. `Delegate`, `Split`, `Merge`, `Deactivate`, `SetLockup`, `Withdraw`) that touches an *existing* stake delegation whose voter/stake did not change causes `upsert_stake_delegation` to recompute `old_stake` with the (potentially new) math mode and compare it against the currently cached total, which was populated using the old math mode.

If fixed-point vs. floating-point stake math produce even a 1-lamport difference for the same delegation/epoch/history input (this is exactly the kind of rounding discrepancy the two implementations are expected to differ by, per the various fixed-point-stake-math test/tracer code seen throughout `runtime/src/inflation_rewards/*` and `runtime/src/stakes.rs`), then `old_stake` computed under the new flag can be *greater* than the cached total that was accumulated under the old flag. In that case, `sub_delegated_stake`'s `checked_sub(...).expect("subtraction value exceeds delegated stake")` and/or `VoteAccounts::sub_stake`'s `checked_sub(...).expect("subtraction value exceeds account's stake")` will underflow and panic.

### Impact Explanation
`upsert_stake_delegation`/`remove_stake_delegation` execute as native, in-process Rust code inside `Bank::update_stakes_cache`, which runs synchronously as part of committing every processed block on *every validator in the cluster* (it is not sandboxed like BPF program execution). A panic here is not caught as an `InstructionError` — it aborts the validator process outright. Because block processing is deterministic and this path runs identically on all replaying nodes, a single unprivileged transaction landing in the "seam" slot after `use_fixed_point_stake_math` toggles could simultaneously crash every validator that processes that block, which is a consensus-halting, cluster-wide denial of service, not merely a single-node crash.

### Likelihood Explanation
This requires: (1) a stake account that is already resident in `stake_delegations` prior to the feature-gate flip, and (2) any subsequent transaction (from any account holder, not necessarily the stake authority — any writable touch that re-serializes the stake account with an unchanged `voter_pubkey`/effectively-same delegation) that causes `check_and_store`/`upsert_stake_delegation` to run in the slot(s) after the flag flips but before the next explicit `refresh_delegated_stakes` call. Since `use_fixed_point_stake_math` is governed by a feature-set activation slot rather than an epoch boundary, and epoch boundaries are the only place `refresh_delegated_stakes` is invoked in the normal block-processing flow, there is a real window where stale-mode contributions coexist with new-mode recomputation. The precise magnitude/direction of the fixed-point vs. floating-point rounding differential was not directly verified in `runtime/src/stake_delegation.rs` in this pass (index truncation prevented reading `delegation_effective_stake`'s body), so it is uncertain whether the discrepancy can ever be large enough, or in the unfavorable direction, to trigger underflow versus merely causing silent accounting drift. This should be confirmed by reading `runtime/src/stake_delegation.rs` in full and by checking whether `use_fixed_point_stake_math`'s activation is always paired with a forced `refresh_delegated_stakes` call outside the epoch-boundary path (which would close this window and invalidate the finding).

### Recommendation
- Ensure any change to `use_fixed_point_stake_math`'s effective value is atomically paired with a call to `Stakes::refresh_delegated_stakes` (or equivalent full recomputation) so that `delegated_stakes`/`vote_accounts` totals are never computed with a math mode different from the one in effect during the prior accumulation.
- Replace the `.expect()`/`panic!` calls in `sub_delegated_stake`, `VoteAccounts::sub_stake`, and `do_sub_node_stake` with saturating subtraction plus a recoverable error/metric, so a residual inconsistency degrades gracefully (stale/slightly-off cached stake) instead of crashing the validator process.
- Add an invariant test that flips `use_fixed_point_stake_math` mid-epoch (without an epoch boundary in between) and asserts that subsequent stake-account upserts/removals do not panic.

### Proof of Concept
Not independently reproducible from static analysis alone; a concrete PoC would require: constructing a `Bank` with `use_fixed_point_stake_math() == false`, inserting a stake delegation (populating `delegated_stakes`/`vote_accounts` via floating-point math), then flipping the corresponding feature/flag such that `use_fixed_point_stake_math()` becomes `true` without an intervening epoch boundary or `refresh_delegated_stakes` call, and finally submitting/simulating a transaction that re-writes the same stake account (e.g., via `Bank::check_and_store` invoked from `update_stakes_cache`) to trigger `upsert_stake_delegation`'s `old_stake` recomputation under the new mode. Whether the fixed-point/floating-point discrepancy is ever large enough or in the underflow-triggering direction needs to be confirmed against `runtime/src/stake_delegation.rs`'s `delegation_effective_stake` implementation, which could not be fully retrieved in this session due to index limits — a Devin session with full repository access would be needed to complete this verification.

### Citations

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

**File:** runtime/src/stakes.rs (L620-659)
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
```

**File:** vote/src/vote_account.rs (L359-421)
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

    fn add_node_stake(&mut self, stake: u64, vote_account: &VoteAccount) {
        let Some(staked_nodes) = self.staked_nodes.get_mut() else {
            return;
        };

        VoteAccounts::do_add_node_stake(staked_nodes, stake, *vote_account.node_pubkey());
    }

    fn do_add_node_stake(
        staked_nodes: &mut Arc<HashMap<Pubkey, u64>>,
        stake: u64,
        node_pubkey: Pubkey,
    ) {
        if stake == 0u64 {
            return;
        }

        Arc::make_mut(staked_nodes)
            .entry(node_pubkey)
            .and_modify(|s| *s += stake)
            .or_insert(stake);
    }

    fn sub_node_stake(&mut self, stake: u64, vote_account: &VoteAccount) {
        let Some(staked_nodes) = self.staked_nodes.get_mut() else {
            return;
        };

        VoteAccounts::do_sub_node_stake(staked_nodes, stake, vote_account.node_pubkey());
    }

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

**File:** runtime/src/bank.rs (L6075-6079)
```rust
        self.compute_and_apply_features_after_snapshot_restore();
        self.stakes_cache.refresh_delegated_stakes(
            self.new_warmup_cooldown_rate_epoch(),
            self.use_fixed_point_stake_math(),
        );
```
