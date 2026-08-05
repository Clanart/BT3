### Title
`Stakes::sub_delegated_stake` / `VoteAccounts::sub_stake` panic on stale cached-stake underflow — validator crash (Denial of Service) - ([File: runtime/src/stakes.rs], [File: vote/src/vote_account.rs])

### Summary
The reported bug is a class where a periodically-recomputed delta (rewards/slashing amount) is subtracted from a previously cached running total, and the code assumes the delta can never exceed the stale cached value — using an unchecked/asserting subtraction instead of clamping or recomputing against the live value. In `agave`, the `Stakes` cache maintains an aggregate `delegated_stakes: ImblHashMap<Pubkey, u64>` per validator that is updated incrementally via `sub_delegated_stake`/`add_delegated_stake`, and `VoteAccounts` maintains an analogous aggregate `stake` field updated via `sub_stake`. Both use `checked_sub(..).expect(...)`, which will panic the validator process if the amount being subtracted (computed from possibly-different `stake_history`/epoch context) ever exceeds the currently cached aggregate.

### Finding Description
`Stakes::sub_delegated_stake` in `runtime/src/stakes.rs`:
```
fn sub_delegated_stake(&mut self, voter_pubkey: &Pubkey, stake: u64) {
    ...
    let current_stake = self.delegated_stakes.get_mut(voter_pubkey)
        .expect("subtraction from missing delegated stake");
    *current_stake = current_stake.checked_sub(stake)
        .expect("subtraction value exceeds delegated stake");
    ...
}
``` [1](#0-0) 

This is invoked from `remove_stake_delegation`, which computes the stake amount to subtract via `delegation_effective_stake(removed_delegation, self.epoch, &self.stake_history, new_rate_activation_epoch, use_fixed_point_stake_math)` — i.e., the amount being subtracted is *recomputed on the fly* from the stored `Delegation` and the cache's current `epoch`/`stake_history`/`new_rate_activation_epoch`/math-mode parameters, rather than reading back the exact value that was previously added to `delegated_stakes`:
```
fn remove_stake_delegation(&mut self, stake_pubkey: &Pubkey, new_rate_activation_epoch: Option<Epoch>, use_fixed_point_stake_math: bool) {
    if let Some(stake_account) = self.stake_delegations.remove(stake_pubkey) {
        let removed_delegation = stake_account.delegation();
        let removed_stake = delegation_effective_stake(removed_delegation, self.epoch, &self.stake_history, new_rate_activation_epoch, use_fixed_point_stake_math);
        self.sub_delegated_stake(&removed_delegation.voter_pubkey, removed_stake);
        ...
    }
}
``` [2](#0-1) 

This mirrors the report's flaw exactly: the subtracted amount (`removed_stake`, analogous to `newSlashAmount`) is derived from a value that can drift relative to the cached aggregate it is applied against (`delegated_stakes[voter]`, analogous to `val.balance`), because `delegation_effective_stake` depends on parameters (`new_rate_activation_epoch`, `use_fixed_point_stake_math`, `stake_history`) that can change between when a delegation's stake was added to the aggregate and when it is removed (e.g., feature-gate activation flips mid-epoch, or activation/deactivation warm-up math changes the effective value calculated for the same underlying `Delegation`). If `removed_stake` ends up larger than the value that was actually accumulated in `delegated_stakes` for that validator, `checked_sub` returns `None` and `.expect(...)` panics.

The identical pattern exists in `solana_vote::vote_account::VoteAccounts::sub_stake`:
```
pub fn sub_stake(&mut self, pubkey: &Pubkey, delta: u64) {
    ...
    *stake = stake.checked_sub(delta).expect("subtraction value exceeds account's stake");
    ...
}
``` [3](#0-2) 

Both `sub_delegated_stake` and `sub_stake` are reached from routine, unprivileged, non-malicious paths — ordinary stake account lifecycle events processed by every validator while replaying blocks (`check_and_store` → `remove_stake_delegation` when a stake account's lamports hit zero, or delegation/redelegation/withdrawal transactions that shrink a `Delegation`), not any admin/trusted-plugin action. There is no fallback path that recomputes the aggregate from scratch or clamps the subtraction to the stale cached value; the code relies entirely on the invariant "recomputed removed_stake ≤ cached current_stake" always holding, exactly the fragile invariant flagged in the source report.

### Impact Explanation
A panic inside `sub_delegated_stake`/`sub_stake` occurs deep in bank/stakes-cache update logic that runs during normal block replay on every validator that processes the same stake-account state transition. Because all validators execute the same deterministic state transitions, a violation of this invariant is not merely a single-node crash — it would be hit by every validator replaying the same block, i.e., a network-wide crash/consensus halt rather than a local anomaly. This matches the "consensus halt" / "non-RPC remote exhaustion/crash" impact bucket: unprivileged/normal state transitions leading to a process panic across the fleet.

### Likelihood Explanation
Likelihood is Medium: triggering requires the effective-stake recomputation at removal time (which depends on mutable calculation-mode flags/`new_rate_activation_epoch`/warmup-cooldown math and `stake_history`) to diverge from the value that was originally accumulated for that delegation, which is most likely around epoch boundaries or feature-activation transitions that change `delegation_effective_stake`'s output for warming-up/cooling-down delegations. This is analogous to the H-04 scenario ("especially likely in the first few days after activation" / during interval transitions) — I was not able to fully trace every call site of `remove_stake_delegation`/`sub_stake` and confirm whether existing invariants elsewhere in `stakes.rs` (e.g., always refreshing `delegated_stakes` via `refresh_delegated_stakes` at epoch boundaries before any incremental subtraction) close this gap; that would require deeper tracing of `activate_epoch`/`upsert_vote_account`/`check_and_store` interleaving than the available context allowed.

### Recommendation
Avoid `expect()`-based panics on this aggregate subtraction. Either (a) clamp with `saturating_sub` and log/metric the discrepancy instead of panicking, or (b) always refresh `delegated_stakes`/`VoteAccounts::stake` from scratch immediately before applying deltas so the value being subtracted is guaranteed consistent with the accumulator, removing the implicit "stale cache tracks live computation" invariant entirely.

### Proof of Concept
Could not be constructed from local code alone — a concrete panic requires driving `delegation_effective_stake`'s inputs (`new_rate_activation_epoch`, `use_fixed_point_stake_math`, `stake_history` contents, and the stored `Delegation`'s warm-up/cool-down state) to a combination where the recomputed value at removal time exceeds what was actually added to `delegated_stakes` at insertion time. Confirming an exact reachable sequence (e.g., specific epoch/feature-activation timing) needs tracing `refresh_delegated_stakes`, `activate_epoch`, and all call sites of `remove_stake_delegation` across epoch boundaries, which was not completed within the available exploration.

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
