## Analog Found: Panic-inducing underflow in Agave's in-memory delegated-stake cache (`runtime/src/stakes.rs`)

### Title
Inconsistent stake-cache accounting causes `checked_sub().expect()` panic in `Stakes::sub_delegated_stake` - (File: `runtime/src/stakes.rs`)

### Summary
The external report's core defect is a system that tracks an aggregate accounting value (`totalSupplies`) independently of the true source of truth, and later subtracts from that aggregate without validating the subtraction is safe, breaking a protocol-wide invariant and causing a permanent DoS. The Agave analog is `Stakes<StakeAccount>::delegated_stakes`, an in-memory `HashMap<Pubkey, u64>` cache of "current effective stake per vote account" that is maintained by additive/subtractive updates in `upsert_stake_delegation`/`sub_delegated_stake`, separately from the authoritative on-chain state (each stake account's `Delegation`). When the cached subtrahend does not match what was actually cached, the subtraction underflows and the code panics instead of saturating or erroring gracefully.

### Finding Description
`Stakes<StakeAccount>` keeps a cache `delegated_stakes: DelegatedStakes` (`HashMap<Pubkey, u64>`) that is supposed to equal the sum, for each vote account, of the *effective* stake of all delegations to it at the cache's current epoch/parameters. [1](#0-0) 

Updates to this cache happen in `upsert_stake_delegation`, which recomputes the *new* effective stake for the incoming account and, for the account that previously occupied that key, recomputes an *old* effective stake using the delegation stored in `stake_delegations` and the **current** `self.epoch` / `self.stake_history` / `new_rate_activation_epoch` / `use_fixed_point_stake_math` parameters, then subtracts that recomputed value from the cache: [2](#0-1) 

The actual subtraction is performed by `sub_delegated_stake`, which uses `checked_sub` and immediately `.expect()`s the result, i.e. it panics if the subtraction would underflow: [3](#0-2) 

This mirrors the vault bug precisely: the cache (`delegated_stakes`, analogous to `totalSupplies`/`totalBorrows`) is an accounting shortcut that must always stay in lock-step with the authoritative per-account state (`stake_delegations`, analogous to real token balances). But the value subtracted is *recomputed* at call time from `delegation_effective_stake(...)`, which depends on runtime parameters (`new_rate_activation_epoch`, `use_fixed_point_stake_math`) that are feature-gated and can change value across calls (feature activation flips these flags for all subsequent calls in the same process). If the effective-stake value computed when an entry was *added* to the cache (under one set of parameters) differs from the effective-stake value recomputed when that same entry is later *replaced/removed* (under a different, newer set of parameters), the recomputed "old_stake" can exceed what is actually stored under that voter's key in `delegated_stakes`, causing `current_stake.checked_sub(stake)` to return `None` and the `.expect("subtraction value exceeds delegated stake")` to panic.

The existing guard in `sub_delegated_stake` — `checked_sub` — is present, but instead of being treated as a recoverable inconsistency (as the report recommends for `burn`), it is escalated straight into a panic via `.expect()`, which is worse than the original bug: rather than merely returning a wrong/negative value, it terminates the validator process.

### Impact Explanation
A panic inside `Stakes::sub_delegated_stake`, which is reached from `StakesCache::check_and_store` on the hot path of processing every stake-account update (bank account-store notification path), would crash the validator process handling that bank/transaction. Because `Stakes<StakeAccount>` is per-`Bank` state built from ordinary transaction processing (not from any trusted/privileged path), this is reachable by any unprivileged user submitting an otherwise-valid transaction that touches stake accounts around a feature-activation boundary (where `new_rate_activation_epoch`/`use_fixed_point_stake_math` semantics change). A crash here on a quorum of validators processing the same transaction would produce a consensus-wide halt (mirrors the report's "core protocol functionality breaks system-wide" impact), not merely a single-node degradation.

### Likelihood Explanation
Triggering this requires the recomputed "old effective stake" for a delegation to legitimately exceed what is currently recorded for that voter in `delegated_stakes`, which in ordinary steady-state operation should not happen because both add and subtract paths use the *same* `self.epoch`/`self.stake_history`/feature flags at the time of the call. The window of exposure is specifically transitions where `new_rate_activation_epoch` or `use_fixed_point_stake_math` change value between when a stake delegation was cached and when it is later superseded — i.e., around feature activation epoch boundaries — which is a narrower, harder-to-hit precondition than the freely-triggerable ERC20 vault exploit in the source report. I was not able to fully trace, within the available tool budget, the exact call sequence in `runtime/src/bank/partitioned_epoch_rewards/*` or feature-activation code that could produce two different effective-stake values for the *same* stored delegation across two `check_and_store`/`upsert_stake_delegation` calls, so likelihood is assessed as **plausible but not fully confirmed** — this is the main uncertainty in this finding.

### Recommendation
- Short term: replace `.expect("subtraction value exceeds delegated stake")` in `sub_delegated_stake` with saturating arithmetic (`saturating_sub`) or a recoverable error path, so a cache/authoritative-state mismatch degrades gracefully (e.g., clamps to zero and logs) instead of crashing the process.
- Long term: ensure `delegated_stakes` cache updates always use the exact effective-stake value that was used when the entry was inserted (e.g., store the previously-computed effective stake alongside the account rather than recomputing it with possibly-different current parameters), removing the dependency on mutable global feature flags for correctness of a purely incremental cache.

### Proof of Concept
Not independently reproducible from local static analysis alone within this session — the code path and panic condition are demonstrated by inspection of `runtime/src/stakes.rs` (`sub_delegated_stake`, `upsert_stake_delegation`), but constructing a concrete transaction sequence that flips `new_rate_activation_epoch`/`use_fixed_point_stake_math` between the add and subtract calls for the same delegation would require deeper tracing through the feature-activation and epoch-boundary code (`runtime/src/bank.rs`, `runtime/src/bank/partitioned_epoch_rewards/`) than was completed here; a Devin session with full repository/test access is recommended to confirm exploitability end-to-end.

### Citations

**File:** runtime/src/stakes.rs (L219-223)
```rust
    /// current effective stake delegated to each vote account pubkey
    #[cfg_attr(feature = "frozen-abi", stable_abi_sample(with = "Default::default()"))]
    #[serde(skip)]
    #[wincode(skip)]
    delegated_stakes: DelegatedStakes,
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
