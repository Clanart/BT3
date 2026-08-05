## Title
Floating-point stake-ratio arithmetic in duplicate/optimistic-confirmation threshold tracking can mis-fire threshold crossing at realistic total-stake magnitudes - (File: `core/src/consensus/vote_stake_tracker.rs`)

## Summary
`VoteStakeTracker::add_vote_pubkey` (used to detect when accumulated vote stake crosses the duplicate-confirmation / optimistic-confirmation thresholds, e.g. `VOTE_THRESHOLD_SIZE`) computes the threshold cutoff as `(total_stake as f64 * threshold) as u64`. The repo's own `Fraction` type and its test (`votor-messages/src/fraction.rs`) demonstrate that this exact `f64` multiply/compare pattern silently loses precision and produces the wrong side of a stake-ratio comparison once `total_stake` reaches magnitudes on the order of real Solana total stake (~1e17). This is the same rounding-down-in-a-derived-threshold class of bug as the PoolTogether report (an approximate/derived quantity is used as if it were exact, and the discrepancy flips a discrete state-transition decision), except here the state transition is duplicate-confirmation/optimistic-confirmation of a slot rather than a reward tier.

## Finding Description
`VoteStakeTracker::add_vote_pubkey` computes, for each stake-weighted threshold to check: [1](#0-0) 

```rust
let reached_threshold_results: Vec<bool> = thresholds_to_check
    .iter()
    .map(|threshold| {
        let threshold_stake = (total_stake as f64 * threshold) as u64;
        old_stake <= threshold_stake && threshold_stake < new_stake
    })
    .collect();
```

This same `total_stake as f64 / (or *) threshold`-style computation for consensus-critical stake ratios recurs in multiple safety-relevant call sites throughout the codebase:
- `check_vote_stake_threshold` in TowerBFT vote-lockout threshold checks: [2](#0-1) 
- `check_switch_threshold` fork-switch proof accumulation: [3](#0-2) 
- `get_highest_super_majority_root` (commitment aggregation root computation): [4](#0-3) 
- `supermajority_root` in blockstore processing: [5](#0-4) 

The repository itself documents that this pattern is unsafe at realistic stake magnitudes. `votor-messages/src/fraction.rs` was introduced specifically because `f64` division/multiplication loses precision for stake comparisons, and its own regression test proves the failure mode: [6](#0-5) 

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

`f64` has a 53-bit mantissa (~9×10^15 representable integer precision), while Solana's total network stake (in lamports, 1 SOL = 1e9 lamports, and total supply on the order of hundreds of millions of SOL) sits at 1e17–1e18 lamports — squarely inside the regime where the test demonstrates a comparison flips to the wrong answer. `VoteStakeTracker`, the TowerBFT switch/lockout threshold checks, and the commitment/root-supermajority calculations all use exactly this unsafe `f64` arithmetic on `total_stake`, whereas the Alpenglow-era code (`GENESIS_VOTE_THRESHOLD: Fraction`, `votor/src/common.rs`, `votor/src/consensus_pool/slot_stake_counters.rs`) was migrated to the precise integer `Fraction` type for the same class of comparison. The legacy TowerBFT paths were not migrated.

## Impact Explanation
`VoteStakeTracker::add_vote_pubkey` output feeds `reached_duplicate_confirmed` / `reached_optimistic_confirmed` decisions in `core/src/cluster_info_vote_listener.rs`, which drive duplicate-confirmation and optimistic-confirmation notifications sent to RPC/gossip and block-notification consumers. `check_vote_stake_threshold`/`check_switch_threshold` drive whether a validator is permitted to vote/switch forks, and `get_highest_super_majority_root`/`supermajority_root` compute the "highest confirmed root" reported via commitment/RPC. Because the threshold cutoff is derived via lossy floating point instead of exact integer arithmetic, at real-network stake magnitudes the computed cutoff can be off by amounts exceeding the true 1-lamport (or more) boundary, causing the discrete "did we cross the threshold" decision to fire on the wrong side of the true ratio — i.e., a slot can be reported as duplicate-confirmed/optimistically-confirmed (or a root/threshold reached) when the true stake-weighted ratio has not actually crossed the threshold, or vice versa. This falls in the "false execution/rooting/acceptance" impact category: a node could report/act on an incorrect commitment or root determination that doesn't correspond to the true weighted-stake supermajority.

## Likelihood Explanation
This requires no malicious actor: it is a deterministic function of `total_stake` magnitude and the exact accumulated vote stake relative to the threshold boundary. Solana's real total active stake is well within the demonstrated precision-loss range (the report's own test uses 1e17, comparable to real mainnet lamport totals). The condition is triggered whenever the true stake ratio sits extremely close to a threshold (`VOTE_THRESHOLD_SIZE`, `SWITCH_FORK_THRESHOLD`, `DUPLICATE_THRESHOLD`) — which happens routinely as votes accumulate incrementally lamport-by-lamport in `VoteStakeTracker`. It is a low-probability-per-check but structurally guaranteed-to-occur-somewhere-eventually class of bug given how many stake accumulations cross near-boundary values over the life of a cluster; existing test coverage (`test_add_vote_pubkey`, `test_f64_precision_loss`) already demonstrates the exact discrepancy mechanism in isolation, it just hasn't been exercised at full-mainnet stake scale in these specific call sites.

## Recommendation
Replace the `f64`-based stake-ratio threshold comparisons in `core/src/consensus/vote_stake_tracker.rs`, `core/src/consensus.rs` (`check_vote_stake_threshold`, `check_switch_threshold`/`make_check_switch_threshold_decision`), `core/src/commitment_service.rs` (`get_highest_super_majority_root`), and `ledger/src/blockstore_processor.rs` (`supermajority_root`) with the existing exact-precision `Fraction` type (or equivalent `u128` cross-multiplication), consistent with how `votor-messages/src/fraction.rs` / `GENESIS_VOTE_THRESHOLD` already solve this for the Alpenglow migration path, so that stake-threshold crossing decisions are computed with exact integer arithmetic rather than lossy floating point.

## Proof of Concept
The repository's own test proves the underlying arithmetic flaw directly and can be adapted 1:1 to the vulnerable call sites: [6](#0-5) 
Applying the same `total_stake`/`stake` values to `VoteStakeTracker::add_vote_pubkey`'s `(total_stake as f64 * threshold) as u64` computation (`core/src/consensus/vote_stake_tracker.rs:30`), or to `check_vote_stake_threshold`'s `*fork_stake as f64 / total_stake as f64` comparison (`core/src/consensus.rs:1351`), reproduces an incorrect threshold-crossing decision at total stake ≈1e17 (comparable to real mainnet stake), i.e., a case where `old_stake <= threshold_stake && threshold_stake < new_stake` (or the corresponding `>` comparison) evaluates opposite to the true exact-fraction result. I was not able to run the Rust test suite in this ask-only session to directly execute a modified reproduction against `VoteStakeTracker`; this should be validated with a unit test mirroring `test_f64_precision_loss` but calling `VoteStakeTracker::add_vote_pubkey` / `Tower::check_vote_stake_threshold` directly.

### Citations

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

**File:** core/src/consensus.rs (L1211-1216)
```rust
                    locked_out_stake += stake;
                    if (locked_out_stake as f64 / total_stake as f64) > SWITCH_FORK_THRESHOLD {
                        return SwitchForkDecision::SwitchProof(switch_proof);
                    }
                    locked_out_vote_accounts.insert(vote_account_pubkey);
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
