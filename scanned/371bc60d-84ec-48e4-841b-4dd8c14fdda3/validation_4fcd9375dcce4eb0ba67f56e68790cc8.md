### Title
f64 precision loss in `check_switch_threshold`/`check_vote_stake_threshold` can flip a true stake fraction across the 38%/threshold boundary - ([File: core/src/consensus.rs])

### Summary
Sherlock M-7 shows that a naive integer-division based threshold (`slotSize`) truncates and lets a spammer accumulate rounding error to force a boolean check to flip against the true intent. Agave's Tower vote-safety logic contains a structurally identical bug class, but using `f64` division/comparison instead of integer truncation: `(locked_out_stake as f64 / total_stake as f64) > SWITCH_FORK_THRESHOLD` and `fork_stake as f64 / total_stake as f64 > threshold_size`. The repo's own `votor-messages/src/fraction.rs` test (`test_f64_precision_loss`) proves this exact operation can silently invert a true `> 0.6` relationship into `<= 0.6`, and states the `Fraction` type exists specifically "for precise stake threshold comparisons" to avoid this. The legacy Tower code in `core/src/consensus.rs` (and similarly `core/src/commitment.rs`, `ledger/src/blockstore_processor.rs::supermajority_root`) never migrated to `Fraction` and still performs the vulnerable floating point comparison.

### Finding Description
`make_check_switch_threshold_decision` accumulates locked-out stake and, for every vote account it processes, tests: [1](#0-0) 
and again in the gossip-votes loop: [2](#0-1) 

The equivalent per-vote lockout threshold check in `check_vote_stake_threshold` uses the same pattern: [3](#0-2) 

Both compute `stake as f64 / total_stake as f64` and compare against a constant threshold (`SWITCH_FORK_THRESHOLD` ≈ 0.38, or `self.threshold_size` ≈ 0.67). `u64` values in the tens-of-millions-of-SOL range (lamports, i.e. up to ~10^18) lose precision when cast to `f64` (53-bit mantissa), and division introduces additional rounding. The codebase's own regression test demonstrates that a genuine `stake / total_stake` ratio of `60% + epsilon` can compute in `f64` to `<= 0.6`: [4](#0-3) 

This is the exact "rounding loss defeats a boundary check" primitive from the Sherlock report, just realized via floating point instead of integer truncation. `total_stake` and each validator's stake are network-visible, deterministic values (delegated stake amounts are chosen by stake-account owners/delegators), so an attacker who controls stake delegation amounts (their own stake, or stake they influence) can craft `stake`/`total_stake` pairs that sit exactly on this misrounding boundary, causing every validator running this identical, deterministic `f64` computation to reach a wrong (but consistent, since it's the same computation on the same values) decision about whether the switch/vote-lockout threshold was crossed.

Unlike the Sherlock bug, this doesn't move funds, but it corrupts the *decision value* fed into `SwitchForkDecision`/`ThresholdDecision`, which gates whether a validator is permitted to switch forks or vote — i.e., a Tower safety invariant.

### Impact Explanation
`check_switch_threshold` decides whether a validator is allowed to switch its vote to another fork (`SwitchForkDecision::SwitchProof` vs `FailedSwitchThreshold`), and `check_vote_stake_thresholds` decides whether a vote passes the deep lockout threshold used by `can_vote_on_candidate_bank` before permitting a vote: [5](#0-4) 
If precision loss causes the computed ratio to fall on the wrong side of the threshold constant when the true stake ratio does not, this is a deterministic, network-wide miscalculation (every honest validator computes the same wrong boolean from the same on-chain stake state) that can either: (a) block a legitimate fork switch/vote that should be permitted, contributing to reduced participation/liveness degradation around fork-choice convergence, or (b) permit a switch that the true stake fraction did not actually justify, which is the specific invariant `check_switch_threshold` exists to prevent (voting on both forks without sufficient justification is normally an equivocation/slashing condition). This falls in the "false rooting/acceptance" / consensus-stability category rather than direct fund theft.

### Likelihood Explanation
Exploitability requires the total/locked-out stake values to land very close to the exact threshold boundary where `f64` rounding flips the comparison — this is a narrow window, but stake amounts are attacker-influenceable (stake delegation amounts are freely chosen), and the values are fully deterministic and public, so an attacker/researcher could in principle search for or engineer a stake configuration that reliably triggers the flip. It is not a single unprivileged transaction exploit like the Sherlock report (no capital-efficient "spam bids" analogy exists here since stake delegation has real economic cost), which lowers the practical likelihood relative to the original finding, but the underlying code pattern is confirmed defective by the repository's own test in `fraction.rs`.

### Recommendation
Replace the `f64` division/comparison in `core/src/consensus.rs` (`make_check_switch_threshold_decision`, `check_vote_stake_threshold`) and `core/src/commitment.rs`/`ledger/src/blockstore_processor.rs::supermajority_root` with the exact, precision-safe `Fraction`/cross-multiplication comparison already implemented in `votor-messages/src/fraction.rs`, i.e. compare `locked_out_stake * threshold_denominator` against `total_stake * threshold_numerator` in `u128` rather than casting to `f64`.

### Proof of Concept
Using the repository's existing test as a template (`votor-messages/src/fraction.rs:76-84`), pick `total_stake` and `locked_out_stake` such that the true fraction is `SWITCH_FORK_THRESHOLD + ε` (e.g. total_stake = 100_000_000_000_000_000, locked_out_stake = 38_000_000_000_000_001), then evaluate `(locked_out_stake as f64 / total_stake as f64) > SWITCH_FORK_THRESHOLD` — the same class of computation demonstrated to yield `false` (wrong) in the repo's own `test_f64_precision_loss` for the analogous 60% case. Feeding such stake values into `Tower::make_check_switch_threshold_decision` would cause `SwitchForkDecision::FailedSwitchThreshold` to be returned even though the true locked-out stake exceeds the switch threshold.

Note: I was not able to fully verify from the index alone whether `SWITCH_FORK_THRESHOLD`'s exact numeric boundary combined with realistic lamport-scale stake totals can be hit precisely enough to flip in practice for this specific constant (0.38); the `fraction.rs` test only demonstrates the flip for 0.6. Confirming an exact PoC value for `SWITCH_FORK_THRESHOLD` would require running the arithmetic, which is outside what the code index can confirm.

### Citations

**File:** core/src/consensus.rs (L1211-1214)
```rust
                    locked_out_stake += stake;
                    if (locked_out_stake as f64 / total_stake as f64) > SWITCH_FORK_THRESHOLD {
                        return SwitchForkDecision::SwitchProof(switch_proof);
                    }
```

**File:** core/src/consensus.rs (L1264-1267)
```rust
                locked_out_stake += stake;
                if (locked_out_stake as f64 / total_stake as f64) > SWITCH_FORK_THRESHOLD {
                    return SwitchForkDecision::SwitchProof(switch_proof);
                }
```

**File:** core/src/consensus.rs (L1351-1367)
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

**File:** core/src/consensus/fork_choice.rs (L349-365)
```rust
    // Check if we failed any of the vote thresholds.
    let mut threshold_passed = true;
    for threshold_failure in vote_thresholds {
        let &ThresholdDecision::FailedThreshold(vote_depth, fork_stake) = threshold_failure else {
            continue;
        };
        failure_reasons.push(HeaviestForkFailures::FailedThreshold(
            candidate_vote_bank_slot,
            vote_depth,
            fork_stake,
            total_threshold_stake,
        ));
        // Ignore shallow checks for voting purposes
        if (vote_depth as usize) >= tower.threshold_depth {
            threshold_passed = false;
        }
    }
```
