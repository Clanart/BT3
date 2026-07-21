### Title
Missing Vote Signature Verification Allows Any Peer to Forge Committee Votes and Force Wrong Block Commitment - (`crates/apollo_consensus/src/single_height_consensus.rs`)

### Summary

`SingleHeightConsensus::handle_vote` performs only a committee-membership check on incoming votes but never verifies the cryptographic signature. Any network peer can forge prevotes and precommits attributed to any committee member, manufacture a 2/3+ quorum for an attacker-controlled `ProposalCommitment`, and cause the sequencer to commit a wrong block — producing a wrong state root, wrong state diff, and wrong block hash in storage.

### Finding Description

The external ZetaChain bug is an incomplete authorization check: `VoteOnInboundBallot` verifies one condition (not tombstoned) but omits a second required condition (not jailed). The direct analog here is that `handle_vote` verifies one condition (voter is a committee member) but omits the second required condition (the vote carries a valid ECDSA signature from that member's key).

In `crates/apollo_consensus/src/single_height_consensus.rs`, the vote handler is:

```rust
pub(crate) fn handle_vote(&mut self, vote: Vote) -> Requests {
    // TODO(Asmaa): verify the signature          // ← check is absent
    ...
    if !self.committee.members().iter().any(|s| s.address == vote.voter) {
        debug!("Ignoring vote from non validator: vote={:?}", vote);
        return VecDeque::new();
    }
    // proceeds directly to quorum accounting
    self.state_machine.handle_event(sm_vote)
}
``` [1](#0-0) 

The `Vote` protobuf carries a `signature` field:

```proto
Address voter     = 6;
Hashes  signature = 7;
``` [2](#0-1) 

The signing infrastructure exists and is complete. `SignatureManager::sign_precommit_vote` signs `blake2s(PRECOMMIT_VOTE || block_hash)` with ECDSA, and `verify_precommit_vote_signature` verifies it:

```rust
pub fn verify_precommit_vote_signature(
    block_hash: BlockHash,
    signature: RawSignature,
    public_key: PublicKey,
) -> SignatureVerificationResult<bool> { ... }
``` [3](#0-2) 

But this function is never called inside `handle_vote`. Compounding the problem, `make_self_vote` also carries a matching `TODO` and emits votes with `signature: RawSignature::default()`:

```rust
// TODO(Asmaa): sign the vote
signature: RawSignature::default(),
``` [4](#0-3) 

The committee `Staker` set stores only `address` and `weight`; no public key is stored alongside the address in the committee structure used by `handle_vote`. [5](#0-4) 

### Impact Explanation

**Attack path:**

1. A malicious committee member (or any peer that can observe committee addresses) proposes a block. The proposal is validated by peers and stored in `valid_proposals` under `(height, round, commitment)`.
2. The attacker broadcasts forged `Vote` messages — `VoteType::Precommit`, correct `height`/`round`, the target `ProposalCommitment`, `voter` set to each other committee member's address, and `signature` set to any bytes (e.g., all-zero default).
3. `handle_vote` accepts each forged vote: height matches, voter address is in the committee, no signature check is performed.
4. The state machine accumulates weight and fires `DecisionReached` with the attacker's `ProposalCommitment`.
5. `SequencerConsensusContext::decision_reached` calls `batcher.decision_reached`, which calls `commit_proposal_and_block` → writes the wrong `ThinStateDiff` and `PartialBlockHashComponents` to storage, queues a commitment task that computes the wrong global root and block hash. [6](#0-5) 

The wrong block is then propagated to state sync and the cende blob, permanently anchoring the wrong state root. [7](#0-6) 

**Impact scope:** Critical — wrong state root, wrong state diff, wrong block hash committed to storage and propagated to L1 and provers.

### Likelihood Explanation

- Committee member addresses are public (broadcast in `ProposalInit` and derivable from the staking contract).
- The attacker needs only one valid proposal in `valid_proposals` for the target `(height, round)` — achievable by being a committee member or by waiting for any honest proposal to be validated.
- No special network position is required; any peer that can send `Vote` messages to the target node can execute the attack.
- The `RawSignature::default()` used by honest nodes means the wire format already carries all-zero signatures, so forged votes are indistinguishable from honest ones at the transport layer.

### Recommendation

1. Store each committee member's ECDSA public key alongside their address in the `Staker` / `Committee` structure.
2. In `handle_vote`, after the committee-membership check, call `verify_precommit_vote_signature` (or the equivalent for prevotes) using the member's stored public key. Reject votes that fail verification.
3. In `make_self_vote`, call `SignatureManager::sign_precommit_vote` (or a prevote equivalent) to populate the `signature` field before broadcasting.
4. Remove both `TODO(Asmaa)` markers once the above is implemented.

### Proof of Concept

```
Attacker (any network peer):
  committee = {A: addr_A, B: addr_B, C: addr_C}   // public info
  target_commitment = <attacker's proposal commitment stored in valid_proposals>

  for voter in [addr_A, addr_B, addr_C]:
      send Vote {
          vote_type:           Precommit,
          height:              H,
          round:               R,
          proposal_commitment: target_commitment,
          voter:               voter,          // forged identity
          signature:           [0u8; 64],      // any bytes, never checked
      }

  // handle_vote accepts all three:
  //   height == H  ✓
  //   voter in committee  ✓
  //   signature verified  ✗  (TODO, skipped)
  //
  // State machine: weight(A)+weight(B)+weight(C) >= 2/3 total → DecisionReached
  // Batcher: commit_proposal_and_block(H, wrong_state_diff, wrong_partial_block_hash)
  // Storage: wrong global root + wrong block hash written
``` [8](#0-7) [9](#0-8)

### Citations

**File:** crates/apollo_consensus/src/single_height_consensus.rs (L241-281)
```rust
    pub(crate) fn handle_vote(&mut self, vote: Vote) -> Requests {
        // TODO(Asmaa): verify the signature
        trace!("Received {:?}", vote);
        let height = self.state_machine.height();
        if vote.height != height {
            warn!("Invalid vote height: expected {:?}, got {:?}", height, vote.height);
            return VecDeque::new();
        }
        if !self.committee.members().iter().any(|s| s.address == vote.voter) {
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

        info!("Accepting {:?}", vote);
        let sm_vote = match vote.vote_type {
            VoteType::Prevote => StateMachineEvent::Prevote(vote),
            VoteType::Precommit => StateMachineEvent::Precommit(vote),
        };
        self.state_machine.handle_event(sm_vote)
    }
```

**File:** crates/apollo_protobuf/src/proto/p2p/proto/consensus/consensus.proto (L34-36)
```text
    Address       voter               = 6;
    Hashes        signature           = 7;
}
```

**File:** crates/apollo_signature_manager/src/signature_manager.rs (L68-82)
```rust
    pub async fn sign_precommit_vote(
        &self,
        block_hash: BlockHash,
    ) -> SignatureManagerResult<RawSignature> {
        let message_digest = build_precommit_vote_message_digest(block_hash);
        self.sign(message_digest).await
    }

    async fn sign(&self, message_digest: MessageDigest) -> SignatureManagerResult<RawSignature> {
        let private_key = self.keystore.get_key().await?;
        let signature = ecdsa_sign(&private_key, &message_digest)
            .map_err(|e| SignatureManagerError::Sign(e.to_string()))?;

        Ok(signature.into())
    }
```

**File:** crates/apollo_signature_manager/src/signature_manager.rs (L179-186)
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

**File:** crates/apollo_consensus/src/state_machine.rs (L254-256)
```rust
            // TODO(Asmaa): sign the vote
            signature: RawSignature::default(),
        };
```

**File:** crates/apollo_staking/src/committee_provider.rs (L74-78)
```rust
pub trait CommitteeTrait: Send + Sync {
    /// Returns a reference to the committee members.
    fn members(&self) -> &StakerSet;

    /// Returns the address of the proposer for the specified height and round.
```

**File:** crates/apollo_batcher/src/batcher.rs (L966-974)
```rust
        self.commit_proposal_and_block(
            height,
            state_diff.clone(),
            block_execution_artifacts.address_to_nonce(),
            block_execution_artifacts.execution_data.consumed_l1_handler_tx_hashes,
            block_execution_artifacts.execution_data.rejected_tx_hashes,
            StorageCommitmentBlockHash::Partial(partial_block_hash_components),
        )
        .await?;
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L989-1024)
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

        // CRITICAL: The block is now committed. This function must not fail beyond this point
        // unless the state is fully reverted, otherwise the node will be left in an
        // inconsistent state.

        self.finalize_decision(
            height,
            &init,
            commitment,
            transactions,
            decision_reached_response,
            finished_info.block_header_commitments.clone(),
            finished_info.l2_gas_used,
            wait_for_last_commitment,
        )
        .await;
```
