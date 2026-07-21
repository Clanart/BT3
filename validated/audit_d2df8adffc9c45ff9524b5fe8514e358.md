### Title
Future-Height Proposal Cache Slot Squatting Blocks Legitimate Block Commitments — (`crates/apollo_consensus/src/manager.rs`)

---

### Summary

`ConsensusCache::cache_future_proposal` uses `or_insert` (first-wins) semantics when storing proposals for future heights. Because proposal signature verification is not yet implemented, any network peer can pre-fill a cache slot for a future (height, round) pair by spoofing the expected proposer address. When the node later reaches that height, it processes the attacker's invalid cached proposal, votes Nil, and the legitimate block commitment is never produced for that round. The attacker can saturate up to `future_height_limit` (default 10) future height slots in a single burst.

---

### Finding Description

**Root cause — `or_insert` in `cache_future_proposal`**

In `crates/apollo_consensus/src/manager.rs`, proposals for future heights are stored with `or_insert`, meaning the first arrival for a given `(height, round)` key permanently occupies the slot; every subsequent proposal for the same key is silently discarded: [1](#0-0) 

The code itself acknowledges the consequence: [2](#0-1) 

**Guard is insufficient — no signature verification**

Before caching, `handle_proposal` checks that `init.proposer` equals the committee-derived expected proposer: [3](#0-2) 

`init.proposer` is a plain field in the `ProposalInit` message set by the sender. Because signature verification is not implemented (the TODO is still open): [4](#0-3) 

any peer can craft a `ProposalInit` with `proposer` set to the legitimate proposer's address (public, derived from the committee), pass the identity check, and have their invalid proposal cached.

**Downstream effect — Nil vote, no block commitment**

When the node reaches the poisoned height, `process_start_height` drains the cache and feeds every cached proposal into `handle_proposal_known_block_info`: [5](#0-4) 

The invalid proposal is forwarded to `validate_proposal`, which calls `initiate_validation` → batcher `validate_block`. The batcher rejects the garbage content (wrong block hash, empty stream, or `ProposalFin` mismatch), the `fin_sender` is never resolved with a valid commitment, and the consensus state machine receives `FinishedValidation(None, round, …)`, causing the node to broadcast a Nil prevote. No `ProposalCommitment` (partial block hash) is produced for that round. [6](#0-5) 

**Scope of pre-poisoning**

The default `FutureMsgLimitsConfig` allows caching up to 10 heights ahead, each with 1 round: [7](#0-6) 

A single burst of 10 spoofed `ProposalInit` messages (one per future height, round 0) poisons 10 consecutive block slots. The attacker can repeat the burst continuously to maintain the blockade indefinitely.

---

### Impact Explanation

Each poisoned cache slot causes the affected validator to vote Nil for that round. If enough validators are poisoned, no quorum is reached, no `ThinStateDiff` is committed, and no `PartialBlockHashComponents` / `ProposalCommitment` is written to storage for those heights. Valid user transactions queued in the mempool are never sequenced. This matches the **High** impact category: *Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing* — specifically, valid transactions are permanently excluded from sequencing for the poisoned rounds.

---

### Likelihood Explanation

- **Unprivileged trigger**: any node that can send P2P messages to a validator can execute the attack; no stake, no special role required.
- **Zero marginal cost**: sending a `ProposalInit` with an empty content channel costs only a network message.
- **Repeatable**: the attacker can continuously re-poison slots as they are consumed, sustaining the DoS indefinitely.
- **Committee is public**: the expected proposer for any future (height, round) is deterministically derivable from public committee data.

---

### Recommendation

1. **Implement proposal signature verification** before any caching decision. The existing TODO (`// TODO(Asmaa): verify the signature`) must be resolved before the cache is used in an adversarial network. Only a message cryptographically signed by the expected proposer's key should be admitted.

2. **Replace `or_insert` with `insert`** in `cache_future_proposal` so that a later, legitimate proposal from the real proposer can overwrite an earlier spoofed one. This is a partial mitigation until signatures are enforced.

3. **Require a minimum stake or rate-limit** future-height proposal submissions per peer, analogous to the M-12 mitigation of requiring a minimum fraction holding to start a buyout.

---

### Proof of Concept

```
// Attacker (any peer) executes for i in 1..=10:
//   1. Query committee to find expected_proposer for (current_height + i, round 0).
//   2. Craft ProposalInit { height: current_height+i, round: 0,
//                           proposer: expected_proposer, ... }
//      with an empty / immediately-closed content channel.
//   3. Send the crafted ProposalInit to the target validator node.
//
// Result per target node:
//   - handle_proposal: proposer check passes (init.proposer == expected_proposer).
//   - cache_future_proposal: or_insert stores the invalid proposal; slot is now locked.
//   - When node reaches height current_height+i:
//       process_start_height drains cache → handle_proposal_known_block_info
//       → validate_proposal → batcher rejects (empty/invalid content)
//       → FinishedValidation(None, 0, None) → node broadcasts Nil prevote.
//   - No ProposalCommitment produced; no ThinStateDiff committed for that height.
//   - Attacker repeats burst to maintain blockade.
```

### Citations

**File:** crates/apollo_consensus/src/manager.rs (L286-296)
```rust
    fn cache_future_proposal(
        &mut self,
        init: ProposalInit,
        content_receiver: mpsc::Receiver<ContextT::ProposalPart>,
    ) {
        self.future_proposals_cache
            .entry(init.height)
            .or_default()
            .entry(init.round)
            .or_insert((init, content_receiver));
    }
```

**File:** crates/apollo_consensus/src/manager.rs (L624-630)
```rust
        let cached_proposals = self.cache.get_current_height_proposals(height);
        trace!("Cached proposals for height {}: {:?}", height, cached_proposals);
        for (init, content_receiver) in cached_proposals {
            let new_requests =
                self.handle_proposal_known_block_info(height, shc, init, content_receiver).await;
            pending_requests.extend(new_requests);
        }
```

**File:** crates/apollo_consensus/src/manager.rs (L773-790)
```rust
                let Ok(proposer) =
                    get_proposer_for_height(&self.committee_provider, init.height, init.round)
                        .await
                else {
                    warn!(
                        "VIRTUAL_PROPOSER_LOOKUP_FAILED: Failed to determine virtual proposer for \
                         height {} round {}. Dropping proposal.",
                        init.height.0, init.round
                    );
                    return Ok(VecDeque::new());
                };
                if proposer != init.proposer {
                    warn!(
                        "Invalid proposer for height {} and round {}: expected {:?}, got {:?}",
                        init.height.0, init.round, proposer, init.proposer
                    );
                    return Ok(VecDeque::new());
                }
```

**File:** crates/apollo_consensus/src/manager.rs (L793-803)
```rust
                        debug!("Received a proposal for a future height. {:?}", init);
                        // Note: new proposals with the same height/round will be ignored.
                        //
                        // TODO(matan): This only work for trusted peers. In the case of
                        // possibly malicious peers this is a
                        // possible DoS attack (malicious
                        // users can insert invalid/bad/malicious proposals before
                        // "good" nodes can propose).
                        //
                        // When moving to version 1.0 make sure this is addressed.
                        self.cache.cache_future_proposal(init, content_receiver);
```

**File:** crates/apollo_consensus/src/single_height_consensus.rs (L252-254)
```rust
    pub(crate) fn handle_vote(&mut self, vote: Vote) -> Requests {
        // TODO(Asmaa): verify the signature
        trace!("Received {:?}", vote);
```

**File:** crates/apollo_consensus_orchestrator/src/validate_proposal.rs (L234-241)
```rust

    // TODO(matan): Switch to signature validation.
    if built_block != received_fin.proposal_commitment {
        CONSENSUS_PROPOSAL_FIN_MISMATCH.increment(1);
        return Err(ValidateProposalError::ProposalFinMismatch);
    }

    Ok(built_block)
```

**File:** crates/apollo_consensus_config/src/config.rs (L311-315)
```rust
impl Default for FutureMsgLimitsConfig {
    fn default() -> Self {
        Self { future_height_limit: 10, future_round_limit: 10, future_height_round_limit: 1 }
    }
}
```
