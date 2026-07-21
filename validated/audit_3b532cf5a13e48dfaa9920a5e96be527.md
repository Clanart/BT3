### Title
Missing Consensus Vote Signature Verification Allows Forged Quorum and Wrong Block Commitment — (`File: crates/apollo_consensus/src/single_height_consensus.rs`)

---

### Summary

`SingleHeightConsensus::handle_vote` accepts incoming peer votes without verifying the cryptographic signature carried in `Vote::signature`. A single network peer can forge precommit votes attributed to any set of known validators, manufacture a false quorum, and drive `decision_reached` to commit a block that was never actually agreed upon by the legitimate committee.

---

### Finding Description

The `Vote` protobuf message carries a `signature: Hashes` field (field 7 in `consensus.proto`). After deserialization the field is stored as `Vote::signature: RawSignature`. The signing side already has a matching TODO:

```rust
// crates/apollo_consensus/src/state_machine.rs  line 245
// TODO(Asmaa): sign the vote
signature: RawSignature::default(),
```

On the receiving side, `handle_vote` explicitly defers verification:

```rust
// crates/apollo_consensus/src/single_height_consensus.rs  line 252-253
pub(crate) fn handle_vote(&mut self, vote: Vote) -> Requests {
    // TODO(Asmaa): verify the signature
```

The only guards present are:
1. `vote.height == current_height` — trivially satisfied by the attacker.
2. `self.validators.contains(&vote.voter)` — the attacker sets `voter` to any known validator address; no key material is required.
3. Duplicate/conflict detection — one forged vote per validator passes cleanly.

Because neither signing nor verification is implemented, any peer that can reach the consensus broadcast channel can inject votes claiming to originate from any validator in the committee.

The infrastructure for verification already exists: `verify_precommit_vote_signature(block_hash, signature, public_key)` in `crates/apollo_signature_manager/src/signature_manager.rs` and `verify_message_hash_signature` in `crates/starknet_api/src/crypto/utils.rs` are both production-ready. They are simply never called from `handle_vote`.

---

### Impact Explanation

**Broken invariant:** The consensus protocol's safety guarantee rests on the assumption that a vote attributed to validator V was cryptographically produced by V. Without signature verification this invariant is absent.

**Attack path:**

1. Attacker connects to the consensus P2P broadcast channel (no validator key required).
2. For each validator `v_i` in the committee, attacker crafts a `Vote { vote_type: Precommit, height: H, round: R, proposal_commitment: Some(C), voter: v_i, signature: RawSignature::default() }`.
3. Each forged vote passes `validators.contains(&vote.voter)` and the duplicate check (first occurrence per voter).
4. After injecting ≥ quorum forged precommits for commitment `C`, `upon_decision` fires and emits `SMRequest::DecisionReached(Decision { precommits, block: C })`.
5. The manager calls `context.decision_reached(height, round, C, ...)`, which calls `batcher.decision_reached(DecisionReachedInput { proposal_id })`.
6. The batcher commits the block: state diff is written, global root is updated, block hash is calculated and stored — all based on a decision that no legitimate validator actually cast.

The committed block's state root, block hash, and all derived commitments are now wrong relative to what the honest committee would have agreed on, satisfying the **Critical — Wrong state, receipt, event, L1 message, class hash, storage value, or revert result** impact.

---

### Likelihood Explanation

Any peer with access to the consensus P2P layer can execute this attack. No validator private key is needed. The `RawSignature::default()` (empty `Vec<Felt>`) is accepted without error because the verification call is simply absent. The attack requires only knowledge of the current validator set (publicly available) and the ability to send P2P messages.

---

### Recommendation

Implement vote signature verification inside `handle_vote` before the vote is forwarded to the state machine. The `verify_precommit_vote_signature` function in `crates/apollo_signature_manager/src/signature_manager.rs` already implements the correct ECDSA check over the block hash. The corresponding signing TODO in `make_self_vote` must also be resolved so that honest nodes produce verifiable votes. Votes with missing, empty, or invalid signatures must be rejected and the sending peer reported.

---

### Proof of Concept

```
// Attacker code (pseudocode using the existing protobuf types)
let validators = fetch_validator_set(height);   // public information
let commitment  = observe_proposal_commitment(); // watch the P2P stream

for validator in validators.iter().take(quorum_size) {
    let forged_vote = Vote {
        vote_type:           VoteType::Precommit,
        height:              current_height,
        round:               current_round,
        proposal_commitment: Some(commitment),
        voter:               *validator,          // claim to be this validator
        signature:           RawSignature::default(), // empty — never checked
    };
    broadcast_to_consensus_channel(forged_vote);
}
// Result: upon_decision fires → decision_reached → wrong block committed
```

**Relevant code locations:**

- Vote signature field (never verified): [1](#0-0) 
- `handle_vote` missing verification: [2](#0-1) 
- `make_self_vote` missing signing: [3](#0-2) 
- Existing `verify_precommit_vote_signature` (unused here): [4](#0-3) 
- `decision_reached` commits state after false quorum: [5](#0-4) 
- Protobuf deserialization accepts any signature without validation: [6](#0-5)

### Citations

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

**File:** crates/apollo_consensus/src/single_height_consensus.rs (L252-263)
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
```

**File:** crates/apollo_consensus/src/state_machine.rs (L239-247)
```rust
        let vote = Vote {
            vote_type,
            height: self.height,
            round: self.round,
            proposal_commitment,
            voter: self.id,
            // TODO(Asmaa): sign the vote
            signature: RawSignature::default(),
        };
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

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L767-786)
```rust
    async fn decision_reached(
        &mut self,
        height: BlockNumber,
        round: Round,
        commitment: ProposalCommitment,
        wait_for_last_commitment: bool,
    ) -> Result<(), ConsensusError> {
        info!("Finished consensus for height: {height}. Agreed on block: {:#066x}", commitment.0);

        self.interrupt_active_proposal().await;
        let (init, transactions, proposal_id, finished_info) = {
            let mut proposals = self.valid_proposals.lock().unwrap();
            let (init, transactions, proposal_id, finished_info) =
                proposals.get_proposal(&height, &round, &commitment).clone();
            proposals.remove_proposals_below_or_at_height(&height);
            (init, transactions, proposal_id, finished_info)
        };

        let decision_reached_response =
            self.deps.batcher.decision_reached(DecisionReachedInput { proposal_id }).await?;
```

**File:** crates/apollo_protobuf/src/converters/consensus.rs (L86-90)
```rust
        // Convert Hashes to RawSignature (default to empty if None)
        let signature =
            value.signature.map(|hashes| hashes.try_into()).transpose()?.unwrap_or_default();

        Ok(Vote { vote_type, height, round, proposal_commitment, voter, signature })
```
