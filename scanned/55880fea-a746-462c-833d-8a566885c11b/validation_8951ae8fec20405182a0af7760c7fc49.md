## Analysis

The report's core pattern is: a **cached running total** (`totalDeposited`) that is expected to always be ≥ any subsequent subtraction, but is derived from external, independently-mutable state (asset price / actual pool balance). When that assumption breaks, a `checked_sub`-style subtraction underflows, and the code that relies on `expect`/`unwrap`-style enforcement either reverts or panics — leaving remaining participants unable to exit ("last ones lose their funds").

The closest structural analog in Agave is `Stakes::sub_delegated_stake` in `runtime/src/stakes.rs`, which maintains `delegated_stakes: DelegatedStakes` (`ImblHashMap<Pubkey, u64>`) as a running cache of "current effective stake delegated to each vote account". This cache is incrementally updated via `add_delegated_stake`/`sub_delegated_stake` whenever a stake account is upserted or removed, rather than being recomputed from scratch each time: [1](#0-0) 

The subtraction amount (`removed_stake` / `old_stake`) is recomputed via `delegation_effective_stake`, which dispatches to either `stake_v2` (fixed-point math) or the legacy `stake()` warmup/cooldown formula depending on the `use_fixed_point_stake_math` flag: [2](#0-1) 

This value is computed independently at insertion time and again at removal/update time in `upsert_stake_delegation` and `remove_stake_delegation`: [3](#0-2) 

### Title
Cached `delegated_stakes` total can desynchronize from actual stake, panicking `sub_delegated_stake` and halting the validator - (File: `runtime/src/stakes.rs`)

### Summary
`Stakes::delegated_stakes` is an incrementally-maintained aggregate (`add_delegated_stake` / `sub_delegated_stake`), analogous to `totalDeposited` in the report: it tracks a derived value across many independent add/remove operations instead of being recomputed atomically from the ground truth. `sub_delegated_stake` enforces the invariant "the amount being removed never exceeds what was previously added" with a hard `expect()`/`checked_sub().expect()`, exactly like the report's un-guarded `totalDeposited -= amount`.

### Finding Description
`upsert_stake_delegation` and `remove_stake_delegation` compute the stake to subtract from `delegated_stakes` by calling `delegation_effective_stake` a second time, using the *current* `self.epoch`, `self.stake_history`, and the *current* `new_rate_activation_epoch` / `use_fixed_point_stake_math` flags — not the values that were in effect when the corresponding `add_delegated_stake` for that same delegation last ran [4](#0-3) . If the effective-stake calculation for a given `(delegation, epoch)` pair is not perfectly monotonic/consistent across the parameter set used at add-time vs. remove-time (e.g. across a mid-flight toggle of `use_fixed_point_stake_math`, which switches between `stake_v2` fixed-point math and the legacy `stake()` warmup/cooldown formula in `stake_delegation.rs`), the recomputed `old_stake`/`removed_stake` used for subtraction can be **larger** than what remains tracked in `delegated_stakes` for that voter, causing:

```rust
*current_stake = current_stake
    .checked_sub(stake)
    .expect("subtraction value exceeds delegated stake");
```
to panic [1](#0-0) .

Unlike `accounts_db`'s capitalization tracking, which uses `checked_sub(...).expect("capitalization cannot underflow")` guarded by careful whole-snapshot recomputation and startup-only invocation [5](#0-4) , this stakes cache is updated on the hot path for every stake-account write across every bank/replay, with no fallback/reconciliation and no `saturating_sub`. There is no guard in `sub_delegated_stake` analogous to a revert-and-continue path — the `expect()` is a hard `panic!`, which crashes the validator process.

### Impact Explanation
A panic in `sub_delegated_stake` is not confined to a single malformed transaction; it happens during normal bank/stakes-cache maintenance (`check_and_store`/`upsert_stake_delegation`/`remove_stake_delegation`), which runs deterministically on every validator processing the same block. If the discrepancy is triggered by state that is replayed identically cluster-wide (e.g. a stake account transition combined with an epoch/feature-flag boundary), every validator that reaches this code path would panic simultaneously — this is a consensus-halt-class failure (mass validator crash), which is within the accepted valid-impact categories (runtime/accounts, non-RPC crash, consensus halt).

### Likelihood Explanation
I was not able to fully verify, from local code alone, a concrete input sequence that produces an actual mismatch between the stake computed at insertion time and at removal time (i.e., I could not prove `delegation.stake_v2()`/`stake()` monotonicity fails under normal epoch/history progression, nor pin down the exact conditions under which `use_fixed_point_stake_math` or `new_rate_activation_epoch` could differ between the two calls for the same delegation). This limits confidence that the underflow is reachable in practice versus being prevented by invariants enforced elsewhere (e.g., feature-gate activation being a fixed, network-wide, one-time switch rather than something that can flip back and forth per-call). This should be treated as a **candidate** finding requiring further tracing of call sites that set `use_fixed_point_stake_math`/`new_rate_activation_epoch` and epoch/stake-history consistency guarantees, not a confirmed exploit.

### Recommendation
- Recompute `delegated_stakes` from the authoritative `stake_delegations` map on any epoch boundary or feature-flag transition rather than relying purely on incremental add/sub deltas, or
- Replace the hard `expect()` in `sub_delegated_stake` with a `saturating_sub` plus a metrics/log path (as accounts-db does with the capitalization comment "capitalization cannot underflow", but paired with a controlled recompute rather than a raw panic on the hot path), and
- Add an explicit invariant test asserting that `delegation_effective_stake` is stable (non-decreasing after the fact) for a fixed delegation and epoch regardless of when `use_fixed_point_stake_math` is toggled.

### Proof of Concept
Not established — a concrete failing sequence of stake-account mutations/epoch transitions that produces `old_stake > current cached delegated_stakes[voter]` was not identified from static reading alone. Confirming this requires tracing all call sites of `check_and_store`/`upsert_stake_delegation` together with how `new_rate_activation_epoch` and `use_fixed_point_stake_math` are threaded through bank epoch-boundary code (`runtime/src/bank.rs`) to determine whether these parameters can differ between two calls referencing the same delegation.

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

**File:** runtime/src/stakes.rs (L582-659)
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

**File:** accounts-db/src/accounts_db.rs (L6108-6111)
```rust
        total_accum.capitalization = total_accum
            .capitalization
            .checked_sub(capitalization_from_duplicates)
            .expect("capitalization cannot underflow");
```
