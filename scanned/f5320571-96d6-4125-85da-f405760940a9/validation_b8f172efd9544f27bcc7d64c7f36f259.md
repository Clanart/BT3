## Title
Floating-point precision loss in stake-fraction threshold comparisons for vote lockout / rooting decisions - (`core/src/consensus.rs`, `runtime/src/commitment.rs`, `ledger/src/blockstore_processor.rs`)

## Summary
The reported `reducedFraction` bug is fundamentally about a fraction-reduction routine that discards precision when approximating `numerator/denominator`, causing downstream logic to act on a materially different ratio than the true one. Agave's legacy Tower/consensus code contains the same *class* of bug: several stake-threshold checks compute `stake_fraction = stake as f64 / total_stake as f64` and then compare that lossy `f64` approximation against a threshold constant, instead of using exact integer/`u128` cross-multiplication. The codebase itself demonstrates awareness of this exact failure mode — `votor-messages/src/fraction.rs` contains a `Fraction` type built specifically to avoid it, with a test (`test_f64_precision_loss`) proving that `stake as f64 / total_stake as f64` can round the *wrong direction* relative to a precise integer comparison — yet the older Tower-based consensus paths were never migrated to use it.

## Finding Description
`Tower::check_vote_stake_threshold` in `core/src/consensus.rs` computes:
```rust
let lockout = *fork_stake as f64 / total_stake as f64;
...
if ... || lockout > threshold_size { PassedThreshold } else { FailedThreshold }
``` [1](#0-0) 

`total_stake` and `fork_stake` are `u64` lamport-denominated stake values that can exceed `2^53` (`f64`'s exact-integer range, ~9.007×10^15), given total network stake in lamports (~5×10^17 at max supply). Converting such values to `f64` already discards low-order bits before the division even occurs — an analog to `reducedFraction`'s truncation, just via floating-point rounding rather than explicit pruning. The same lossy pattern recurs in:
- `VoteStakeTracker::add_vote_pubkey`, computing `(total_stake as f64 * threshold) as u64` to detect crossing a stake threshold [2](#0-1) 
- `BlockCommitmentCache::get_lockout_count`, comparing `(sum as f64 / self.total_stake as f64) > minimum_stake_percentage` to compute confirmation counts [3](#0-2) 
- `supermajority_root` in blockstore processing, comparing `total as f64 / total_epoch_stake as f64 > VOTE_THRESHOLD_SIZE` to pick the root on startup [4](#0-3) 

By contrast, the newer Alpenglow/votor stake-threshold code deliberately avoids this: `Fraction::cmp` cross-multiplies numerator/denominator pairs in `u128` to guarantee an exact comparison, and its own test explicitly documents that the `f64` division approach used elsewhere is unreliable near threshold boundaries [5](#0-4) . `bls-cert-verify/src/cert_verify.rs::verify_stake` also uses this exact `Fraction` comparison for certificate-stake verification, confirming the project treats `f64` stake-ratio comparisons as unsafe for consensus-critical decisions [6](#0-5) .

## Impact Explanation
If a stake distribution lands exactly at (or extremely close to) a threshold boundary (e.g., the 2/3 supermajority lockout threshold, or the startup "supermajority root" determination), the `f64`-based comparison can round the ratio the wrong way relative to the true integer ratio, causing:
- A vote lockout threshold check (`check_vote_stake_thresholds`) to pass or fail incorrectly, affecting fork-choice/tower voting behavior.
- `supermajority_root` in `blockstore_processor.rs` to select a different (or no) root at startup, affecting rooting.
- `BlockCommitmentCache` confirmation-count reporting to be off by one level.

This maps to the "false execution/rooting/acceptance" impact category, since it directly concerns whether a fork/root is accepted as having crossed a stake supermajority.

## Likelihood Explanation
Likelihood is low. Unlike the original `reducedFraction` bug (which could deviate by >10% for arbitrary inputs), `f64`'s relative precision is ~2^-52, so the discrepancy versus the exact ratio is minuscule — it only matters if the true ratio lands within that vanishingly small epsilon of the threshold constant. Exploiting it deterministically would require an attacker to control the exact lamport-level total/voted stake at the network level, which is not achievable by an unprivileged actor (stake amounts are the aggregate of many independent stakers/validators, and `f64` truncation only manifests for extremely large aggregate stake values near a threshold boundary). This is a legitimate but low-severity/low-likelihood latent-precision defect rather than a readily attacker-triggerable exploit.

## Recommendation
Migrate the remaining legacy Tower/consensus stake-ratio comparisons (`Tower::check_vote_stake_threshold`, `VoteStakeTracker::add_vote_pubkey`, `BlockCommitmentCache::get_lockout_count`, `supermajority_root`) to the same exact-comparison approach already used by `votor_messages::Fraction` (i.e., `u128` cross-multiplication instead of `f64` division), eliminating the precision-loss class of bug network-wide rather than only in the newer Alpenglow path.

## Proof of Concept
No concrete on-chain PoC was found or constructed — exploitation would require crafting an aggregate stake value at the exact boundary of `f64` imprecision relative to a threshold constant, which is not practically controllable by a single unprivileged actor. The `test_f64_precision_loss` unit test in `votor-messages/src/fraction.rs` is the closest available demonstration in-repo, showing that `stake as f64 / total_stake as f64 <= 0.6` can hold even though the exact fraction is greater than 60% by one lamport:
```rust
let total_stake = NonZeroU64::new(100_000_000_000_000_000).unwrap();
let stake = 60_000_000_000_000_001u64; // 60% + 1
let f64_ratio = stake as f64 / total_stake.get() as f64;
assert!(f64_ratio <= 0.6); // wrong!
``` [7](#0-6) 

This confirms the same rounding-direction failure is reachable by the identical `f64` division pattern still used in `core/src/consensus.rs`, `runtime/src/commitment.rs`, and `ledger/src/blockstore_processor.rs`.

### Citations

**File:** core/src/consensus.rs (L1332-1369)
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

**File:** runtime/src/commitment.rs (L146-158)
```rust
    fn get_lockout_count(&self, slot: Slot, minimum_stake_percentage: f64) -> Option<usize> {
        self.get_block_commitment(slot).map(|block_commitment| {
            let iterator = block_commitment.commitment.iter().enumerate().rev();
            let mut sum = 0;
            for (i, stake) in iterator {
                sum += stake;
                if (sum as f64 / self.total_stake as f64) > minimum_stake_percentage {
                    return i + 1;
                }
            }
            0
        })
    }
```

**File:** ledger/src/blockstore_processor.rs (L2029-2047)
```rust
fn supermajority_root(roots: &[(Slot, u64)], total_epoch_stake: u64) -> Option<Slot> {
    if roots.is_empty() {
        return None;
    }

    // Find latest root
    let mut total = 0;
    let mut prev_root = roots[0].0;
    for (root, stake) in roots.iter() {
        assert!(*root <= prev_root);
        total += stake;
        if total as f64 / total_epoch_stake as f64 > VOTE_THRESHOLD_SIZE {
            return Some(*root);
        }
        prev_root = *root;
    }

    None
}
```

**File:** votor-messages/src/fraction.rs (L47-84)
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

#[cfg(test)]
mod tests {
    use super::*;

    fn frac(n: u64, d: u64) -> Fraction {
        Fraction::new(n, NonZeroU64::new(d).unwrap())
    }

    #[test]
    fn test_cmp() {
        assert!(frac(1, 3) < frac(1, 2));
        assert!(frac(2, 4) <= frac(1, 2));
        assert!(frac(2, 4) >= frac(1, 2));
        assert!(frac(3, 4) > frac(2, 3));
    }

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
