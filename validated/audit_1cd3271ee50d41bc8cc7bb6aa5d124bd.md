## Title
Floating-point precision loss in stake-ratio threshold comparisons can yield incorrect consensus/commitment decisions - (File: `core/src/consensus.rs`, `runtime/src/commitment.rs`, `core/src/commitment_service.rs`)

### Summary
The external report's root cause is generic: a value is compared against a fixed decimal threshold, but the comparison arithmetic silently loses precision, so the comparison can resolve incorrectly at/near the boundary, flipping a pass/fail decision that should not have flipped. In Agave, the analogous pattern is the use of native `f64` division to compute a stake-ratio and compare it against a hard-coded threshold constant (e.g. `SWITCH_FORK_THRESHOLD`, `VOTE_THRESHOLD_SIZE`) in Tower-BFT vote-safety and optimistic-confirmation/root logic, instead of using exact integer arithmetic.

### Finding Description
`Tower::check_vote_stake_threshold` computes `let lockout = *fork_stake as f64 / total_stake as f64;` and then decides `PassedThreshold` if `lockout > threshold_size` [1](#0-0) . The switch-threshold decision in `make_check_switch_threshold_decision` does the same: `if (locked_out_stake as f64 / total_stake as f64) > SWITCH_FORK_THRESHOLD` [2](#0-1) .

The same pattern recurs in the commitment/root logic used for optimistic-confirmation and super-majority-root computation:
- `runtime/src/commitment.rs::get_lockout_count`: `if (sum as f64 / self.total_stake as f64) > minimum_stake_percentage` [3](#0-2) 
- `core/src/commitment_service.rs::get_highest_super_majority_root`: `if (stake_sum as f64 / total_stake as f64) > VOTE_THRESHOLD_SIZE` [4](#0-3) 

`f64` has only 53 bits of mantissa precision (~9.007×10^15 exactly representable integers). Stake values are denominated in lamports (1 SOL = 10^9 lamports), and total network/epoch stake in lamports can exceed 2^53 well within realistic mainnet stake levels. Once the numerator or denominator exceeds that range, the division result is rounded to the nearest representable `f64`, which can round the ratio up or down across the exact threshold boundary — exactly the same class of bug as the reported "decimal precision omitted, so the comparison against `DECIMAL` resolves incorrectly" issue.

Critically, the codebase itself has already identified and fixed this exact bug class for the newer Alpenglow/Votor consensus stack: `votor-messages/src/fraction.rs` introduces an integer `Fraction` type specifically "for precise stake threshold comparisons" and its own test (`test_f64_precision_loss`) demonstrates that `stake as f64 / total_stake as f64 <= 0.6` while the true ratio is `>60%` [5](#0-4) . That fix, however, was applied only to the Votor (Alpenglow) side (`votor/src/consensus_pool/slot_stake_counters.rs` uses `Fraction` cross-multiplication instead of `f64` division) [6](#0-5) . The legacy Tower-BFT code paths in `core/src/consensus.rs`, `runtime/src/commitment.rs`, and `core/src/commitment_service.rs`, which remain the production consensus/commitment mechanism for non-Alpenglow validators, still use raw `f64` division and were not similarly hardened.

No existing guard corrects this: the comparisons are plain `f64 >` comparisons with no epsilon/tolerance handling and no fallback to integer cross-multiplication, so once the stake magnitudes exceed `f64`'s exact-integer range, the comparison result is simply whatever IEEE-754 rounding produces.

### Impact Explanation
These comparisons gate safety-critical Tower-BFT decisions:
- `check_vote_stake_threshold`/`check_switch_threshold` decide whether a validator may cast a vote that violates its lockout (switch to a different fork) — the entire safety of Tower-BFT depends on this threshold being computed exactly, since an incorrect "PassedThreshold"/"SwitchProof" result can permit an unsafe fork switch.
- `get_lockout_count` and `get_highest_super_majority_root` determine which slot is reported as optimistically confirmed / rooted, which feeds into RPC "confirmed"/"finalized" status and downstream cluster logic (e.g., `blockstore_processor.rs`, `replay_stage.rs`).

A wrong result in either direction is a "false execution/rooting/acceptance" outcome: a slot could be treated as having crossed a supermajority/lockout threshold when it has not (false accept), or fail to be recognized as having crossed it (false reject, contributing to unnecessary liveness stalls). Because the computation is deterministic given the same stake inputs, the same rounding error would occur uniformly across all validators computing locally from equivalent stake data, meaning it does not directly cause fork divergence between honest validators, but it does mean the entire network's Tower-BFT/optimistic-confirmation safety margin is computed on a systematically imprecise basis at the exact class of stake magnitudes where 53-bit mantissa precision is exceeded — undermining the correctness guarantee the threshold is supposed to provide.

### Likelihood Explanation
This does not require a malicious peer, admin, or leaked key — it is a property of the arithmetic used on ordinary, validly-signed stake/vote data that already flows through these code paths in every epoch. The precision loss is guaranteed to manifest whenever stake sums (in lamports) exceed `2^53 (~9.007×10^15 lamports ≈ 9 million SOL)`, which is well within the scale of current total network stake and even individual large-stake validator sets, making the erroneous rounding a `latent, continuously-present computational defect` rather than a rare edge case triggerable only by an attacker-crafted input.

### Recommendation
Replace the `f64`-division-then-compare pattern in `core/src/consensus.rs` (`check_vote_stake_threshold`, `make_check_switch_threshold_decision`), `runtime/src/commitment.rs` (`get_lockout_count`), and `core/src/commitment_service.rs` (`get_highest_super_majority_root`) with exact integer cross-multiplication comparisons, mirroring the `Fraction` type already implemented in `votor-messages/src/fraction.rs` (i.e., compare `stake * 100` against `total_stake * threshold_pct` using `u128` arithmetic, avoiding any floating-point rounding).

### Proof of Concept
Using the exact scenario already present as a unit test in the codebase (`votor-messages/src/fraction.rs::test_f64_precision_loss`), substitute the same magnitudes into `Tower::check_vote_stake_threshold`/`get_lockout_count`:
1. Let `total_stake = 100_000_000_000_000_000` lamports and `fork_stake (or stake_sum) = 60_000_000_000_000_001` lamports — i.e., stake is `60% + 1 lamport`, genuinely above a `0.6` threshold.
2. Compute `fork_stake as f64 / total_stake as f64` — this rounds to a value `<= 0.6` (as demonstrated by the existing test assertion `assert!(f64_ratio <= 0.6); // wrong!`) [5](#0-4) .
3. If the configured `threshold_size`/`VOTE_THRESHOLD_SIZE` equals `0.6`, `check_vote_stake_threshold`'s comparison `lockout > threshold_size` [7](#0-6)  evaluates `false` even though the true stake ratio exceeds the threshold, causing `FailedThreshold` to be returned when the vote should actually be allowed (or the symmetric case near other boundaries can cause the opposite, incorrect `PassedThreshold`).
4. The identical rounding failure applies verbatim to `get_lockout_count`'s `(sum as f64 / self.total_stake as f64) > minimum_stake_percentage` and `get_highest_super_majority_root`'s `(stake_sum as f64 / total_stake as f64) > VOTE_THRESHOLD_SIZE`, which can misreport the optimistically-confirmed/rooted slot boundary under the same stake magnitudes.

Note: I was not able to execute this scenario against the live `Tower`/`CommitmentAggregationData` code paths (no test harness run), so this analysis is based on static code reading and the codebase's own existing test demonstrating the exact `f64` rounding failure at these magnitudes; a Devin session with repo execution access would be needed to run a concrete regression test reproducing the wrong threshold decision end-to-end.

### Citations

**File:** core/src/consensus.rs (L1200-1217)
```rust
                if !last_vote_ancestors.contains(lockout_interval_start) && {
                    // Given a `lockout_interval_start` < root that appears in a
                    // bank for a `candidate_slot`, it must be that `lockout_interval_start`
                    // is an ancestor of the current root, because `candidate_slot` is a
                    // descendant of the current root
                    *lockout_interval_start > root
                } {
                    let stake = epoch_vote_accounts
                        .get(vote_account_pubkey)
                        .map(|(stake, _)| *stake)
                        .unwrap_or(0);
                    locked_out_stake += stake;
                    if (locked_out_stake as f64 / total_stake as f64) > SWITCH_FORK_THRESHOLD {
                        return SwitchForkDecision::SwitchProof(switch_proof);
                    }
                    locked_out_vote_accounts.insert(vote_account_pubkey);
                }
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

**File:** core/src/commitment_service.rs (L54-61)
```rust
fn get_highest_super_majority_root(mut rooted_stake: Vec<(Slot, u64)>, total_stake: u64) -> Slot {
    rooted_stake.sort_by(|a, b| a.0.cmp(&b.0).reverse());
    let mut stake_sum = 0;
    for (root, stake) in rooted_stake {
        stake_sum += stake;
        if (stake_sum as f64 / total_stake as f64) > VOTE_THRESHOLD_SIZE {
            return root;
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
