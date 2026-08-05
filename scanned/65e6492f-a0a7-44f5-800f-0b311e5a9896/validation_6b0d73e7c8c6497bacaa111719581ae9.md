## Title
Floating-point stake-ratio comparison in tower vote threshold check can accept votes below the true `VOTE_THRESHOLD_SIZE` - (File: `core/src/consensus.rs`)

## Summary
The external report's bug class is "an arithmetic expression that silently rounds/loses precision and produces a materially wrong ratio, defeating a security-relevant comparison." The direct Uniswap fix was to correct integer bit-shift/divide ordering. Agave's own `Fraction` type documents an equivalent class of bug for stake-ratio math: comparing stake ratios with `f64` division can flip the result of a threshold comparison near the boundary, as demonstrated by the codebase's own test `test_f64_precision_loss` in `votor-messages/src/fraction.rs`. That test proves `stake as f64 / total_stake as f64 <= 0.6` even though `stake` is `60%+1`. Despite this being known and fixed for the Alpenglow/BLS certificate path by introducing exact-integer `Fraction` comparisons (`bls-cert-verify/src/cert_verify.rs`, `votor/src/consensus_pool/slot_stake_counters.rs`, `votor/src/aggregate_accumulator.rs`), the legacy Tower BFT vote-threshold check in `core/src/consensus.rs::check_vote_stake_threshold` still performs the exact same unsafe `f64` division/comparison pattern.

## Finding Description
`Tower::check_vote_stake_threshold` computes: [1](#0-0) 

```rust
let lockout = *fork_stake as f64 / total_stake as f64;
...
if Self::optimistically_bypass_vote_stake_threshold_check(...)
    || lockout > threshold_size
{
    return ThresholdDecision::PassedThreshold;
}
ThresholdDecision::FailedThreshold(threshold_depth as u64, *fork_stake)
```

`fork_stake` and `total_stake` are `u64` lamport-denominated stake weights that can be arbitrarily large (up to the total token supply, well beyond `f64`'s 53-bit mantissa of exact integer representation). Converting both to `f64` and dividing introduces rounding error before the comparison against `threshold_size` (`VOTE_THRESHOLD_SIZE`, a `f64` constant equal to 2/3, imported from `solana_runtime::commitment::VOTE_THRESHOLD_SIZE`). This is the exact bug class from the external report: an arithmetic shortcut ("compute this ratio quickly") silently corrupts the value used in a security-relevant comparison, because floating point division does not preserve exact ratios for large integer numerator/denominator pairs.

Agave's own codebase already acknowledges this failure mode explicitly. The `Fraction` type's doc comment and unit test demonstrate that `stake as f64 / total_stake as f64` can round the *wrong direction* relative to a percentage threshold: [2](#0-1) 

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

The newer consensus paths (BLS certificate verification, votor safe-to-notar/safe-to-skip checks) were rewritten to use `Fraction`'s exact `u128` cross-multiplication comparison to avoid exactly this class of bug: [3](#0-2) [4](#0-3) 

but `check_vote_stake_threshold` in the legacy Tower path (`core/src/consensus.rs`) was never migrated and still uses raw `f64` division: [5](#0-4) 

No guard exists to bound the magnitude of `fork_stake`/`total_stake` before the cast, and no compensating integer cross-multiplication is performed as a fallback — the `f64` comparison is the sole gate for `ThresholdDecision::PassedThreshold` vs `FailedThreshold`.

## Impact Explanation
`check_vote_stake_threshold` determines whether a validator considers a fork's historical vote lockout deep enough to safely vote/lockout further on top of it (Tower BFT threshold check, used in `Tower::check_vote_stake_threshold`/`make_check_switch_threshold_decision` machinery referenced throughout `core/src/consensus.rs`). This directly gates the validator's own voting/locking behavior, i.e., it affects fork-choice/voting correctness (false acceptance of a threshold that hasn't actually been met, or false rejection of one that has been met), which falls under "false execution/rooting/acceptance" for the runtime/consensus vote path. Because the rounding error is data-dependent on the exact stake distribution at the moment of the check (near-boundary ratios around `VOTE_THRESHOLD_SIZE = 2/3`), it is plausible for the comparison to flip near the boundary purely due to floating-point rounding rather than actual stake support, differing from what exact-rational arithmetic would decide.

## Likelihood Explanation
The likelihood of exact boundary-crossing conditions naturally recurring is comparatively low relative to a "High/High" C-01 finding (real cluster stake distributions rarely land at the exact `f64` precision edge of `2/3`), but the code path is executed on every vote/threshold evaluation in the hot consensus loop with attacker-uninfluenced but adversarial-stake-distribution-dependent inputs (total network stake, which can be shaped over epochs by legitimate stake movement, not by a malicious peer performing an attack in a single message). This is a a real, in-place latent correctness bug in the current codebase, rather than a hypothetical one, evidenced by the project's own acknowledgment and fix of the identical bug class elsewhere in the same repository (`Fraction`).

## Recommendation
Replace the `f64` ratio computation and comparison in `check_vote_stake_threshold` with the same exact-integer `Fraction` comparison already used elsewhere in the codebase (`agave_votor_messages::fraction::Fraction`), e.g.:

```rust
let lockout = Fraction::new(*fork_stake, NonZeroU64::new(total_stake).unwrap());
if ... || lockout > threshold_fraction { ... }
```

where `threshold_fraction` is expressed as an exact `Fraction` (e.g. `Fraction::from_percentage(67)` or an equivalent numerator/denominator pair) instead of the `f64` constant `VOTE_THRESHOLD_SIZE`.

## Proof of Concept
Using the same numeric construction as the repository's own `test_f64_precision_loss`:
1. Let `total_stake = 100_000_000_000_000_000` lamports and `fork_stake = 66_666_666_666_666_667` (just over 2/3 of `total_stake`, i.e. it should exceed `VOTE_THRESHOLD_SIZE`).
2. Compute `lockout = fork_stake as f64 / total_stake as f64` as done in `core/src/consensus.rs` line 1351.
3. Due to `f64` rounding of both the numerator/denominator conversion and the division, `lockout` can round down to exactly `VOTE_THRESHOLD_SIZE`'s `f64` representation or below it, causing `lockout > threshold_size` to evaluate `false` even though the true rational value `fork_stake/total_stake` exceeds 2/3.
4. This causes `ThresholdDecision::FailedThreshold` to be returned instead of `PassedThreshold`, incorrectly blocking a validator from voting further on a fork it has, in exact stake terms, sufficiently locked out — a false-rejection instance of the same rounding bug class the report describes as "the variable holding the wrong value," here manifesting as a wrong boolean/threshold decision rather than a wrong numeric field.

Note: I was unable to fully trace every call-site of `check_vote_stake_threshold` (e.g., its exact position inside `check_vote_stake_thresholds` across the tower depth chain) within the available indexed context; a full reproduction against live stake distributions and confirmation of an actually reachable boundary crossing would require running the code, which is outside what the current index/tools allow me to execute.

### Citations

**File:** core/src/consensus.rs (L1332-1368)
```rust
    /// Checks a single vote threshold for `slot`
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
```

**File:** votor-messages/src/fraction.rs (L47-58)
```rust
impl Ord for Fraction {
    fn cmp(&self, other: &Self) -> std::cmp::Ordering {
        // Cross-multiply to compare
        let lhs = (self.numerator as u128)
            .checked_mul(other.denominator.get() as u128)
            .unwrap();
        let rhs = (other.numerator as u128)
            .checked_mul(self.denominator.get() as u128)
            .unwrap();
        lhs.cmp(&rhs)
    }
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

**File:** votor/src/consensus_pool/slot_stake_counters.rs (L129-137)
```rust
        let notarized_ratio = Fraction::new(*stake, self.total_stake);
        let notarized_plus_skip_ratio = Fraction::new(
            self.skip_total.checked_add(*stake).unwrap(),
            self.total_stake,
        );
        notarized_ratio >= SAFE_TO_NOTAR_MIN_NOTARIZE_ONLY
            // Check if the block fits condition (ii) 20% notarized, and 60% notarized or skip
            || (notarized_ratio >= SAFE_TO_NOTAR_MIN_NOTARIZE_FOR_NOTARIZE_OR_SKIP
                && notarized_plus_skip_ratio >= SAFE_TO_NOTAR_MIN_NOTARIZE_AND_SKIP)
```
