## Finding: Duplicate vote-aggregates over-count stake in `AggregateAccumulator::add_aggregate`, allowing certificate thresholds to be met with less than the true unique stake

### Title
Missing duplicate-rank check in `AggregateAccumulator::add_aggregate` inflates observed stake used for consensus certificate thresholds - (File: `votor/src/aggregate_accumulator.rs`)

### Summary
`AggregateAccumulator` tracks both a bitmap of validator ranks and a running `stake` total used to decide whether enough stake has voted to build a `Certificate` (Notarize/Finalize/Skip/etc.). For votes received from the network (external votes), `add_aggregate` unconditionally adds the aggregate's stake with `saturating_add`, while only OR-ing the rank bitmap. There is no check that the ranks being added are not already present. The "own vote" path (`add_own_vote_message`) explicitly guards against this by rejecting duplicates, but the external path used for all other validators' votes does not.

### Finding Description
`AggregateAccumulator::add_aggregate` is defined as: [1](#0-0) 

Contrast this with `add_own_vote_message`, which checks for and rejects duplicate ranks before mutating state: [2](#0-1) 

`add_aggregate` merges `ranks` with a bitwise OR (`self.ranks |= aggregate.ranks()`), which is idempotent — re-applying the same rank bit twice has no effect on the bitmap. But `self.stake = self.stake.saturating_add(aggregate.stake().get())` is **not** idempotent: it adds the aggregate's stake every time it is called, regardless of whether the ranks it represents were already accounted for.

`VoteAggregate` instances are constructed in the BLS sig-verifier from `VoteMessage`s received over the network: [3](#0-2) 

Each verified batch of votes is forwarded independently to the consensus pool as a `SigVerifiedBatch::Votes`: [4](#0-3) 

The only duplicate-detection present in this path is a `debug_assertions`-only sanity check that a *single* incoming batch of packets contains no duplicate `vote_message`s: [5](#0-4) 

This check does not persist across separate sig-verify invocations/batches, and is compiled out in release builds. No other component (gossip/CRDS dedup, a seen-signature cache, or persistent per-validator/per-slot bitmap) filters duplicate `VoteMessage`s or `VoteAggregate`s before they reach `add_pool_vote`: [6](#0-5) 

`PoolVote::External(a) => acc.add_aggregate(a)` is invoked directly here for every incoming external vote/aggregate batch, with no cross-batch dedup.

The accumulated `stake` value is subsequently compared directly against the certificate threshold to decide whether to emit a Notarize/Finalize/Skip/etc. certificate: [7](#0-6) 

### Impact Explanation
If the same validator's vote for a given slot/vote-type is received by a node more than once as a separate `VoteAggregate` — which can legitimately happen due to normal, non-malicious network duplication (UDP packet duplication, redundant relay paths in gossip/turbine fan-out, or a validator itself retransmitting an unacknowledged vote after a timeout) — each occurrence independently passes BLS signature verification (the signature is valid) and is forwarded to the consensus pool as a distinct `VoteAggregate`. `add_aggregate` will OR the same rank bit into the bitmap (no-op) but will add that validator's stake to `self.stake` again. This inflates the computed `observed_fraction` used to satisfy `cert_type.threshold()`, potentially causing a validator to construct/broadcast a Notarize, Finalize, or Skip certificate while actual unique participating stake is below the protocol's real threshold. This directly threatens the BFT safety assumption underpinning finalization (false/insecure "acceptance" of a certificate), which falls in the "false execution/rooting/acceptance, consensus halt" impact category.

### Likelihood Explanation
This does not require a malicious peer or validator — ordinary network conditions (duplicate packet delivery, redundant gossip relay, or benign retransmission on timeout) are sufficient to trigger repeated delivery of the same signed vote. Because dedup is only enforced within a single sig-verify batch (and only in debug builds), and the persistent, cross-batch accounting path (`AggregateAccumulator::add_aggregate`) has no duplicate-rank guard (unlike `add_own_vote_message`), the double-count can occur under normal operating conditions, not just adversarial ones.

### Recommendation
Add the same duplicate-rank check to `add_aggregate` that already exists in `add_own_vote_message`: before merging `aggregate.ranks()` into `self.ranks` and adding `aggregate.stake()`, compute the bits in `aggregate.ranks()` that are already set in `self.ranks`. If any overlap exists, either reject the whole aggregate (mirroring `add_own_vote_message`'s `Duplicate` error) or, since a `VoteAggregate` can bundle multiple validators, subtract the already-counted validators' stake from what gets added (recomputing per-rank stake contribution rather than adding the aggregate's total stake wholesale).

### Proof of Concept
1. A validator's signed `VoteMessage` (vote V, rank R, stake S) is broadcast and reaches the local node twice as two separate network deliveries (e.g., due to duplicate packet delivery or gossip relay duplication) — no attacker action required.
2. Each delivery is independently BLS-signature verified in `bls_vote_sigverify.rs` and wrapped into its own `VoteAggregate` via `VoteAggregate::new_from_verified_vote`, then sent to the consensus pool as separate `SigVerifiedBatch::Votes` messages.
3. `VotePool::add_pool_vote` is called twice with `PoolVote::External(aggregate)` for the same rank R:
   - First call: `self.ranks` bit R set (0→1), `self.stake += S`.
   - Second call: `self.ranks |= ranks` is a no-op (bit R already 1), but `self.stake = self.stake.saturating_add(S)` adds S again, since `add_aggregate` contains no analogous check to `add_own_vote_message`'s `self.ranks.replace(msg.rank as usize, true)` guard.
4. `try_build_base2_cert`/`try_build_base3_cert` compute `Fraction::new(self.stake, total_stake)` using the inflated `self.stake`, which can push `observed_fraction` above `cert_type.threshold()` earlier than warranted by genuinely distinct, unique participating stake.

### Citations

**File:** votor/src/aggregate_accumulator.rs (L56-67)
```rust
    /// Accumulate a vote aggregate into the accumulator.
    pub fn add_aggregate(
        &mut self,
        aggregate: &VoteAggregate,
    ) -> Result<u64, AggregateAccumulatorError> {
        self.signature
            .aggregate_with(std::iter::once(aggregate.signature()))
            .map_err(AggregateAccumulatorError::SignatureAggregationFailed)?;
        self.ranks |= aggregate.ranks();
        self.stake = self.stake.saturating_add(aggregate.stake().get());
        Ok(self.stake)
    }
```

**File:** votor/src/aggregate_accumulator.rs (L69-86)
```rust
    /// Accumulate own vote message into the accumulator.
    ///
    /// Due to nodes restarting or failover, etc. it is possible to get duplicates.
    pub fn add_own_vote_message(
        &mut self,
        msg: &VoteMessage,
    ) -> Result<u64, AggregateAccumulatorError> {
        let mut signature = self.signature;
        signature
            .aggregate_with(std::iter::once(&msg.signature))
            .map_err(AggregateAccumulatorError::SignatureAggregationFailed)?;
        if self.ranks.replace(msg.rank as usize, true) {
            return Err(AggregateAccumulatorError::Duplicate);
        }
        self.signature = signature;
        self.stake = self.stake.saturating_add(msg.stake.get());
        Ok(self.stake)
    }
```

**File:** votor/src/aggregate_accumulator.rs (L88-108)
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
        let mut ranks = self.ranks.clone();
        let new_len = ranks.last_one().map_or(0, |i| i.saturating_add(1));
        ranks.resize(new_len, false);
        let bitmap = encode_base2(&ranks).map_err(AggregateAccumulatorError::EncodingFailed)?;
        let signature = BLSSignature::from(self.signature);
        Ok(Some(Certificate {
            cert_type,
            signature,
            bitmap,
        }))
    }
```

**File:** votor-messages/src/sig_verified_messages.rs (L57-72)
```rust
impl VoteAggregate {
    /// Creates a sig verified vote batch from a VoteMessage.
    ///
    /// WARN: this is only public to enable handling already verified votes that are sent within
    /// the validator.  Think carefully before using this in production code.
    pub fn new_from_verified_vote(max_validators: usize, msg: VoteMessage) -> Self {
        assert!((msg.rank as usize) < max_validators);
        let mut ranks = BitVec::repeat(false, max_validators);
        ranks.set(msg.rank as usize, true);
        Self {
            vote: msg.vote,
            signature: msg.signature,
            stake: msg.stake,
            ranks,
        }
    }
```

**File:** bls-sigverify/src/bls_vote_sigverify.rs (L158-207)
```rust
fn process_verified_votes(
    verified_votes: Vec<VerifiedVotePayload>,
    root_bank: &Bank,
    cluster_info: &ClusterInfo,
    leader_schedule: &LeaderScheduleCache,
) -> (
    SigVerifiedBatch,
    HashMap<Pubkey, Vec<Slot>>,
    Vec<VoteAggregate>,
    Vec<ConsensusMetricsEvent>,
) {
    let mut votes_for_reward = Vec::with_capacity(verified_votes.len());
    let mut msgs_for_repair = HashMap::new();
    let mut vote_aggregates_for_pool = Vec::with_capacity(verified_votes.len());
    let mut votes_for_metrics = Vec::with_capacity(verified_votes.len());
    for payload in verified_votes {
        inspect_for_repair(&payload, &mut msgs_for_repair);

        for pubkey in &payload.sender_vote_account_pubkeys {
            votes_for_metrics.push(ConsensusMetricsEvent::Vote {
                id: *pubkey,
                vote: *payload.vote_aggregate.vote(),
            });
        }
        if rewards_wants_vote(
            cluster_info,
            leader_schedule,
            root_bank.slot(),
            payload.vote_aggregate.vote(),
        ) {
            votes_for_reward.push(payload.vote_aggregate.clone());
        }
        vote_aggregates_for_pool.push(payload.vote_aggregate);
    }
    let msgs_for_repair = msgs_for_repair
        .into_iter()
        .map(|(pubkey, mut slots)| {
            slots.sort_unstable();
            slots.dedup();
            (pubkey, slots)
        })
        .collect();
    let sig_verified_batch = SigVerifiedBatch::Votes(vote_aggregates_for_pool);
    (
        sig_verified_batch,
        msgs_for_repair,
        votes_for_reward,
        votes_for_metrics,
    )
}
```

**File:** bls-sigverify/src/bls_vote_sigverify.rs (L289-296)
```rust
    #[cfg(debug_assertions)]
    {
        let deduped = unverified_votes
            .iter()
            .map(|v| &v.vote_message)
            .collect::<HashSet<_>>();
        assert_eq!(deduped.len(), unverified_votes.len());
    }
```

**File:** votor/src/consensus_pool/vote_pool.rs (L142-164)
```rust
    /// Adds votes and if some certs can be produced and they are not already included in the completed certs, produces them.
    pub(super) fn add_pool_vote(
        &mut self,
        total_stake: NonZero<u64>,
        msg: &PoolVote,
        completed_certs: &BTreeMap<CertificateType, Arc<Certificate>>,
    ) -> Result<(u64, Option<Certificate>), AggregateAccumulatorError> {
        let vote = *msg.vote();
        let acc = self
            .accumulators
            .entry(vote)
            .or_insert_with(|| AggregateAccumulator::new(self.max_validators));
        let stake = match msg {
            PoolVote::Own(vote_msg) => acc.add_own_vote_message(vote_msg),
            PoolVote::External(a) => acc.add_aggregate(a),
        }?;
        let acc = self
            .accumulators
            .get(&vote)
            .expect("the accumulator was created above");
        let cert = self.try_produce_cert(total_stake, vote, completed_certs, acc)?;
        Ok((stake, cert))
    }
```
