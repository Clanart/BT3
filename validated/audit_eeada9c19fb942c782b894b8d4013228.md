## Title
Ancestor-hashes repair reaches "agreement" from spoofable, unauthenticated UDP responses keyed only by source address, with no signature or stake weighting — (File: `core/src/repair/duplicate_repair_status.rs`)

### Summary
`PriceUpdates::verify` was flagged because it accepted an oracle update as valid without checking that a *minimum threshold* of distinct, verified guardians actually signed it — a single approving guardian sufficed. The structurally equivalent pattern in Agave is `AncestorRequestStatus::add_response` in `core/src/repair/duplicate_repair_status.rs`, which decides that a set of ancestor slot/hash pairs is "duplicate confirmed" once `MINIMUM_ANCESTOR_AGREEMENT_SIZE` *responses* have been seen — but "responses" are deduplicated purely by UDP source `SocketAddr`, and the response payload (`AncestorHashesResponse::Hashes`) carries no per-validator signature that is checked against that address. There is no stake-weighting and no cryptographic guardian-style identity check, unlike the Pyth `guardian_set` check that at least validates each signer is a member of a known, admin-configured set.

### Finding Description
`add_response` records a response keyed by `from_addr: &SocketAddr`: [1](#0-0) 

Once `validators_with_same_response.len() == get_minimum_ancestor_agreement_size().min(self.sampled_validators.len())`, the code finalizes a `DuplicateAncestorDecision` and treats the agreed-upon ancestor `(Slot, Hash)` list as ground truth for repairing/dumping forks: [2](#0-1) 

The comment above the sample-size constant explicitly documents the security assumption: it treats "agreement from N of 21 *sampled peers*" as statistically unlikely to be malicious, based on a binomial model of *independent, distinct* validators: [3](#0-2) 

That statistical argument depends on the responses genuinely coming from `ANCESTOR_HASH_REPAIR_SAMPLE_SIZE` *distinct validators*, i.e., that the identity used to de-duplicate responses (the `SocketAddr`) reliably corresponds to a unique validator that actually produced the payload. However, the response handler in `verify_and_process_ancestor_response` only validates: (a) that the packet deserializes, (b) that there's no trailing garbage, and (c) that the nonce matches an outstanding request: [4](#0-3) 

Nowhere in this path is the `AncestorHashesResponse::Hashes` payload signed by the responding validator's identity keypair or verified against a pubkey bound to `from_addr`. Repair traffic runs over UDP, so `from_addr` is attacker-controllable (source-IP spoofing over UDP is not authenticated at the transport layer), and the "sampled validators" set is itself just a list of `SocketAddr`s taken from cluster info / gossip contact info (an unprivileged, self-reported value). This breaks the exact invariant the guardian-threshold report was about: the "threshold of unique, authenticated participants" is checked only against an unauthenticated address, not against a verified identity, so one attacker capable of sending several distinctly-addressed (spoofed-source) UDP packets can satisfy `MINIMUM_ANCESTOR_AGREEMENT_SIZE` alone — mirroring how a single Pyth guardian could satisfy `PriceUpdates::verify` before the fix required checking uniqueness and a real 2/3 threshold of *verified* signers.

### Impact Explanation
If an attacker can supply `MINIMUM_ANCESTOR_AGREEMENT_SIZE` fabricated, mutually-consistent `(Slot, Hash)` responses under distinct spoofed source addresses matching a subset of the node's `sampled_validators`, `handle_sampled_validators_reached_agreement` will treat the attacker-supplied ancestor chain as duplicate-confirmed truth. This can steer the victim's `ReplayStage` into dumping a locally-held correct fork and requesting repair toward a fabricated ancestor `(Slot, Hash)` pair chosen by the attacker (via `AncestorRequestDecision::slot_to_repair`), i.e., false rooting/acceptance of an attacker-chosen chain state — a consensus-safety-relevant fault (false-execution/false-rooting class) rather than a simple crash.

### Likelihood Explanation
Exploitability requires: (1) knowing which `SocketAddr`s were selected as `sampled_validators` for a given ancestor-hashes request (these come from the requester's own `cluster_slots`/serve-repair node selection, not secret, but the requester picks them, so the attacker would need to guess or observe the request itself — repair requests are sent over the network and can potentially be observed), and (2) being able to spoof UDP source addresses to match those samples on the network path to the victim, which is feasible for many network positions (no additional cryptographic secret is required since UDP source addresses are unauthenticated). This makes the attack non-trivial but plausible for a well-positioned network attacker, and it does not require a malicious validator/leader — it only requires unauthenticated packet injection, consistent with the "unprivileged" scope.

### Recommendation
Bind each ancestor-hashes response to a signed identity: require the response to include (or be wrapped in) the responding validator's signature over the payload/nonce, verifiable against the pubkey associated with the sampled validator's `SocketAddr`/node-id used in `sampled_validators`, so `add_response` can de-duplicate and count agreement by verified pubkey rather than raw `SocketAddr`. Optionally weight agreement by stake (as is already done elsewhere in the repair path, e.g. `DUPLICATE_THRESHOLD` in `repair_weight.rs`) rather than a flat count of unauthenticated addresses, closing the gap between the "N-of-M distinct honest validators" statistical assumption and what is actually verified.

### Proof of Concept
Conceptual (network-level) PoC, not runnable purely from source review:
1. Node `V` sends `AncestorHashes(slot)` repair requests to `ANCESTOR_HASH_REPAIR_SAMPLE_SIZE` peers selected from `cluster_slots`, recorded in `AncestorRequestStatus.sampled_validators` keyed by `SocketAddr`.
2. Attacker `A`, positioned to spoof source addresses toward `V` (or simply co-located with enough of the real sampled validators' address space knowledge), crafts `get_minimum_ancestor_agreement_size()` UDP packets each carrying an identical, attacker-chosen `AncestorHashesResponse::Hashes(fake_slot_hash_chain)` payload plus a valid outstanding nonce, spoofing the source address to match distinct entries in `sampled_validators`.
3. `verify_and_process_ancestor_response` accepts each packet (nonce matches, no signature check on the payload) and calls `add_response`, which records them as responses from distinct "validators" since de-duplication is by `SocketAddr` only: [5](#0-4) .
4. Once the count reaches `get_minimum_ancestor_agreement_size()`, `handle_sampled_validators_reached_agreement` runs on the fabricated `agreed_response`, potentially yielding `DuplicateAncestorDecision::EarliestMismatchFound`/repair instructions derived entirely from attacker data: [2](#0-1) .

Note: I was not able to fully trace the outstanding-request/nonce validation code (`OutstandingAncestorHashesRepairs::register_response`) or confirm definitively whether any additional signature/identity binding exists elsewhere in the repair transport layer that might mitigate this before it reaches `add_response`; a Devin session with full repo access should verify `core/src/repair/outstanding_requests.rs` (or equivalent) and `serve_repair.rs`'s packet-signing conventions for repair responses to close out this uncertainty.

### Citations

**File:** core/src/repair/duplicate_repair_status.rs (L14-31)
```rust
// Number of validators to sample for the ancestor repair
// We use static to enable tests from having to spin up 21 validators
static ANCESTOR_HASH_REPAIR_SAMPLE_SIZE: AtomicUsize = AtomicUsize::new(21);

pub fn get_ancestor_hash_repair_sample_size() -> usize {
    ANCESTOR_HASH_REPAIR_SAMPLE_SIZE.load(Ordering::Relaxed)
}

pub fn set_ancestor_hash_repair_sample_size_for_tests_only(sample_size: usize) {
    ANCESTOR_HASH_REPAIR_SAMPLE_SIZE.store(sample_size, Ordering::Relaxed);
}

// Even assuming 20% of validators malicious, the chance that >= 11 of the
// ANCESTOR_HASH_REPAIR_SAMPLE_SIZE = 21 validators is malicious is roughly 1/1000.
// Assuming we send a separate sample every 5 seconds, that's once every hour.

// On the other hand with a 52-48 split of validators with one version of the block vs
// another, the chance of >= 11 of the 21 sampled being from the 52% portion is
```

**File:** core/src/repair/duplicate_repair_status.rs (L203-223)
```rust
    pub fn add_response(
        &mut self,
        from_addr: &SocketAddr,
        response_slot_hashes: Vec<(Slot, Hash)>,
        blockstore: &Blockstore,
    ) -> Option<DuplicateAncestorDecision> {
        // If this is not a response from one of the sampled validators, return.
        let did_get_response = self.sampled_validators.get_mut(from_addr)?;
        if *did_get_response {
            // If we've already received a response from this validator, return.
            return None;
        }
        // Mark we got a response from this validator already
        *did_get_response = true;
        self.num_responses += 1;

        let validators_with_same_response = self
            .ancestor_request_responses
            .entry(response_slot_hashes.clone())
            .or_default();
        validators_with_same_response.push(*from_addr);
```

**File:** core/src/repair/duplicate_repair_status.rs (L225-251)
```rust
        // If we got enough of the sampled validators to respond, we are confident
        // this is the correct set of ancestors
        if validators_with_same_response.len()
            == get_minimum_ancestor_agreement_size().min(self.sampled_validators.len())
        {
            // When we reach MINIMUM_ANCESTOR_AGREEMENT_SIZE of the same responses,
            // check for mismatches.
            return Some(
                self.handle_sampled_validators_reached_agreement(blockstore, response_slot_hashes),
            );
        }

        // If everyone responded and we still haven't agreed upon a set of
        // ancestors, that means there was a lot of disagreement and we sampled
        // a bad set of validators.
        if self.num_responses
            == get_ancestor_hash_repair_sample_size().min(self.sampled_validators.len())
        {
            info!(
                "{} return invalid sample no agreement",
                self.requested_mismatched_slot
            );
            return Some(DuplicateAncestorDecision::InvalidSample);
        }

        None
    }
```

**File:** core/src/repair/ancestor_hashes_service.rs (L378-424)
```rust
        let mut cursor = Cursor::new(packet_data);
        let Ok(response) = wincode::config::deserialize_from(&mut cursor, packet_config()) else {
            stats.invalid_packets += 1;
            return None;
        };

        match response {
            AncestorHashesResponse::Hashes(ref hashes) => {
                // deserialize trailing nonce
                let Ok(nonce) = wincode::config::deserialize_from(&mut cursor, packet_config())
                else {
                    stats.invalid_packets += 1;
                    return None;
                };

                // verify that packet does not contain extraneous data
                if cursor.bytes().next().is_some() {
                    stats.invalid_packets += 1;
                    return None;
                }

                let request_slot = outstanding_requests.write().unwrap().register_response(
                    nonce,
                    &response,
                    timestamp(),
                    // If the response is valid, return the slot the request
                    // was for
                    |ancestor_hashes_request| ancestor_hashes_request.0,
                );

                if request_slot.is_none() {
                    stats.invalid_packets += 1;
                    return None;
                }

                // If was a valid response, there must be a valid `request_slot`
                let request_slot = request_slot.unwrap();
                stats.processed += 1;

                if let Occupied(mut ancestor_hashes_status_ref) =
                    ancestor_hashes_request_statuses.entry(request_slot)
                {
                    let decision = ancestor_hashes_status_ref.get_mut().add_response(
                        &from_addr,
                        hashes.clone(),
                        blockstore,
                    );
```
