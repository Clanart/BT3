## Title
Zero `total_epoch_stake` in `ValidatorStakeInfo` causes fork-propagation threshold to be trivially satisfied - ([File: core/src/consensus/progress_map.rs])

## Summary
The external report's root cause is a value that defaults/starts at `0` before proper initialization, and that `0` is then trusted as a legitimate input to a downstream check, causing the check to behave as if the precondition were already satisfied. The same pattern exists in Agave's fork/cluster propagation logic: `ValidatorStakeInfo::total_epoch_stake` defaults to `0` and is used as the denominator of the stake-propagation-threshold ratio in `PropagatedStats`. When `total_epoch_stake == 0` (e.g., epoch stakes for a slot/epoch not yet populated, or a stray/degenerate `ForkProgress` entry), the propagation check is *vacuously* satisfied, rather than blocked or deferred until real stake data is available.

## Finding Description
`PropagatedStats` tracks the stake of validators that have "propagated" (voted on/observed) a slot, and compares accumulated stake against `total_epoch_stake` to decide `is_propagated`. This is analogous to a price oracle whose "price" starts at 0 and is treated as valid until a real value is submitted: here, `total_epoch_stake` starts as `0` in `ValidatorStakeInfo::default()`/uninitialized cases, and the ratio check `stake / total_epoch_stake > threshold` degenerates when `total_epoch_stake == 0`.

The project's own test explicitly documents this behavior as intentional: [1](#0-0) 
which states: "If the stake is zero, then threshold is always achieved" — i.e., a `ForkProgress`/`ValidatorStakeInfo` constructed with `total_epoch_stake: 0` is considered `is_propagated == true` without any actual stake having voted for/observed the slot.

This mirrors the broken invariant in the stETH oracle report: the guarded resource (price / stake denominator) has an unsafe zero default, and no `require`-style guard prevents the value from being treated as meaningful before it is legitimately populated (e.g. via `submitState` in the oracle case, or via `update_epoch_stakes`/`bank.epoch_total_stake` returning the fully-populated epoch stake in Agave's case).

The relevant bank API that could return this zero state before epoch stake computation completes is: [2](#0-1) 
`epoch_total_stake` returns `Option<u64>` for a queried epoch, and if the epoch stake computation has not yet been performed/cached for a given epoch (e.g., early epoch transition or forks that reference the not-yet-computed leader-schedule epoch), a `0` or missing value can flow into structures like `ValidatorStakeInfo` before real stake is populated, similar in nature to the `update_epoch_stakes` cache-population routine: [3](#0-2) 

## Impact Explanation
If `is_propagated` (or an analogous stake/threshold ratio) is trivially true because the total-stake denominator was zero/uninitialized at evaluation time, a validator could treat a slot/fork as "propagated" or count consensus/gossip conditions as satisfied without real corroborating stake weight. Downstream logic (leader confirmation gating, vote refresh, fork-choice propagation gating) that relies on `is_propagated` to gate voting/leader behavior could act on an under-verified assumption, degrading the safety margin of consensus decisions built on top of it. This falls into the "false execution/acceptance" bucket relevant to Agave's runtime/consensus code.

## Likelihood Explanation
This is a low-likelihood but structurally real edge case: it requires `total_epoch_stake` to be `0` at the exact time the check executes (e.g., a race during epoch-boundary bookkeeping, a not-yet-populated epoch, or a degenerate/default `ValidatorStakeInfo`). The code appears defended in most call sites (e.g. `epoch_total_stake` returns `Option`, and other stake-threshold call sites like `check_vote_stake_threshold` and `is_slot_duplicate_confirmed` guard on `voted_stakes.get(...)` being present, returning `FailedThreshold`/`false` rather than a vacuous pass). The `is_propagated`/zero-total-stake case, however, explicitly special-cases and *accepts* the zero-stake condition as "always achieved," which is the pattern that most directly parallels the reported oracle bug (uninitialized denominator/value silently treated as valid).

## Recommendation
Add an explicit guard so that a `total_epoch_stake` (or any equivalent totalStake/price denominator) of `0` is treated as "not yet initialized/unknown" rather than "trivially satisfied":
```rust
// core/src/consensus/progress_map.rs
if total_epoch_stake == 0 {
    // Do not treat as vacuously propagated; defer until real stake data exists.
    return false; // or Err(...) depending on call convention
}
```
More generally, ensure any code path constructing `ValidatorStakeInfo`/`ForkProgress` verifies epoch stakes have actually been computed for the referenced epoch (i.e., `bank.epoch_total_stake(epoch)` returns a real, non-zero value) before using it in threshold arithmetic, mirroring the recommended `require(value > 0, "NOT_INITIALIZED")` pattern from the external report.

## Proof of Concept
The existing unit test in the codebase already demonstrates the exact vacuous-pass behavior (not a hypothetical): [4](#0-3) 
Constructing `ForkProgress::new(.., Some(ValidatorStakeInfo{ total_epoch_stake: 0, ..Default::default() }), ..)` yields `progress.propagated_stats.is_propagated == true`, with zero actual stake having voted/observed the slot — the analog of the oracle's stETH price defaulting to `0` and being accepted as legitimate before `submitState` initializes it.

**Caveat:** I was unable to fully trace every call site that could realistically construct a `ForkProgress`/`ValidatorStakeInfo` with `total_epoch_stake == 0` in production (vs. only in tests/dev-context), due to running out of tool iterations before reading the full `progress_map.rs` file and its callers (e.g. `replay_stage.rs`). This should be verified in a follow-up session to confirm whether this zero-stake path is reachable outside of test/dev-only code, which would determine whether this is an actual exploitable vulnerability or a benign test-only edge case.

### Citations

**File:** core/src/consensus/progress_map.rs (L560-579)
```rust
    #[test]
    fn test_is_propagated_status_on_construction() {
        // If the given ValidatorStakeInfo == None, then this is not
        // a leader slot and is_propagated == false
        let progress = ForkProgress::new(Hash::default(), Some(9), None, 0, 0, None);
        assert!(!progress.propagated_stats.is_propagated);

        // If the stake is zero, then threshold is always achieved
        let progress = ForkProgress::new(
            Hash::default(),
            Some(9),
            Some(ValidatorStakeInfo {
                total_epoch_stake: 0,
                ..ValidatorStakeInfo::default()
            }),
            0,
            0,
            None,
        );
        assert!(progress.propagated_stats.is_propagated);
```

**File:** runtime/src/bank.rs (L2594-2624)
```rust
    fn update_epoch_stakes(
        &mut self,
        leader_schedule_epoch: Epoch,
        prefiltered_distribution_vote_accounts: Option<VoteAccounts>,
    ) {
        // update epoch_stakes cache
        //  if my parent didn't populate for this staker's epoch, we've
        //  crossed a boundary
        if !self.epoch_stakes.contains_key(&leader_schedule_epoch) {
            self.epoch_stakes.retain(|&epoch, _| {
                // Note the greater-than-or-equal (and the `- 1`) is needed here
                // to ensure we retain the oldest epoch, if that epoch is 0.
                epoch >= leader_schedule_epoch.saturating_sub(MAX_LEADER_SCHEDULE_STAKES - 1)
            });
            // At the epoch boundary, `compute_new_epoch_caches_and_rewards`
            // has already produced the VAT-filtered vote-account snapshot;
            // reuse it here instead of re-cloning and re-filtering the
            // `stakes_cache`. Other callers (same-epoch refresh, warps)
            // fall back to `get_top_epoch_stakes`.
            let stakes = match prefiltered_distribution_vote_accounts {
                Some(prefiltered) => Stakes::new(prefiltered, self.epoch()),
                None => self.get_top_epoch_stakes(),
            };
            let stakes = SerdeStakesToStakeFormat::from(stakes);
            let new_epoch_stakes = VersionedEpochStakes::new(stakes, leader_schedule_epoch);
            info!(
                "new epoch stakes, epoch: {}, total_stake: {}",
                leader_schedule_epoch,
                new_epoch_stakes.total_stake(),
            );

```

**File:** runtime/src/bank.rs (L5858-5863)
```rust
    /// Returns the total stake in Lamports for the given epoch.
    pub fn epoch_total_stake(&self, epoch: Epoch) -> Option<u64> {
        self.epoch_stakes
            .get(&epoch)
            .map(|epoch_stakes| epoch_stakes.total_stake())
    }
```
