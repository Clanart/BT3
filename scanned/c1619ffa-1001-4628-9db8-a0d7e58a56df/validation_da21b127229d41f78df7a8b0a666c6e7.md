Given the extensive search, the closest genuine Agave analog to the Ditto bug (two differently-timed reads of the "same" quantity feeding into an unchecked/`expect`-panicking subtraction) is in `Stakes::sub_delegated_stake` / `Stakes::upsert_stake_delegation` / `Stakes::remove_stake_delegation` in `runtime/src/stakes.rs`.

### Title
Feature-flag-dependent stake recomputation can desynchronize `delegated_stakes` bookkeeping and panic on subtraction underflow - (File: runtime/src/stakes.rs)

### Summary
`Stakes::delegated_stakes` is a running per-vote-account total that is incrementally updated (`add_delegated_stake` / `sub_delegated_stake`) every time a stake account is stored during transaction processing, via `StakesCache::check_and_store` → `upsert_stake_delegation` / `remove_stake_delegation`. Each incremental update *recomputes* the stake amount to add/remove by calling `delegation_effective_stake(delegation, self.epoch, &self.stake_history, new_rate_activation_epoch, use_fixed_point_stake_math)` at the time of the update, rather than storing/re-using the value that was originally added. `use_fixed_point_stake_math` is threaded straight through from the current `Bank`'s feature set on every call to `update_stakes_cache` [1](#0-0) , i.e. it can differ between the transaction that first inserted a delegation's stake into the accumulator and a later transaction (in the same epoch) that updates/removes it, if the corresponding feature activates in between.

### Finding Description
`upsert_stake_delegation` computes `old_stake` for the value being replaced using the *current* `use_fixed_point_stake_math`/`new_rate_activation_epoch`, and calls `sub_delegated_stake(&old_voter_pubkey, old_stake)` [2](#0-1) . `sub_delegated_stake` then unconditionally panics via `.expect(...)` if the recomputed value does not match what is actually stored in the accumulator: [3](#0-2) 

The value originally added to `delegated_stakes` for that same delegation was computed by an *earlier* call (either the initial `calculate_delegated_stakes`/`refresh_delegated_stakes` at epoch start, or an earlier `upsert_stake_delegation`) using whatever `use_fixed_point_stake_math` value was in effect for that earlier `Bank`. Both `use_fixed_point_stake_math` and `new_rate_activation_epoch` are passed to `delegation_effective_stake` fresh on every call from `Bank::update_stakes_cache` [4](#0-3) , and `delegation_effective_stake` dispatches between the legacy floating-point `stake()` computation and the new fixed-point `stake_v2()` computation [5](#0-4) . These two computations are not guaranteed to produce bit-identical `u64` results for the same delegation/epoch/history, since one is float-based and the other fixed-point. Unlike `new_rate_activation_epoch` (which is itself an epoch-boundary marker meant to phase in a new warmup/cooldown rate), the `use_fixed_point_stake_math` boolean is derived directly from the feature set and is not itself epoch-gated in this update path — nothing in `upsert_stake_delegation`/`sub_delegated_stake` prevents it from flipping between the slot where a delegation's stake was first counted into `delegated_stakes` and a later slot in the same epoch where that same delegation is modified again (e.g. deactivated, split, merged, or simply re-stored due to any account write), because the accumulator is only *fully* recomputed at the epoch boundary (`activate_epoch`/`calculate_activated_stake`), not on every feature flip.

Existing guards do not stop this path: `sub_delegated_stake` has no fallback (e.g., saturating_sub or best-effort clamp) — it is a hard `.expect()` panic if the recomputed `old_stake` exceeds what is actually stored for that voter, or if the voter key is missing.

### Impact Explanation
An unrecoverable Rust panic (`.expect("subtraction value exceeds delegated stake")`) inside `Bank::update_stakes_cache`, which runs synchronously for every validator replaying/executing the same block of transactions, is not a per-transaction sandboxed failure — it aborts the bank-processing thread. Because every honest validator executing the identical transaction sequence would hit the identical mismatch deterministically, this is not merely a single-node crash but a network-wide consensus-halt risk: all validators that process the triggering transaction would panic identically. This falls under "non-RPC remote exhaustion/crash" and "consensus halt" impact categories.

### Likelihood Explanation
Likelihood is low-to-moderate and time-window-dependent: it requires a stake-math-related feature (`use_fixed_point_stake_math`) to transition its activation state strictly inside an epoch (rather than being aligned to the epoch boundary the way `new_rate_activation_epoch` is), and requires an ordinary, unprivileged stake-modifying transaction (delegate/split/merge/deactivate/withdraw, any of which cause `check_and_store` to re-run `upsert_stake_delegation`/`remove_stake_delegation` for that account) to land on both sides of that activation boundary for the same delegation. This is realistically triggerable only during the rollout of such a feature and is not attacker-controlled in the general case (feature activation timing is a network governance event), which limits it to a narrow, hard-to-force window rather than a trivially repeatable exploit.

I was not able to fully confirm, within the available search budget, whether `Bank::use_fixed_point_stake_math()` itself is internally epoch-gated elsewhere (e.g. only flips at epoch boundaries by design, mirroring `new_rate_activation_epoch`), which would foreclose this scenario entirely. This is an important open question that should be verified directly in `runtime/src/bank.rs`'s definition of `use_fixed_point_stake_math()` and its associated feature-gate documentation before treating this as a confirmed, actionable vulnerability.

### Recommendation
- Verify whether `use_fixed_point_stake_math` transitions are epoch-boundary-gated; if not, gate them the same way `new_rate_activation_epoch` is, so that within a single epoch all `delegated_stakes` accumulator operations use a consistent math mode.
- Replace the hard `.expect()` panics in `sub_delegated_stake` (and the analogous `do_sub_node_stake` in `vote/src/vote_account.rs`) with a recoverable path (e.g., saturating subtraction plus a full `refresh_delegated_stakes` recomputation, or a `datapoint_error!`-logged clamp to zero) so a bookkeeping mismatch degrades gracefully instead of crashing the process.
- Consider recomputing/verifying `delegated_stakes` from `stake_delegations` directly whenever a relevant feature-gate transition is detected mid-epoch.

### Proof of Concept
Conceptual reproduction (not fully executable without controlling feature-activation slot):
1. Create a stake delegation for voter `V` that is partially activating/deactivating, such that the legacy float-based `stake()` and fixed-point `stake_v2()` calculations diverge for the same `(delegation, epoch, stake_history)` inputs.
2. In slot N (before the `use_fixed_point_stake_math`-gating feature activates), submit a transaction that stores/updates this stake account, causing `upsert_stake_delegation` to add `stake_v1` into `delegated_stakes[V]`.
3. Have the feature activate at slot N+1 within the same epoch (no epoch boundary crossed), flipping `use_fixed_point_stake_math` to `true` for subsequent banks.
4. In slot N+1, submit another ordinary transaction touching the same stake account (e.g., partial deactivation), causing `upsert_stake_delegation`/`remove_stake_delegation` to recompute `old_stake` using `stake_v2`, which differs from `stake_v1` actually stored.
5. `sub_delegated_stake` computes `current_stake.checked_sub(old_stake_v2)`; if `old_stake_v2 > current_stake`, `.expect("subtraction value exceeds delegated stake")` panics, crashing the bank-processing thread on every validator that executes this transaction. [6](#0-5) [7](#0-6)

### Citations

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

**File:** runtime/src/stake_delegation.rs (L10-23)
```rust
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
