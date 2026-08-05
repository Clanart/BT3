### Title
Legacy Tower-BFT threshold checks use lossy `u64 as f64` division to compare stake ratios against consensus constants — ([File: core/src/consensus.rs])

### Summary
The external report's bug class is: dividing/scaling a value before comparison introduces rounding/precision loss that can flip the outcome of a critical threshold comparison. Agave's legacy Tower-BFT consensus path (`core/src/consensus.rs`, `core/src/commitment_service.rs`, `runtime/src/commitment.rs`, `ledger/src/blockstore_processor.rs`, `core/src/consensus/vote_stake_tracker.rs`, `core/src/replay_stage.rs`) has the exact analog: it repeatedly casts `u64` stake amounts (in lamports) to `f64` and divides, then compares the resulting float against constants like `VOTE_THRESHOLD_SIZE` (0.67), `SWITCH_FORK_THRESHOLD`, `DUPLICATE_THRESHOLD`, `SUPERMINORITY_THRESHOLD`. Because `f64` only has 52 bits of mantissa (exact integer representation only up to 2^53 ≈ 9.007×10^15), and real-world total stake in lamports on Solana mainnet is on the order of several×10^17 (hundreds of millions of SOL × 1e9 lamports/SOL), both the numerator and denominator lose precision on the `as f64` cast before the division even happens — a rounding error class that matches the "price down-escalation" issue in the report, where a raw integer value is scaled/divided in a lossy way before being compared to a threshold.

### Finding Description
Throughout the legacy (non-Alpenglow) consensus code, stake ratios are computed like this: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) 

All of these follow the pattern `(stake as f64 / total_stake as f64) > THRESHOLD` (or the reciprocal `(total_stake as f64 * THRESHOLD) as u64`). This is exactly the report's "division-before-comparison" precision-loss pattern, but instead of `1e14`, the lossy operation is the `u64→f64` cast plus float division.

Notably, the maintainers have *already recognized and fixed this exact bug class* in the newer Alpenglow/votor code path by introducing an exact `Fraction` type that cross-multiplies with `u128` instead of using floating point: [9](#0-8) 
The accompanying test explicitly demonstrates that the `f64` approach silently returns a wrong boundary result: [10](#0-9) 
This fix is used in the certificate/BLS verification path and the votor consensus pool: [11](#0-10) [12](#0-11) 

However, the legacy Tower-BFT path — which is still the active consensus mechanism prior to/outside of full Alpenglow activation — was never migrated to `Fraction` and still relies on the lossy `f64` comparisons for switch-fork threshold, vote-lockout threshold, duplicate-confirmation, propagation-threshold, and root/commitment calculations.

### Impact Explanation
These f64-based comparisons directly gate consensus-critical decisions:
- `check_vote_stake_threshold` / `check_switch_threshold` in `core/src/consensus.rs` decide whether a validator is allowed to vote/switch forks.
- `is_slot_duplicate_confirmed` decides whether a slot is considered duplicate-confirmed (used to safely replay/dump/repair forks).
- `supermajority_root` in `ledger/src/blockstore_processor.rs` and `get_highest_super_majority_root` in `core/src/commitment_service.rs` decide the highest super-majority root, which affects snapshotting/rooting.
- `VoteStakeTracker::add_vote_pubkey` decides when optimistic-confirmation/duplicate-confirmation thresholds are newly crossed.

If precision loss causes a validator's local computation of a stake ratio to differ from the "true" rational value near a threshold boundary (e.g., exactly at 2/3 or 52%), different validators could reach different votes/rooting/duplicate-confirmation decisions for the same input data purely due to floating-point rounding, rather than due to different underlying stake data. This is a "false execution/rooting/acceptance"-class risk: it can affect which fork is treated as duplicate-confirmed/rooted. In practice the risk is bounded by clustering and by validators generally sharing the same values (all inputs come from the same stake table so results are usually consistent), so it is not by itself an attacker-controlled exploit, but it is a real precision-loss defect in a security-critical comparison — the same "may result in a loss of precision, because Solidity/float does not hold decimals/precision and can round differently" class flagged by the report, and the codebase's own `Fraction` fix and test prove the maintainers consider this pattern unsafe.

### Likelihood Explanation
Total lamport stake on mainnet is already well beyond 2^53 (≈9.007×10^15), so the `as f64` cast of `total_stake` (and often also of accumulated `stake`/`locked_out_stake`) already loses low-order bits on every call in the hot consensus path, on every validator, on every slot. The precision loss is guaranteed to occur constantly; whether it ever flips the > / <= outcome of a threshold check depends on how close the true ratio is to the constant boundary, which happens periodically given natural stake distribution near round percentages (e.g. exactly at 2/3, 60%, 52%, etc.), making the likelihood of an eventual boundary flip non-negligible over long-running mainnet operation, even though it's not attacker-triggerable on demand.

### Recommendation
Replace the lossy `stake as f64 / total_stake as f64` comparisons in the legacy Tower-BFT path with the same exact rational-arithmetic technique already used for Alpenglow (`votor-messages/src/fraction.rs`'s `Fraction` type, using `u128` cross-multiplication), specifically in:
- `core/src/consensus.rs` (`is_slot_confirmed`, `is_slot_duplicate_confirmed`, `check_vote_stake_threshold`, `make_check_switch_threshold_decision`)
- `core/src/commitment_service.rs` (`get_highest_super_majority_root`)
- `ledger/src/blockstore_processor.rs` (`supermajority_root`)
- `runtime/src/commitment.rs` (`get_lockout_count`)
- `core/src/consensus/vote_stake_tracker.rs` (`add_vote_pubkey`)
- `core/src/replay_stage.rs` (`update_slot_propagated_threshold_from_votes`)

so that all consensus-affecting stake-ratio comparisons are computed with exact integer/rational arithmetic instead of floating point, eliminating cast-and-divide precision loss near threshold boundaries.

### Proof of Concept
Demonstration of the underlying precision-loss mechanism, mirrored directly from the codebase's own test (which the maintainers wrote to justify the `Fraction` fix, but which is *not* applied to the legacy Tower path): [10](#0-9) 
```rust
let total_stake = NonZeroU64::new(100_000_000_000_000_000).unwrap(); // realistic mainnet-scale lamport stake
let stake = 60_000_000_000_000_001u64; // 60% + 1 lamport

let f64_ratio = stake as f64 / total_stake.get() as f64;
assert!(f64_ratio <= 0.6); // WRONG: true ratio is > 0.6, but f64 rounds it down
```
The same pattern (`stake as f64 / total_stake as f64`) is used verbatim, unfixed, in the legacy consensus threshold checks cited above (`core/src/consensus.rs:591-595`, `1212-1214`, `1349-1364`; `core/src/commitment_service.rs:59`; `ledger/src/blockstore_processor.rs:2040`; `runtime/src/commitment.rs:152`), so at mainnet-scale stake values the same rounding error can occur inside live vote/switch/duplicate-confirmation/root decisions rather than only in a test.

### Citations

**File:** core/src/consensus.rs (L591-595)
```rust
        voted_stakes
            .get(&slot)
            .map(|stake| (*stake as f64 / total_stake as f64) > self.threshold_size)
            .unwrap_or(false)
    }
```

**File:** core/src/consensus.rs (L1212-1214)
```rust
                    if (locked_out_stake as f64 / total_stake as f64) > SWITCH_FORK_THRESHOLD {
                        return SwitchForkDecision::SwitchProof(switch_proof);
                    }
```

**File:** core/src/consensus.rs (L1349-1364)
```rust
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
```

**File:** core/src/commitment_service.rs (L54-64)
```rust
fn get_highest_super_majority_root(mut rooted_stake: Vec<(Slot, u64)>, total_stake: u64) -> Slot {
    rooted_stake.sort_by(|a, b| a.0.cmp(&b.0).reverse());
    let mut stake_sum = 0;
    for (root, stake) in rooted_stake {
        stake_sum += stake;
        if (stake_sum as f64 / total_stake as f64) > VOTE_THRESHOLD_SIZE {
            return root;
        }
    }
    0
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

**File:** runtime/src/commitment.rs (L144-158)
```rust
    // Returns the lowest level at which at least `minimum_stake_percentage` of the total epoch
    // stake is locked out
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

**File:** core/src/replay_stage.rs (L4898-4905)
```rust
        if leader_propagated_stats.total_epoch_stake == 0
            || leader_propagated_stats.propagated_validators_stake as f64
                / leader_propagated_stats.total_epoch_stake as f64
                > SUPERMINORITY_THRESHOLD
        {
            leader_propagated_stats.is_propagated = true;
            did_newly_reach_threshold = true
        }
```

**File:** votor-messages/src/fraction.rs (L41-58)
```rust
impl PartialOrd for Fraction {
    fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
        Some(self.cmp(other))
    }
}

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

**File:** votor/src/consensus_pool/slot_stake_counters.rs (L114-138)
```rust
    fn is_safe_to_notar(&self, block_id: &Hash, stake: &Stake) -> bool {
        // White paper v1.1 page 22: The event is only issued if the node voted in slot s already,
        // but not to notarize b. Moreover:
        // notar(b) >= 40% or (skip(s) + notar(b) >= 60% and notar(b) >= 20%)
        if let Some(Vote::Notarize(my_vote)) = self.my_first_vote.as_ref()
            && &my_vote.block.block_id == block_id
        {
            return false; // I voted for the same block, no need to send NotarizeFallback
        }
        trace!(
            "safe_to_notar {block_id:?} skip_ratio={} notarized_ratio={}",
            self.skip_total as f64 / self.total_stake.get() as f64,
            *stake as f64 / self.total_stake.get() as f64
        );
        // Check if the block fits condition (i) 40% of stake holders voted notarize
        let notarized_ratio = Fraction::new(*stake, self.total_stake);
        let notarized_plus_skip_ratio = Fraction::new(
            self.skip_total.checked_add(*stake).unwrap(),
            self.total_stake,
        );
        notarized_ratio >= SAFE_TO_NOTAR_MIN_NOTARIZE_ONLY
            // Check if the block fits condition (ii) 20% notarized, and 60% notarized or skip
            || (notarized_ratio >= SAFE_TO_NOTAR_MIN_NOTARIZE_FOR_NOTARIZE_OR_SKIP
                && notarized_plus_skip_ratio >= SAFE_TO_NOTAR_MIN_NOTARIZE_AND_SKIP)
    }
```
