### Title
Unauthenticated Consensus Votes Accepted Without Signature Verification Enable Validator Impersonation and Consensus Liveness Failure - (File: crates/apollo_consensus/src/single_height_consensus.rs)

---

### Summary

`handle_vote` in `SingleHeightConsensus` accepts prevotes and precommits from the network without verifying the cryptographic signature on the `Vote` struct. A single malicious validator can broadcast fake votes that claim to originate from every other validator in the committee, with arbitrary `proposal_commitment` values and invalid/default signatures. The duplicate-vote guard in `received_vote` then permanently blocks each legitimate validator's real vote for that height and round, preventing quorum from ever being reached and halting block production.

---

### Finding Description

`handle_vote` in `crates/apollo_consensus/src/single_height_consensus.rs` contains an explicit unimplemented TODO:

```rust
pub(crate) fn handle_vote(&mut self, vote: Vote) -> Requests {
    // TODO(Asmaa): verify the signature   // ← signature never checked
    ...
    if !self.validators.contains(&vote.voter) { ... }   // only membership check
    match self.state_machine.received_vote(&vote) {
        VoteStatus::Duplicate => return VecDeque::new(),
        VoteStatus::Conflict(...)  => return VecDeque::new(),
        VoteStatus::New => {}
    }
    ...
}
``` [1](#0-0) 

The only guard is a committee-membership check on `vote.voter`. Any network peer can set `vote.voter` to any legitimate validator's `ContractAddress` and broadcast a vote with `signature: RawSignature::default()` (all-zero bytes). The `Vote` struct carries the signature field but it is never read during ingestion: [2](#0-1) 

`received_vote` in the state machine uses `(round, voter)` as the deduplication key. Once a fake vote for validator Y is inserted into `prevotes` or `precommits`, any subsequent real vote from Y for the same round is classified as `Duplicate` or `Conflict` and silently dropped: [3](#0-2) 

The state machine's `handle_prevote` and `handle_precommit` insert directly into the maps keyed by `(round, voter)` with an `assert!(inserted, ...)` that would panic on a second insertion — so the SHC-level conflict guard is the only thing preventing a crash, but it still discards the legitimate vote: [4](#0-3) 

The signing infrastructure exists and is fully functional (`verify_precommit_vote_signature`, `build_precommit_vote_message_digest`) but is simply never called on the inbound path: [5](#0-4) 

Self-votes are also emitted with `signature: RawSignature::default()` because the signing TODO is also open on the outbound path: [6](#0-5) 

---

### Impact Explanation

A single malicious committee member can, at the start of any height/round:

1. Enumerate all validators in the committee.
2. For each validator V, broadcast a `Vote { voter: V, proposal_commitment: Some(evil_hash), signature: default(), ... }` to every peer.
3. Every honest node accepts these fake votes (membership check passes, signature never checked), records them under key `(round, V)`.
4. When the real validator V broadcasts its legitimate vote, every honest node classifies it as `Duplicate` or `Conflict` and discards it.
5. No honest validator can accumulate a 2/3 precommit quorum for the correct `ProposalCommitment`, so `upon_decision` never fires. [7](#0-6) 

The result is a permanent consensus stall for that height. Because the fake votes are already stored in the in-memory state machine maps, the damage persists for the entire height even if the malicious node is subsequently removed from the committee. The sequencer stops producing blocks, matching the "High" impact scope: **mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing**, and more severely the **network not being able to confirm new transactions** impact from the audit scope.

---

### Likelihood Explanation

The attack requires only committee membership (one active validator slot). The `Vote` struct is broadcast over the P2P layer to all peers simultaneously. No special privilege, no key material from other validators, and no timing precision is required — only the ability to send a well-formed protobuf message with an arbitrary `voter` field. The TODO comment confirms this is a known gap, not an oversight buried in complex logic.

---

### Recommendation

In `handle_vote`, before the duplicate check, call `verify_precommit_vote_signature` (for precommits) and the equivalent prevote verifier using the public key associated with `vote.voter` from the committee:

```rust
pub(crate) fn handle_vote(&mut self, vote: Vote) -> Requests {
    // Resolve the public key for vote.voter from the committee.
    let public_key = self.committee.get_public_key(vote.voter)?;
    let block_hash = BlockHash(vote.proposal_commitment?.0);
    if !verify_precommit_vote_signature(block_hash, vote.signature, public_key)? {
        warn!("Dropping vote with invalid signature: {:?}", vote);
        return VecDeque::new();
    }
    ...
}
```

Simultaneously, complete the signing TODO in `make_self_vote` so that outbound votes carry a real ECDSA signature over the `proposal_commitment` / `block_hash`. [8](#0-7) 

---

### Proof of Concept

A malicious validator node, upon receiving any `ProposalInit` for height H round R, iterates the committee and for each validator V ≠ self broadcasts:

```rust
Vote {
    vote_type: VoteType::Prevote,   // repeat for Precommit
    height: H,
    round: R,
    proposal_commitment: Some(ProposalCommitment(Felt::from(0xdeadbeef_u64))),
    voter: V,                        // impersonate V
    signature: RawSignature::default(), // all-zero, never checked
}
```

Each honest peer receives this before V's real vote, inserts it under key `(R, V)`, and subsequently drops V's legitimate vote as `Duplicate`. With all N−1 honest validators' votes poisoned, the quorum threshold is never met, `upon_decision` never fires, and the height never completes.

### Citations

**File:** crates/apollo_consensus/src/single_height_consensus.rs (L252-284)
```rust
    pub(crate) fn handle_vote(&mut self, vote: Vote) -> Requests {
        // TODO(Asmaa): verify the signature
        trace!("Received {:?}", vote);
        let height = self.state_machine.height();
        if vote.height != height {
            warn!("Invalid vote height: expected {:?}, got {:?}", height, vote.height);
            return VecDeque::new();
        }
        if !self.validators.contains(&vote.voter) {
            debug!("Ignoring vote from non validator: vote={:?}", vote);
            return VecDeque::new();
        }

        // Check if vote has already been received.
        match self.state_machine.received_vote(&vote) {
            VoteStatus::Duplicate => {
                // Duplicate - ignore.
                trace_every_n_ms!(
                    DUPLICATE_VOTE_LOG_PERIOD_MS,
                    "Ignoring duplicate vote: {vote:?}"
                );
                return VecDeque::new();
            }
            VoteStatus::Conflict(old_vote, new_vote) => {
                // Conflict - ignore and record.
                warn!("Conflicting votes: old={old_vote:?}, new={new_vote:?}");
                CONSENSUS_CONFLICTING_VOTES.increment(1);
                return VecDeque::new();
            }
            VoteStatus::New => {
                // Vote is new, proceed to process it.
            }
        }
```

**File:** crates/apollo_protobuf/src/consensus.rs (L53-61)
```rust
#[derive(Debug, Default, Hash, Clone, Eq, PartialEq, Serialize, Deserialize)]
pub struct Vote {
    pub vote_type: VoteType,
    pub height: BlockNumber,
    pub round: Round,
    pub proposal_commitment: Option<ProposalCommitment>,
    pub voter: ContractAddress,
    pub signature: RawSignature,
}
```

**File:** crates/apollo_consensus/src/state_machine.rs (L196-232)
```rust
    /// Check if a vote has already been received (either in the vote maps or queued).
    /// Returns the status of the vote: NotReceived, Duplicate, or Conflict.
    pub(crate) fn received_vote(&self, vote: &Vote) -> VoteStatus {
        let determine_status = |old: &Vote, new: &Vote| {
            if old.proposal_commitment == new.proposal_commitment {
                VoteStatus::Duplicate
            } else {
                VoteStatus::Conflict(old.clone(), new.clone())
            }
        };

        // Check Map
        let key = (vote.round, vote.voter);
        let map_entry = match vote.vote_type {
            VoteType::Prevote => self.prevotes.get(&key),
            VoteType::Precommit => self.precommits.get(&key),
        };

        if let Some((old_vote, _)) = map_entry {
            return determine_status(old_vote, vote);
        }

        // Check Queue
        for event in &self.events_queue {
            let queued_vote = match (event, vote.vote_type) {
                (StateMachineEvent::Prevote(v), VoteType::Prevote) => v,
                (StateMachineEvent::Precommit(v), VoteType::Precommit) => v,
                _ => continue,
            };

            if queued_vote.round == vote.round && queued_vote.voter == vote.voter {
                return determine_status(queued_vote, vote);
            }
        }

        VoteStatus::New
    }
```

**File:** crates/apollo_consensus/src/state_machine.rs (L239-246)
```rust
        let vote = Vote {
            vote_type,
            height: self.height,
            round: self.round,
            proposal_commitment,
            voter: self.id,
            // TODO(Asmaa): sign the vote
            signature: RawSignature::default(),
```

**File:** crates/apollo_consensus/src/state_machine.rs (L404-439)
```rust
    // A prevote from a peer node.
    fn handle_prevote(&mut self, vote: Vote) -> VecDeque<SMRequest> {
        let round = vote.round;
        let voter = vote.voter;
        let inserted = self.prevotes.insert((round, voter), (vote, 1)).is_none();
        assert!(
            inserted,
            "SHC should handle conflicts & replays: duplicate prevote for round={round}, \
             voter={voter}",
        );
        self.map_round_to_upons(round)
    }

    fn handle_timeout_prevote(&mut self, round: u32) -> VecDeque<SMRequest> {
        if self.step != Step::Prevote || round != self.round {
            return VecDeque::new();
        };
        debug!("Applying TimeoutPrevote for round={round}.");
        CONSENSUS_TIMEOUTS.increment(1, &[(LABEL_NAME_TIMEOUT_TYPE, TimeoutType::Prevote.into())]);
        let mut output = self.make_self_vote(VoteType::Precommit, None);
        output.append(&mut self.advance_to_step(Step::Precommit));
        output
    }

    // A precommit from a peer node.
    fn handle_precommit(&mut self, vote: Vote) -> VecDeque<SMRequest> {
        let round = vote.round;
        let voter = vote.voter;
        let inserted = self.precommits.insert((round, voter), (vote, 1)).is_none();
        assert!(
            inserted,
            "SHC should handle conflicts & replays: duplicate precommit for round={round}, \
             voter={voter}"
        );
        self.map_round_to_upons(round)
    }
```

**File:** crates/apollo_consensus/src/state_machine.rs (L682-704)
```rust
    fn upon_decision(&mut self, round: u32) -> VecDeque<SMRequest> {
        let Some((Some(proposal_id), _)) = self.proposals.get(&round) else {
            return VecDeque::new();
        };
        if !self.value_has_enough_votes(&self.precommits, round, &Some(*proposal_id), &self.quorum)
        {
            return VecDeque::new();
        }
        if !self.virtual_proposer_in_favor(&self.precommits, round, &Some(*proposal_id)) {
            return VecDeque::new();
        }
        // Collect all supporting precommits for this proposal and round.
        let supporting_precommits: Vec<Vote> = self
            .precommits
            .iter()
            .filter(|(&(r, _voter), (v, _w))| {
                r == round && v.proposal_commitment == Some(*proposal_id)
            })
            .map(|(_vote_key, (v, _w))| v.clone())
            .collect();

        let decision = Decision { precommits: supporting_precommits, block: *proposal_id };
        VecDeque::from([SMRequest::DecisionReached(decision)])
```

**File:** crates/apollo_signature_manager/src/signature_manager.rs (L170-177)
```rust
pub fn verify_precommit_vote_signature(
    block_hash: BlockHash,
    signature: RawSignature,
    public_key: PublicKey,
) -> SignatureVerificationResult<bool> {
    let message_digest = build_precommit_vote_message_digest(block_hash);
    verify_signature(message_digest, signature, public_key)
}
```
