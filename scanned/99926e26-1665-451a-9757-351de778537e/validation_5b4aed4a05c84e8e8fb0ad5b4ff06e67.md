Based on my research, the strongest local analog to the reported "decimal unit discrepancy" bug class is a **floating-point precision-loss bug in Agave's legacy Tower-BFT stake-threshold arithmetic**, which the project's own codebase demonstrates was already recognized as a real defect in a parallel subsystem (`votor-messages`) but was never back-ported to the still-active legacy consensus path.

### Title
Floating-point precision loss in Tower-BFT stake-ratio threshold comparisons can flip vote/switch decisions - (File: core/src/consensus.rs)

### Summary
Agave's legacy (pre-Alpenglow) Tower-BFT consensus logic computes stake-ratio thresholds by casting `u64` lamport-stake values to `f64` and dividing, then comparing against a hard-coded `f64` threshold constant. The `f64` type only has 52 bits of mantissa (~2^53 ≈ 9.007×10^15 exact integers), but real cluster stake values (e.g., total active stake in lamports, which is SOL × 10^9) regularly exceed that magnitude, so the division `stake as f64 / total_stake as f64` silently rounds the true ratio. The project's own `votor-messages` crate already contains a `Fraction` type built specifically to avoid this defect, with a unit test proving the naive `f64` approach yields a wrong boolean result at exactly the kind of magnitude present on mainnet. The equivalent naive-`f64` pattern is still used unfixed in the legacy Tower-BFT vote/switch-threshold and propagation code paths that remain active for validators not yet migrated to Alpenglow/votor.

### Finding Description
The comparison-based bug class in the report — “both `veTokenAmount` and price are being treated as if they are in 18 decimal units” causing an incorrect calculated value be used downstream — maps here to “a stake ratio is computed with insufficient numeric precision, causing an incorrect calculated value (threshold-passed boolean) to be used downstream in a consensus decision.”

Concretely:
- `core/src/consensus.rs::check_vote_stake_threshold` computes `let lockout = *fork_stake as f64 / total_stake as f64;` then compares `lockout > threshold_size` to decide `ThresholdDecision::PassedThreshold` vs `FailedThreshold`. [1](#0-0) 
- The same `as f64` division-and-compare pattern recurs in `make_check_switch_threshold_decision` (`SWITCH_FORK_THRESHOLD` comparisons), [2](#0-1)  in `ForkStats::fork_weight`, [3](#0-2)  in `VoteStakeTracker::add_vote_pubkey`'s threshold-crossing detection, [4](#0-3)  and in `update_slot_propagated_threshold_from_votes`'s `SUPERMINORITY_THRESHOLD` check. [5](#0-4) 
- Agave's own newer `votor-messages/src/fraction.rs::Fraction` type exists precisely to eliminate this defect, using integer cross-multiplication (`u128` intermediate) instead of `f64` division, and its test explicitly demonstrates the flaw: for `total_stake = 100_000_000_000_000_000` and `stake = 60_000_000_000_000_001` (60% + 1 lamport), the naive `f64` ratio evaluates `<= 0.6` — the wrong answer — while the `Fraction` comparison correctly reports it as `> 60%`. [6](#0-5) 
- No guard exists in the legacy Tower-BFT path (`core/src/consensus.rs`, `progress_map.rs`, `vote_stake_tracker.rs`, `replay_stage.rs`, `fork_choice.rs`) to bound or correct this precision loss; unlike `votor-messages`, none of these call sites use `Fraction`, checked `u128` cross-multiplication, or any epsilon/rounding-direction safeguard.

### Impact Explanation
`check_vote_stake_threshold` and `make_check_switch_threshold_decision` gate whether a validator casts a vote or switches forks — core safety mechanisms of Tower-BFT designed to prevent voting for conflicting forks. If the computed ratio is silently rounded across the threshold boundary at the exact stake magnitudes present on a large mainnet-scale cluster, a validator could:
- Vote (`PassedThreshold`) when the true stake ratio is actually below the safety threshold, weakening the lockout-based safety guarantee, or
- Fail to vote/switch when it legitimately should, contributing to unnecessary liveness stalls.

Because this directly feeds a validator's own vote/switch decision (not just telemetry), it falls in the "false execution/rooting/acceptance" impact category for runtime/consensus components.

### Likelihood Explanation
Likelihood is low-to-medium: the precision loss only manifests when stake magnitudes are large enough (>2^53 lamports, i.e., roughly >9,000,000 SOL of total/fork stake) and the true ratio sits extremely close to the threshold boundary — a naturally occurring, non-adversarial condition rather than one requiring a malicious peer or privileged actor. An attacker with knowledge of exact current stake distributions (public information) could in principle attempt to engineer stake amounts near a boundary, but this requires influencing/observing precise aggregate stake, which is difficult to control precisely enough to guarantee a specific rounding outcome. This matches the "Medium" severity classification given to the original report, since the defect is real and unguarded but requires specific numeric alignment to trigger a visible consensus effect.

### Recommendation
Replace the `as f64` ratio-and-compare pattern in the legacy Tower-BFT threshold checks (`check_vote_stake_threshold`, `make_check_switch_threshold_decision`, `ForkStats::fork_weight`, `VoteStakeTracker::add_vote_pubkey`, `update_slot_propagated_threshold_from_votes`) with the same integer-cross-multiplication `Fraction` comparison approach already implemented in `votor-messages/src/fraction.rs`, eliminating floating-point precision loss for large stake values.

### Proof of Concept
The existing repository test already constitutes a proof of concept for the root numerical defect:
```rust
// votor-messages/src/fraction.rs:76-84
#[test]
fn test_f64_precision_loss() {
    let total_stake = NonZeroU64::new(100_000_000_000_000_000).unwrap();
    let stake = 60_000_000_000_000_001u64; // 60% + 1

    let f64_ratio = stake as f64 / total_stake.get() as f64;
    assert!(f64_ratio <= 0.6); // wrong!
    assert!(Fraction::new(stake, total_stake) > Fraction::from_percentage(60));
}
```
The same numeric pattern (`stake as f64 / total_stake as f64` compared against a fixed threshold) is reachable in `core/src/consensus.rs::check_vote_stake_threshold` and sibling functions with real, mainnet-scale lamport stake values, reproducing the identical rounding error in an actual vote/switch-threshold decision path. [1](#0-0)

### Citations

**File:** core/src/consensus.rs (L1212-1214)
```rust
                    if (locked_out_stake as f64 / total_stake as f64) > SWITCH_FORK_THRESHOLD {
                        return SwitchForkDecision::SwitchProof(switch_proof);
                    }
```

**File:** core/src/consensus.rs (L1351-1368)
```rust
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

**File:** core/src/consensus/progress_map.rs (L226-230)
```rust
impl ForkStats {
    /// Return fork_weight, i.e. bank_stake over total_stake.
    pub fn fork_weight(&self) -> f64 {
        self.fork_stake as f64 / self.total_stake as f64
    }
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

**File:** core/src/replay_stage.rs (L4898-4902)
```rust
        if leader_propagated_stats.total_epoch_stake == 0
            || leader_propagated_stats.propagated_validators_stake as f64
                / leader_propagated_stats.total_epoch_stake as f64
                > SUPERMINORITY_THRESHOLD
        {
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
