## Title
Missing per-rank overlap check in `AggregateAccumulator::add_aggregate` allows stake double-counting when aggregating externally-sourced `VoteAggregate`s toward reward/consensus certificates - (File: `votor/src/aggregate_accumulator.rs`)

### Summary
The external report describes `bigclaim()` accepting a "multi-signature" array without ensuring the signers are distinct, letting one signer's stake (signature power) be counted multiple times toward a stake/quorum threshold. The closest analog in this codebase is `AggregateAccumulator::add_aggregate`, which merges an incoming `VoteAggregate`'s stake into a running total via `saturating_add` while only OR-ing the rank bitmap, rather than checking whether any of the incoming ranks are already accounted for.

### Finding Description
`AggregateAccumulator` tracks a running set of validator ranks (`ranks: BitVec<u8>`) and an accumulated `stake: u64` used to decide whether a BLS certificate threshold has been reached [1](#0-0) .

Two update paths exist:
- `add_own_vote_message`, used for locally-produced votes, explicitly guards against re-adding an already-set rank and returns `AggregateAccumulatorError::Duplicate` if `self.ranks.replace(...)` reports the bit was already set [2](#0-1) .
- `add_aggregate`, used for externally-supplied `VoteAggregate`s, has no such check. It aggregates the BLS signature, OR-merges the rank bitmap (`self.ranks |= aggregate.ranks();`), and unconditionally adds `aggregate.stake()` to the running total with `saturating_add`, with no verification that the newly-merged ranks are disjoint from ranks already present in `self.ranks` [3](#0-2) .

Because the bitmap union is idempotent (re-setting an already-set bit is a no-op) but the stake accumulator is a raw arithmetic sum, any two `VoteAggregate`s that share one or more ranks will cause that shared validator's stake to be counted once per aggregate it appears in, even though the bitmap correctly reflects only one bit per rank. This is structurally the same broken invariant as the reported issue: a threshold check (`>= 60%`/`>=80%` stake, or `m`-of-`n` signatures) is satisfied using a count that does not correspond to distinct participants.

This function is invoked from `AggregateAccumulator::add_aggregate`'s only two call sites: `PartialCert::add_aggregate` (used to build reward certificates) [4](#0-3)  and `VotePool::add_pool_vote` (used to build consensus certificates such as `Notarize`/`Finalize`/`Skip`) [5](#0-4) . Both paths feed `PoolVote::External`/`RewardInput::External` aggregates that ultimately originate from the network (via `bls_sigverifier`/gossip).

I was only able to partially trace the upstream dedup guarantees within the available tool budget. In the consensus-pool path (`bls-sigverify/src/vote_pool.rs`'s `SlotEntry::try_add_vote`), a validator's own sig-verifier locally deduplicates per-rank votes before constructing a `VoteAggregate` [6](#0-5) , which appears to prevent a duplicate/overlapping rank from reaching `add_aggregate` for that specific pipeline. I was not able to fully confirm the equivalent guarantee for the rewards pipeline (`RewardInput::External`, populated in `core/src/block_creation_loop/rewards/certs_builder.rs`), i.e., whether every `VoteAggregate` entering `PartialCert::add_aggregate` is guaranteed to have ranks disjoint from all previously-accumulated ranks for that slot/vote before this call. That verification would require inspecting `bls-sigverify/src/rewards.rs` and `core/src/block_creation_loop/rewards/reward_certs_service.rs`, which I did not get to examine before the iteration limit.

### Impact Explanation
If the rewards pipeline (or any other future caller of `add_aggregate`) can present two `VoteAggregate`s whose rank bitmaps overlap without being rejected upstream, a validator's stake would be double-counted in `AggregateAccumulator.stake`. Since reward-certificate/consensus-certificate construction gates on stake fraction of `total_stake` (`Fraction::new(self.stake, total_stake) >= threshold`) [7](#0-6) , this could let a certificate be produced/accepted with less real, distinct stake participation than the protocol threshold requires — a false-acceptance condition analogous to the "single signer satisfies a multi-signer threshold" bug class. Depending on which caller is affected, this ranges from reward-certificate mis-accounting (fund impact) to consensus/certificate quorum mis-accounting.

### Likelihood Explanation
Likelihood is uncertain because I could not confirm, within the tool budget, whether an unprivileged/network-observable path exists that can actually deliver two overlapping-rank `VoteAggregate`s to `add_aggregate` without being filtered earlier (e.g., by `SlotEntry::try_add_vote`'s per-rank dedup in the consensus-vote pipeline). For the consensus-pool call site, the evidence found suggests upstream dedup already prevents this. For the rewards call site, the guarantee is unverified.

### Recommendation
Add an explicit disjointness check in `AggregateAccumulator::add_aggregate`, mirroring `add_own_vote_message`'s pattern: before/while OR-ing in the new ranks, detect any bit that is already set in `self.ranks` and return `AggregateAccumulatorError::Duplicate` (or a new `Overlap` variant) instead of silently accepting the aggregate and adding its stake. This removes the reliance on upstream callers to guarantee disjoint ranks and makes the invariant "stake is only counted once per rank" hold unconditionally at the point where stake is accumulated.

### Proof of Concept
Not able to construct a concrete end-to-end network PoC within the remaining iteration budget — this would require confirming the exact upstream call path (`bls-sigverify/src/rewards.rs`, `reward_certs_service.rs`) that produces `RewardInput::External` aggregates, and whether attacker-influenced or duplicate/retransmitted `VoteAggregate`s could reach `PartialCert::add_aggregate` with overlapping ranks. A minimal unit-level demonstration of the missing check itself:

```rust
// votor/src/aggregate_accumulator.rs
let mut acc = AggregateAccumulator::new(max_validators);
acc.add_aggregate(&aggregate_a)?; // ranks = {5}, stake += stake_of_rank_5
acc.add_aggregate(&aggregate_b)?; // aggregate_b.ranks() also contains {5}
// No error is raised; acc.stake now double-counts rank 5's stake,
// while acc.ranks (bitmap) still correctly shows rank 5 set only once.
```

Given the uncertainty around whether this is reachable by an unprivileged/network attacker in the current call graph, this should be treated as a **hardening recommendation with unconfirmed exploitability** rather than a fully confirmed vulnerability.

### Citations

**File:** votor/src/aggregate_accumulator.rs (L38-54)
```rust
#[derive(Debug, Clone)]
/// Accumulates [`VoteAggregate`]s and then can build [`Certificate`] from them.
pub struct AggregateAccumulator {
    ranks: BitVec<u8>,
    signature: SignatureProjective,
    stake: u64,
}

impl AggregateAccumulator {
    /// Constructs a new accumulator.
    pub fn new(max_validators: usize) -> Self {
        Self {
            ranks: default_bitvec(max_validators),
            signature: SignatureProjective::identity(),
            stake: 0,
        }
    }
```

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

**File:** core/src/block_creation_loop/rewards/certs_builder/entry/partial_cert.rs (L40-49)
```rust
    /// Accumulates a new observed vote aggregate from another validator.
    pub(super) fn add_aggregate(
        &mut self,
        aggregate: VoteAggregate,
        mut vote_account_pubkeys: Vec<Pubkey>,
    ) -> Result<(), AggregateAccumulatorError> {
        self.accumulator.add_aggregate(&aggregate)?;
        self.validators.append(&mut vote_account_pubkeys);
        Ok(())
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

**File:** bls-sigverify/src/vote_pool.rs (L45-90)
```rust
    fn try_add_vote(
        &mut self,
        msg: &UnverifiedVoteMessage,
        rank: usize,
        max_validators: usize,
    ) -> Result<(), VotePoolError> {
        debug_assert!(rank < max_validators);
        match &msg.vote {
            Vote::Skip(_) => {
                if self.notar[rank].is_some()
                    || self.finalize[rank]
                    || self.skip_fallback[rank]
                    || self.genesis[rank].is_some()
                {
                    return Err(VotePoolError::Invalid);
                }
                if self.skip.replace(rank, true) {
                    Err(VotePoolError::Duplicate)
                } else {
                    Ok(())
                }
            }
            Vote::SkipFallback(_) => {
                if self.finalize[rank] || self.skip[rank] || self.genesis[rank].is_some() {
                    return Err(VotePoolError::Invalid);
                }
                if self.skip_fallback.replace(rank, true) {
                    Err(VotePoolError::Duplicate)
                } else {
                    Ok(())
                }
            }
            Vote::Finalize(_) => {
                if self.skip[rank]
                    || self.skip_fallback[rank]
                    || !self.notar_fallback[rank].is_empty()
                    || self.genesis[rank].is_some()
                {
                    return Err(VotePoolError::Invalid);
                }
                if self.finalize.replace(rank, true) {
                    Err(VotePoolError::Duplicate)
                } else {
                    Ok(())
                }
            }
```
