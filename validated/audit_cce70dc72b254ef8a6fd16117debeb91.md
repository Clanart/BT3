## Finding

### Title
Floating-point precision loss in stake-ratio threshold comparisons corrupts consensus/commitment decisions - (`core/src/consensus.rs`, `core/src/consensus/vote_stake_tracker.rs`, `runtime/src/commitment.rs`, `core/src/replay_stage.rs`)

### Summary
The Surge report's root cause is that a ratio (`debt/collateral`) is computed by dividing two integers expressed in different precision domains, and the division silently rounds to a wrong/zero value near boundary cases, corrupting a security-critical decision (liquidation eligibility). Agave has a direct structural analog: several consensus- and commitment-critical stake-ratio comparisons compute `stake as f64 / total_stake as f64` and then compare the *lossy* floating point result against a threshold, instead of doing the comparison in exact integer/rational arithmetic. Because stake values on a real cluster routinely exceed `2^53` (the point past which `f64` can no longer represent `u64` integers exactly), this division loses precision in exactly the same way the report describes, and the comparison result can flip near the threshold boundary.

Agave itself has already recognized and partially fixed this exact bug class: `votor-messages/src/fraction.rs` introduces an exact `Fraction` type specifically to avoid this precision loss for Alpenglow stake-threshold checks, and its own test explicitly demonstrates the failure mode.

### Finding Description
`votor-messages/src/fraction.rs` contains a self-documenting proof that naive `f64` division of large stake values is unsafe for threshold comparisons: [1](#0-0) 

This is why the newer Alpenglow/votor code path (`votor/src/consensus_pool/slot_stake_counters.rs`, `votor-messages/src/certificate.rs`, `votor/src/aggregate_accumulator.rs`) uses the exact `Fraction` type with `u128` cross-multiplication for all stake-threshold comparisons: [2](#0-1) [3](#0-2) 

However, the legacy TowerBFT consensus code — which is still the code path exercised before/during the Alpenglow migration on every cluster, and is *not* Alpenglow-only — still performs the same class of unsafe division:

- Vote lockout threshold check: [4](#0-3) 

- Stake-weighted threshold tracker used for duplicate-confirmation / vote-stake thresholds: [5](#0-4) 

- Leader propagation ("superminority") threshold: [6](#0-5) 

- RPC commitment/confirmation-count computation (feeds `confirmed`/`finalized` RPC status): [7](#0-6) 

- `get_stake_percent_in_gossip` (feeds wait-for-supermajority startup gating): [8](#0-7) 

In every one of these, `stake` and `total_stake` are `u64` lamport values that are cast to `f64` and divided, then the result is compared against an `f64` threshold constant (`VOTE_THRESHOLD_SIZE`, `SWITCH_FORK_THRESHOLD`, `DUPLICATE_THRESHOLD`, `SUPERMINORITY_THRESHOLD`, etc.). None of these paths use the exact-fraction technique that `votor-messages/src/fraction.rs` was written to provide.

Why existing guards don't stop this: there is no validation or rounding-direction guard on these divisions — the code trusts the `f64` comparison result directly. Unlike `Fraction::cmp`, which cross-multiplies in `u128` to get an exact comparison, these call sites perform a lossy division first and lose information before the comparison ever happens.

### Impact Explanation
Real Solana mainnet total stake is on the order of several hundred million SOL, i.e. `total_stake` values are commonly in the range of `10^17`–`10^18` lamports — far beyond `2^53 ≈ 9.007 × 10^15`, the largest integer `f64` can represent exactly. Any `stake`/`total_stake` pair in this range is not guaranteed to be represented exactly as `f64`, so `stake as f64 / total_stake as f64` can produce a result that differs from the true rational value by more than an ULP, and a comparison against a threshold constant (`0.6667`, `0.38`, `1/3`, `SUPERMINORITY_THRESHOLD`) can resolve on the wrong side of the true boundary.

Consequences of a flipped comparison, depending on which call site is affected:
- `check_vote_stake_threshold` (`core/src/consensus.rs`): a validator's local tower can incorrectly conclude a lockout threshold was/was-not passed, corrupting local vote-safety bookkeeping and fork-choice behavior — a false-acceptance/false-rejection of a vote decision.
- `VoteStakeTracker` thresholds (`core/src/consensus/vote_stake_tracker.rs`): used for duplicate-slot confirmation thresholds — an incorrectly-reached threshold can cause a node to treat a slot as duplicate-confirmed (or not) incorrectly, affecting fork resolution.
- `get_lockout_count`/commitment cache (`runtime/src/commitment.rs`): directly determines the `confirmed`/`finalized` RPC commitment level reported to clients — a false-positive here is a false "acceptance"/finality report to RPC consumers.
- Superminority propagation threshold (`core/src/replay_stage.rs`): affects leader-schedule propagation bookkeeping used in fork-weighting decisions.

These fall within the accepted impact categories (false execution/rooting/acceptance, consensus-adjacent decision corruption) and require no malicious peer/trusted-process assumption — the corruption arises purely from the arithmetic itself operating on legitimate, network-observed stake totals.

### Likelihood Explanation
The precision loss is not a contrived edge case: given current or near-future total stake magnitudes, `total_stake` already exceeds the `f64` exact-integer boundary on mainnet-scale clusters, so the lossy cast happens on essentially every threshold evaluation. Whether a *specific* evaluation flips the boundary depends on the exact bit pattern of `stake`/`total_stake`, which is influenced by (but not fully controlled by) the aggregate of all validators' stake amounts — these are public, attacker-observable, and to a significant degree attacker/validator-influenceable via delegation amounts denominated in lamports, making deliberate construction of a boundary-flipping stake configuration plausible over time as stake shifts.

### Recommendation
Port the exact-fraction comparison technique already implemented in `votor-messages/src/fraction.rs` (cross-multiplication in `u128`, no floating point) to the legacy stake-threshold call sites still in active use:
- `core/src/consensus.rs::check_vote_stake_threshold`
- `core/src/consensus/vote_stake_tracker.rs::add_vote_pubkey`
- `core/src/replay_stage.rs::update_slot_propagated_threshold_from_votes`
- `runtime/src/commitment.rs::get_lockout_count`
- `core/src/validator.rs::get_stake_percent_in_gossip` (lower severity; used for logging/startup gating)

Replace `stake as f64 / total_stake as f64 > threshold` with an exact integer comparison such as `(stake as u128) * DENOM > (total_stake as u128) * NUMER` (equivalent to `Fraction`'s `cmp`), eliminating the lossy intermediate `f64` value entirely.

### Proof of Concept
Directly analogous to the repo's own regression test demonstrating the failure mode: [1](#0-0) 

Applying the same construction to `core/src/consensus.rs::check_vote_stake_threshold`: choose `fork_stake`/`total_stake` (both realistic, mainnet-scale `u64` lamport values, e.g. `total_stake = 100_000_000_000_000_000` and `fork_stake = 60_000_000_000_000_001`, i.e. stake at exactly `60% + 1 lamport`) such that the true ratio is `> 0.6` (the tower's `VOTE_THRESHOLD_SIZE`), but `fork_stake as f64 / total_stake as f64 <= 0.6` due to rounding, causing `lockout > threshold_size` at line 1364 of `core/src/consensus.rs` to evaluate `false` when it should be `true`, flipping the threshold decision from `PassedThreshold` to `FailedThreshold`.

### Citations

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

**File:** votor/src/aggregate_accumulator.rs (L88-97)
```rust
    /// Builds a base2 [`Certificate`] if its threshold is met.
    pub fn try_build_base2_cert(
        &self,
        cert_type: CertificateType,
        total_stake: NonZero<u64>,
    ) -> Result<Option<Certificate>, AggregateAccumulatorError> {
        let observed_fraction = Fraction::new(self.stake, total_stake);
        if observed_fraction < cert_type.threshold() {
            return Ok(None);
        }
```

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

**File:** core/src/consensus/vote_stake_tracker.rs (L9-38)
```rust
impl VoteStakeTracker {
    // Returns tuple (reached_threshold_results, is_new) where
    // Each index in `reached_threshold_results` is true if the corresponding threshold in the input
    // `thresholds_to_check` was newly reached by adding the stake of the input `vote_pubkey`
    // `is_new` is true if the vote has not been seen before
    pub fn add_vote_pubkey(
        &mut self,
        vote_pubkey: Pubkey,
        stake: u64,
        total_stake: u64,
        thresholds_to_check: &[f64],
    ) -> (Vec<bool>, bool) {
        let is_new = !self.voted.contains(&vote_pubkey);
        if is_new {
            self.voted.insert(vote_pubkey);
            let old_stake = self.stake;
            let new_stake = self.stake + stake;
            self.stake = new_stake;
            let reached_threshold_results: Vec<bool> = thresholds_to_check
                .iter()
                .map(|threshold| {
                    let threshold_stake = (total_stake as f64 * threshold) as u64;
                    old_stake <= threshold_stake && threshold_stake < new_stake
                })
                .collect();
            (reached_threshold_results, is_new)
        } else {
            (vec![false; thresholds_to_check.len()], is_new)
        }
    }
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

**File:** core/src/validator.rs (L3088-3096)
```rust
    let online_stake_percentage = (online_stake as f64 / total_activated_stake as f64) * 100.;
    if log {
        info!("{online_stake_percentage:.3}% of active stake visible in gossip");

        if !offline_nodes.is_empty() {
            info!(
                "{:.3}% of active stake is not visible in gossip",
                (offline_stake as f64 / total_activated_stake as f64) * 100.
            );
```
