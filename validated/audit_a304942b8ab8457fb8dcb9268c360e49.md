### Title
Duplicate/overlapping external `VoteAggregate` stake is double-counted toward Alpenglow certificate thresholds in `AggregateAccumulator::add_aggregate` - (File: `votor/src/aggregate_accumulator.rs`)

### Summary
`AggregateAccumulator::add_aggregate` accumulates the `stake` field of an incoming `VoteAggregate` unconditionally, without verifying that the validator ranks it represents haven't already been counted in the accumulator. This mirrors the SEDA `Secp256k1ProverV1.postBatch` bug class: voting power/stake is summed from attacker- or network-supplied inputs with no uniqueness check against what has already contributed to the total, while the sibling function `add_own_vote_message` in the same struct explicitly performs this dedup check and rejects duplicates.

### Finding Description
`AggregateAccumulator` tracks three pieces of state per vote type: a `ranks` bitmap of which validators have voted, an aggregated BLS `signature`, and a running `stake` total used to test consensus thresholds. [1](#0-0) 

For votes generated locally, `add_own_vote_message` correctly guards against double counting by using `BitVec::replace` to atomically check-and-set the rank bit, returning `AggregateAccumulatorError::Duplicate` if the rank was already set, before adding `stake`: [2](#0-1) 

However, `add_aggregate` — the path used for `VoteAggregate`s received from other validators over the network (`PoolVote::External`) — has no equivalent check. It ORs the incoming `ranks` bitmap into the accumulator's `ranks` (which is naturally idempotent for the bitmap) but adds `aggregate.stake()` to `self.stake` unconditionally, regardless of whether some or all of those ranks were already counted: [3](#0-2) 

This is called from `VotePool::add_pool_vote`, which routes `PoolVote::External(a)` messages straight into `acc.add_aggregate(a)`: [4](#0-3) 

`ConsensusPool::add_pool_vote` then feeds the running `entry_stake` (derived from this possibly double-counted total) into `try_produce_cert`, which calls `try_build_base2_cert`/`try_build_base3_cert`. Those functions compute `observed_fraction = Fraction::new(self.stake, total_stake)` and compare it directly against the certificate's stake threshold to decide whether to mint a `Certificate` (Notarize/Finalize/Skip/etc.): [5](#0-4) 

Because `stake` is a plain counter decoupled from the `ranks` bitmap (the bitmap can't overflow past `true`, but the `u64` stake counter can be incremented repeatedly for the same bit), any code path that delivers the same `VoteAggregate` — or two different `VoteAggregate`s whose `ranks` overlap — more than once to `add_aggregate` for the same `Vote` key inflates `self.stake` beyond what is backed by unique validator signatures. The threshold check in `try_build_base2_cert`/`try_build_base3_cert` will then pass with `observed_fraction` computed from inflated (double-counted) stake, exactly the same broken invariant as the SEDA `votingPower += validatorProofs[i].votingPower` loop that lacked a "seen validator" guard.

I was not able to fully trace, within the code visible to me, whether an upstream layer (e.g. the BLS sigverifier or `consensus_pool_service`) deduplicates `VoteAggregate`s by hash/rank-set before they reach `VotePool::add_pool_vote`; no such dedup check is present in `consensus_pool.rs`, `consensus_pool_service.rs` matches I inspected, or in `vote_pool.rs` itself. Given `add_own_vote_message` needed an explicit duplicate check "due to nodes restarting or failover, etc.", it is reasonable to conclude duplicates of externally-sourced aggregates are also a normal occurrence (retransmission, multiple relaying peers, restart-replay), and `add_aggregate` provides no equivalent protection.

### Impact Explanation
If the same (or overlapping-rank) `VoteAggregate` for a given `Vote` is processed more than once by `add_aggregate`, the accumulated `stake` total used for Alpenglow certificate generation (`Notarize`, `NotarizeFallback`, `Finalize`, `FinalizeFast`, `Skip`, `Genesis`) can cross the consensus threshold without genuine unique-validator support. This directly threatens false acceptance/false finalization of blocks or skip certificates — i.e., consensus integrity — which falls squarely in the "false execution/rooting/acceptance" and "consensus halt" impact categories for this scan.

### Likelihood Explanation
The trigger does not require a malicious validator: ordinary network duplication (multiple peers relaying the same `VoteAggregate`, restart/replay of previously sent aggregates as acknowledged in the `add_own_vote_message` doc comment) is enough to reach `add_aggregate` more than once for the same rank set, given no dedup guard exists at that call site. This makes the likelihood of at least partial double-counting under normal operating conditions non-negligible, though I could not verify from the visible code whether some other layer (sigverifier cache, per-peer received-cache) fully suppresses this before it reaches the accumulator — that would need runtime verification.

### Recommendation
Mirror the guard already present in `add_own_vote_message`: before adding `aggregate.stake()` to `self.stake` in `add_aggregate`, check whether any bit in `aggregate.ranks()` overlaps with the existing `self.ranks` (e.g., `if (self.ranks.clone() & aggregate.ranks()).any() { return Err(AggregateAccumulatorError::Duplicate); }`), or otherwise recompute/verify `stake` strictly from the final OR'd `ranks` bitmap rather than accumulating it as an independent running counter.

### Proof of Concept
Conceptual PoC (unit-test style) demonstrating the double count:
1. Construct an `AggregateAccumulator::new(max_validators)`.
2. Build a `VoteAggregate` for rank `r` with `stake = S` via `VoteAggregate::new_from_verified_vote`.
3. Call `acc.add_aggregate(&aggregate)` — `stake()` becomes `S`, `ranks[r] = true`.
4. Call `acc.add_aggregate(&aggregate)` again with the exact same aggregate (simulating a retransmitted/duplicated network message) — no error is returned, and `stake()` becomes `2S`, while `ranks[r]` remains `true` (unchanged).
5. Compare against `add_own_vote_message` called twice with the same `VoteMessage` for the same rank: the second call returns `Err(AggregateAccumulatorError::Duplicate)` and `stake` stays at `S`.
6. This shows `add_aggregate` allows exactly the double-counting `add_own_vote_message` was written to prevent, and that inflated `stake` value feeds directly into `try_build_base2_cert`'s/`try_build_base3_cert`'s `observed_fraction` threshold comparison used to mint consensus certificates. [6](#0-5)

### Citations

**File:** votor/src/aggregate_accumulator.rs (L38-44)
```rust
#[derive(Debug, Clone)]
/// Accumulates [`VoteAggregate`]s and then can build [`Certificate`] from them.
pub struct AggregateAccumulator {
    ranks: BitVec<u8>,
    signature: SignatureProjective,
    stake: u64,
}
```

**File:** votor/src/aggregate_accumulator.rs (L56-86)
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
