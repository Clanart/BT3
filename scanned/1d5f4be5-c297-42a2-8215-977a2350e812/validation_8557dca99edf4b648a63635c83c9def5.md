## Finding

### Title
Floating-point stake-ratio precision loss can flip TowerBFT safety-threshold decisions (switch threshold / duplicate-confirmation) - (File: `core/src/consensus.rs`)

### Summary
The external report's root cause is a *unit/precision mismatch feeding a critical safety comparison*, which silently corrupts the result of a check that is supposed to gate an irreversible/critical action (liquidation). The closest Agave analog is in the legacy TowerBFT consensus code: several stake-ratio safety checks that gate fork-switch votes and duplicate-confirmation decisions are computed with lossy `f64` division/multiplication of `u64` stake values, instead of exact integer/fraction arithmetic — even though the codebase elsewhere (`agave_votor_messages::fraction::Fraction`) explicitly documents and unit-tests that this exact computation pattern silently produces an incorrect boolean result near a decision boundary.

### Finding Description
`Tower::is_slot_confirmed`, `Tower::is_slot_duplicate_confirmed`, and `Tower::check_vote_stake_threshold` in `core/src/consensus.rs` compute the safety ratio as: [1](#0-0) 
and [2](#0-1) 

Both cast `u64` stake amounts to `f64` and divide, then compare against a fixed threshold (`DUPLICATE_THRESHOLD`, `threshold_size`). `f64` only has a 53-bit mantissa, but validator stake amounts are `u64` lamport quantities that on mainnet-scale networks (hundreds of millions of SOL, i.e. values around `10^17`–`10^20` lamports) routinely exceed `2^53 (~9.007e15)`. Any stake value above that magnitude cannot be represented exactly as `f64`, so the division `fork_stake as f64 / total_stake as f64` is not the exact rational fraction — it is rounded to the nearest representable `f64`, which can push the computed ratio to the wrong side of the threshold.

The codebase itself demonstrates this exact bug class already occurred and was fixed for the Alpenglow/BLS certificate path via the exact-fraction type: [3](#0-2) 
This test explicitly shows a case where the f64 ratio comparison against a 60% threshold is `wrong!` (asserts `<=0.6`) while the exact `Fraction` comparison correctly resolves `>60%`. `Fraction` was introduced specifically because `f64`-based stake threshold comparisons are unsafe, and it is used in `bls-cert-verify/src/cert_verify.rs::verify_stake`: [4](#0-3) 
and in `core/src/consensus.rs`'s own `GENESIS_VOTE_THRESHOLD` comparison at line 546. However, the legacy TowerBFT `check_vote_stake_threshold` (vote-lockout switch safety), `is_slot_confirmed`, and `is_slot_duplicate_confirmed` were **not migrated** to `Fraction` and still use the fragile `f64` division shown above. The same pattern also appears in `core/src/repair/repair_weight.rs::get_popular_pruned_forks`: [5](#0-4) 
and in `core/src/consensus/vote_stake_tracker.rs::add_vote_pubkey`: [6](#0-5) 

Existing guards do not prevent this: there is no bounds check that `total_stake` (or `min_total_stake`) is small enough to be exactly representable in `f64` before the comparison, and no fallback to integer/`Fraction` arithmetic. The same rounding is applied deterministically by every validator running the same code, so it does not cause direct fork disagreement between correctly-updated validators, but it means the "> DUPLICATE_THRESHOLD" / vote-lockout switch-threshold check — the sole enforcement mechanism preventing a validator from switching its vote to a conflicting fork without adequate observed lockout — can silently accept a stake ratio that is, in exact arithmetic, below the required threshold, or reject one that should have passed, exactly analogous to how the reviewed report's precision-mismatched division silently defeated the liquidation-eligibility check.

### Impact Explanation
`check_vote_stake_threshold` is the function guarding whether a vote lockout is deep enough to permit switching votes onto a conflicting fork — it is a core TowerBFT safety invariant meant to prevent validators from voting in ways that could contribute to a safety violation (conflicting/duplicate confirmations). `is_slot_duplicate_confirmed` similarly gates duplicate-confirmation-triggered dump/repair and commitment reporting logic in `replay_stage.rs`. If floating point rounding causes either check to return an incorrect boolean near the exact threshold boundary, a fork-switch decision or duplicate-confirmation signal can be produced (or withheld) when the exact stake math says otherwise — degrading the integrity of the safety mechanism that is supposed to prevent conflicting votes/forks from being accepted, which maps to "false execution/rooting/acceptance" territory.

### Likelihood Explanation
Likelihood is constrained: it requires the true stake ratio to land within roughly one `f64` ULP of the exact threshold value, which is a narrow window in relative terms, but total network/observed stake values are large integers (`u64`) that are already outside exact `f64` representable range on a live mainnet-scale cluster, so rounding is not a rare edge case but a systemic property of every such comparison; it only becomes security-relevant precisely at ratios extremely close to the threshold, which narrows practical exploitability without stake-value grinding (e.g., a validator engineering its own or delegated stake distribution to land the aggregate exactly on the rounding boundary).

### Recommendation
Replace the `f64`-based stake ratio comparisons in `core/src/consensus.rs` (`is_slot_confirmed`, `is_slot_duplicate_confirmed`, `check_vote_stake_threshold`), `core/src/consensus/vote_stake_tracker.rs::add_vote_pubkey`, and `core/src/repair/repair_weight.rs::get_popular_pruned_forks` with exact integer/`Fraction`-based comparisons (as already used in `bls-cert-verify/src/cert_verify.rs` and for `GENESIS_VOTE_THRESHOLD`), i.e. compare `numerator * threshold_denominator` against `denominator * threshold_numerator` in `u128`, avoiding any `f64` conversion of stake quantities.

### Proof of Concept
Conceptual PoC (mirrors `votor-messages/src/fraction.rs::test_f64_precision_loss`):
```rust
// total_stake, fork_stake chosen so the true ratio is > DUPLICATE_THRESHOLD (e.g. > 2/3)
// but f64 division rounds it to appear <= threshold (or vice versa).
let total_stake: u64 = 100_000_000_000_000_000; // beyond 2^53 exact-int range for f64
let fork_stake: u64 = 66_666_666_666_666_667;   // true ratio > 2/3
let f64_ratio = fork_stake as f64 / total_stake as f64;
assert!(f64_ratio <= (2.0/3.0)); // demonstrates the wrong boolean outcome,
// exactly the class of error shown in votor-messages/src/fraction.rs's
// test_f64_precision_loss, but unfixed in core/src/consensus.rs's
// is_slot_duplicate_confirmed / check_vote_stake_threshold.
```
This is not a full runtime exploit trace (I could not run the validator to confirm an end-to-end consensus-halt scenario within this session), but it demonstrates the exact numerical mechanism, using the same test pattern the codebase itself already uses to document the bug class, applied to the still-vulnerable call sites in `core/src/consensus.rs`.

### Citations

**File:** core/src/consensus.rs (L584-607)
```rust
    #[cfg(test)]
    fn is_slot_confirmed(
        &self,
        slot: Slot,
        voted_stakes: &VotedStakes,
        total_stake: Stake,
    ) -> bool {
        voted_stakes
            .get(&slot)
            .map(|stake| (*stake as f64 / total_stake as f64) > self.threshold_size)
            .unwrap_or(false)
    }

    pub(crate) fn is_slot_duplicate_confirmed(
        &self,
        slot: Slot,
        voted_stakes: &VotedStakes,
        total_stake: Stake,
    ) -> bool {
        voted_stakes
            .get(&slot)
            .map(|stake| (*stake as f64 / total_stake as f64) > DUPLICATE_THRESHOLD)
            .unwrap_or(false)
    }
```

**File:** core/src/consensus.rs (L1333-1369)
```rust
    fn check_vote_stake_threshold<'a>(
        threshold_vote: Option<&Lockout>,
        tower_before_applying_vote: impl Iterator<Item = &'a Lockout>,
        threshold_depth: usize,
        threshold_size: f64,
        slot: Slot,
        voted_stakes: &VotedStakes,
        total_stake: u64,
    ) -> ThresholdDecision {
        let Some(threshold_vote) = threshold_vote else {
            // Tower isn't that deep.
            return ThresholdDecision::PassedThreshold;
        };
        let Some(fork_stake) = voted_stakes.get(&threshold_vote.slot()) else {
            // We haven't seen any votes on this fork yet, so no stake
            return ThresholdDecision::FailedThreshold(threshold_depth as u64, 0);
        };

        let lockout = *fork_stake as f64 / total_stake as f64;
        trace!(
            "fork_stake slot: {}, threshold_vote slot: {}, lockout: {} fork_stake: {} \
             total_stake: {}",
            slot,
            threshold_vote.slot(),
            lockout,
            fork_stake,
            total_stake
        );
        if Self::optimistically_bypass_vote_stake_threshold_check(
            tower_before_applying_vote,
            threshold_vote,
        ) || lockout > threshold_size
        {
            return ThresholdDecision::PassedThreshold;
        }
        ThresholdDecision::FailedThreshold(threshold_depth as u64, *fork_stake)
    }
```

**File:** votor-messages/src/fraction.rs (L76-84)
```rust
    #[test]
    fn test_f64_precision_loss() {
        let total_stake = NonZeroU64::new(100_000_000_000_000_000).unwrap();
        let stake = 60_000_000_000_000_001u64; // 60% + 1

        let f64_ratio = stake as f64 / total_stake.get() as f64;
        assert!(f64_ratio <= 0.6); // wrong!
        assert!(Fraction::new(stake, total_stake) > Fraction::from_percentage(60));
    }
```

**File:** bls-cert-verify/src/cert_verify.rs (L119-135)
```rust
fn verify_stake(
    cert: &UnverifiedCertificate,
    aggregate_stake: u64,
    total_stake: NonZero<u64>,
) -> Result<(), Error> {
    let required_fraction = cert.cert_type.threshold();
    let cert_fraction = Fraction::new(aggregate_stake, total_stake);
    if cert_fraction >= required_fraction {
        Ok(())
    } else {
        Err(Error::NotEnoughStake {
            aggregate_stake,
            cert_fraction,
            required_fraction,
        })
    }
}
```

**File:** core/src/repair/repair_weight.rs (L835-846)
```rust
            let min_total_stake = pruned_tree
                .slots_iter()
                .map(|slot| {
                    epoch_stakes
                        .get(&epoch_schedule.get_epoch(slot))
                        .expect("Pruned tree cannot contain slots more than an epoch behind")
                        .total_stake()
                })
                .min()
                .expect("Pruned tree cannot be empty");
            let duplicate_confirmed_threshold =
                ((min_total_stake as f64) * DUPLICATE_THRESHOLD) as u64;
```

**File:** core/src/consensus/vote_stake_tracker.rs (L27-33)
```rust
            let reached_threshold_results: Vec<bool> = thresholds_to_check
                .iter()
                .map(|threshold| {
                    let threshold_stake = (total_stake as f64 * threshold) as u64;
                    old_stake <= threshold_stake && threshold_stake < new_stake
                })
                .collect();
```
