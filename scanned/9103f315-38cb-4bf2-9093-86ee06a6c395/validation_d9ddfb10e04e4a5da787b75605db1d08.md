## Analysis

Confirmed: `AncestorRequestStatus::add_response` in `duplicate_repair_status.rs` dedupes responses by `SocketAddr` only, and `AncestorHashesResponse::Hashes` packets carry no signature verification (unlike the `Ping` variant, which calls `ping.verify()`). [1](#0-0) [2](#0-1) [3](#0-2) 

The only gate before a response is admitted is `outstanding_requests.register_response(nonce, ...)`, which validates a random `u32` nonce against an LRU cache — it performs no check that the response came from the socket address the request was actually sent to. [4](#0-3) 

### Title
Unauthenticated `AncestorHashesResponse::Hashes` allows nonce-replay/spoofing to forge distinct-validator agreement in ancestor-hashes repair - (File: core/src/repair/duplicate_repair_status.rs, core/src/repair/ancestor_hashes_service.rs, core/src/repair/outstanding_requests.rs)

### Summary
`AncestorRequestStatus::add_response` counts "distinct validator agreement" purely by the packet's `SocketAddr`, and the ancestor-hashes response path performs no signature verification on the `Hashes` payload (only `Ping` responses are verified). Because UDP allows source-address spoofing and the nonce used to admit a response is a bare `u32` with no cryptographic binding to the intended responder, an attacker who learns/guesses one outstanding nonce can inject forged `Hashes` responses appearing to originate from several of the `sampled_validators` addresses, satisfying `MINIMUM_ANCESTOR_AGREEMENT_SIZE` with attacker-chosen ancestor hashes.

### Finding Description
`AncestorRequestStatus` samples a fixed number of validator socket addresses (`sampled_validators: HashMap<SocketAddr, bool>`) for a given dead/pruned slot and requires `MINIMUM_ANCESTOR_AGREEMENT_SIZE` *distinct* addresses to report the same `(Slot, Hash)` chain before it finalizes a `DuplicateAncestorDecision` (analogous to the delegate report's `tallyProposalResult`, which required distinct delegate addresses to reach a majority). [5](#0-4) [6](#0-5) 

Uniqueness is enforced only against `from_addr` — the socket address read from packet metadata, i.e. `packet.meta().socket_addr()`, which is the UDP-reported source address, not a cryptographically-verified validator identity: [7](#0-6) 

The `AncestorHashesResponse::Hashes(...)` branch of `verify_and_process_ancestor_response` never validates a signature on the response body; it only deserializes the payload and the trailing nonce, then calls `outstanding_requests.register_response(nonce, ...)`. Compare with the `Ping` branch just below it, which explicitly calls `ping.verify()` — showing the codebase's own signature-check pattern is absent for `Hashes` responses. [8](#0-7) 

`register_response` itself only checks: (1) the nonce exists and hasn't expired, and (2) `status.request.verify_response(response)` — for `AncestorHashesRepairType`, this call chain does not bind the response to a specific sender address either; the acceptance test is agnostic of `from_addr`. [4](#0-3) 

Because the nonce is a random `u32` sent unencrypted inside the original request to each of the sampled validators, any of those (or an eavesdropper on the path, or, more simply, one of the actual sampled validators who receives a legitimate request and also learns the nonce) can craft multiple UDP packets with that nonce and spoofed source addresses matching other entries in `sampled_validators`, since UDP has no source-address authentication. Each spoofed packet is treated as an independent "distinct validator" response by `add_response`, inflating `ancestor_request_responses` and `num_responses` toward `MINIMUM_ANCESTOR_AGREEMENT_SIZE`/`get_ancestor_hash_repair_sample_size()` without needing that many real, honest validators to actually agree. [9](#0-8) 

This mirrors the report's core defect exactly: the "voter list" (`sampled_validators`) is deduplicated on an attacker-controllable identifier (socket address, spoofable over UDP) rather than an authenticated identity (a signed validator pubkey), so one entity can be counted as several "delegates" toward the threshold that decides whether a repair/rollback action executes.

### Impact Explanation
If an attacker can force `handle_sampled_validators_reached_agreement` to fire with attacker-chosen `agreed_response`, `AncestorRequestDecision::EarliestMismatchFound(...)` (or `EarliestPrunedMismatchFound`/`ContinueSearch`) is returned and forwarded via `ancestor_duplicate_slots_sender` to ReplayStage, driving it to dump/replay a slot based on the forged "cluster-agreed" ancestor hash chain rather than genuine multi-validator consensus. [10](#0-9) 

This is a false-acceptance-of-consensus-state bug in the repair subsystem: it can cause a validator to dump a correct fork or treat an attacker's chosen ancestor hash as duplicate-confirmed, which is precisely the "false execution/acceptance" impact class referenced in the report's fund-safety/consensus concerns, mapped here onto Agave's repair protocol rather than a voting contract.

### Likelihood Explanation
Exploitation requires: (a) UDP source-address spoofing capability toward the requester's `ancestor_hashes_request_socket`, which is a standard network-layer assumption for this repair protocol (no additional per-packet authentication exists for `Hashes` responses, unlike `Ping`), and (b) knowledge of the outstanding nonce, which is trivially available to any of the sampled validators that legitimately received the request (the nonce is embedded in the request sent to them) or an on-path observer. No privileged validator/stake-weighted collusion assumption beyond "one of the queried peers is malicious or the requester's network path is spoofable" is required — this is a lower bar than assuming an entire supermajority of validators collude, and is consistent with the "unprivileged...repair" issue class.

### Recommendation
- Bind response admission to the specific socket address the corresponding request was sent to (store expected `from_addr` per nonce in `OutstandingRequests`/`RequestStatus`, and reject responses whose `from_addr` doesn't match).
- Require and verify a signature over `AncestorHashesResponse::Hashes` payloads keyed to the validator's known pubkey (as is already done for `Ping`/`Pong`), so `add_response` can dedupe by verified pubkey instead of raw, spoofable `SocketAddr`.
- Consider deriving nonces per (request, expected responder) pair with sender-binding, and add unit tests for spoofed/duplicate-address response injection to catch regressions.

### Proof of Concept
1. Trigger a dead/duplicate/pruned-fork condition so `AncestorHashesService::initiate_ancestor_hashes_requests_for_duplicate_slot` samples `N = get_ancestor_hash_repair_sample_size()` validators and sends each an `AncestorHashes` request containing a distinct nonce per peer. [11](#0-10) 
2. As one of the sampled validators (or an eavesdropper who can read the outbound UDP packet on the path), obtain the nonce sent to you.
3. Craft `MINIMUM_ANCESTOR_AGREEMENT_SIZE - 1` additional UDP packets containing `AncestorHashesResponse::Hashes(forged_chain)` plus the learned nonce, spoofing the source address field to match other addresses present in `sampled_validators` (obtainable from public gossip `ContactInfo`).
4. Send these packets, plus one genuine response, to the requester's `ancestor_hashes_request_socket`.
5. `verify_and_process_ancestor_response` accepts each packet (nonce still valid, no signature check on `Hashes`), and `AncestorRequestStatus::add_response` records each spoofed `from_addr` as a new distinct agreeing validator, reaching `MINIMUM_ANCESTOR_AGREEMENT_SIZE` on `forged_chain` and returning `DuplicateAncestorDecision::EarliestMismatchFound`, driving the requester to act on the attacker's chosen ancestor hash chain.

### Citations

**File:** core/src/repair/duplicate_repair_status.rs (L158-179)
```rust
#[derive(Default, Clone)]
pub struct AncestorRequestStatus {
    // The mismatched slot that was the subject of the AncestorHashes(requested_mismatched_slot)
    // repair request. All responses to this request should be for ancestors of this slot.
    requested_mismatched_slot: Slot,
    // Condition which initiated this request
    request_type: AncestorRequestType,
    // Timestamp at which we sent out the requests
    start_ts: u64,
    // The addresses of the validators we asked for a response, a response is only acceptable
    // from these validators. The boolean represents whether the validator
    // has responded.
    sampled_validators: HashMap<SocketAddr, bool>,
    // The number of sampled validators that have responded
    num_responses: usize,
    // Validators who have responded to our ancestor repair requests. An entry
    // Vec<(Slot, Hash)> -> usize tells us which validators have
    // responded with the same Vec<(Slot, Hash)> set of ancestors.
    //
    // TODO: Trie may be more efficient
    ancestor_request_responses: HashMap<Vec<(Slot, Hash)>, Vec<SocketAddr>>,
}
```

**File:** core/src/repair/duplicate_repair_status.rs (L196-235)
```rust
    /// Record the response from `from_addr`. Returns Some(DuplicateAncestorDecision)
    /// if we have finalized a decision based on the responses. We can finalize a decision when
    /// one of the following conditions is met:
    /// 1. We have heard from all the validators
    /// 2. Or >= MINIMUM_ANCESTOR_AGREEMENT_SIZE have agreed that we have the correct versions
    ///    of nth ancestor, for some `n>0`, AND >= MINIMUM_ANCESTOR_AGREEMENT_SIZE have
    ///    agreed we have the wrong version of the `n-1` ancestor.
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
```

**File:** core/src/repair/ancestor_hashes_service.rs (L371-373)
```rust
    {
        let packet = packet.into();
        let from_addr = packet.meta().socket_addr();
```

**File:** core/src/repair/ancestor_hashes_service.rs (L384-461)
```rust
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
                    let request_type = ancestor_hashes_status_ref.get().request_type();
                    if decision.is_some() {
                        // Once a request is completed, remove it from the map so that new
                        // requests for the same slot can be made again if necessary. It's
                        // important to hold the `write` lock here via
                        // `ancestor_hashes_status_ref` so that we don't race with deletion +
                        // insertion from the `t_ancestor_requests` thread, which may
                        // 1) Remove expired statuses from `ancestor_hashes_request_statuses`
                        // 2) Insert another new one via `manage_ancestor_requests()`.
                        // In which case we wouldn't want to delete the newly inserted entry here.
                        ancestor_hashes_status_ref.remove();
                    }
                    decision.map(|decision| AncestorRequestDecision {
                        slot: request_slot,
                        decision,
                        request_type,
                    })
                } else {
                    None
                }
            }
            AncestorHashesResponse::Ping(ping) => {
                // verify that packet does not contain extraneous data
                if cursor.bytes().next().is_some() {
                    stats.invalid_packets += 1;
                    return None;
                }
                if !ping.verify() {
                    stats.ping_err_verify_count += 1;
                    return None;
                }
                stats.ping_count += 1;
                let pong = RepairProtocol::Pong(Pong::new(&ping, keypair));
                if let Ok(pong) = wincode::serialize(&pong) {
                    let _ = ancestor_socket.send_to(&pong, from_addr);
                }
                None
```

**File:** core/src/repair/ancestor_hashes_service.rs (L466-505)
```rust
    fn handle_ancestor_request_decision(
        ancestor_request_decision: AncestorRequestDecision,
        ancestor_duplicate_slots_sender: &AncestorDuplicateSlotsSender,
        retryable_slots_sender: &RetryableSlotsSender,
    ) {
        if ancestor_request_decision.is_retryable()
            && let Err(TrySendError::Full(_)) = retryable_slots_sender.try_send((
                ancestor_request_decision.slot,
                ancestor_request_decision.request_type,
            ))
        {
            warn!("Dropping ancestor request decision - retryable_slots channel is full");
        }

        // TODO: In the case of DuplicateAncestorDecision::ContinueSearch
        // This means all the ancestors were mismatched, which
        // means the earliest mismatched ancestor has yet to be found.
        //
        // In the best case scenario, this means after ReplayStage dumps
        // the earliest known ancestor `A` here, and then repairs `A`,
        // because we may still have the incorrect version of some ancestor
        // of `A`, we will mark `A` as dead and then continue the search
        // protocol through another round of ancestor repairs.
        //
        // However this process is a bit slow, so in an ideal world, the
        // protocol could be extended to keep searching by making
        // another ancestor repair request from the earliest returned
        // ancestor from this search.

        let potential_slot_to_repair = ancestor_request_decision.slot_to_repair();

        // Now signal ReplayStage about the new updated slot. It's important to do this
        // AFTER we've removed the ancestor_hashes_status_ref in case replay
        // then sends us another dead slot signal based on the updates we are
        // about to send.
        if let Some(slot_to_repair) = potential_slot_to_repair {
            // Signal ReplayStage to dump the fork that is descended from
            // `earliest_mismatched_slot_to_dump`.
            let _ = ancestor_duplicate_slots_sender.send(slot_to_repair);
        }
```

**File:** core/src/repair/ancestor_hashes_service.rs (L836-864)
```rust
        let Ok(sampled_validators) = serve_repair.repair_request_ancestor_hashes_sample_peers(
            duplicate_slot,
            cluster_slots,
            repair_validators,
            Protocol::UDP,
            &identity_keypair.pubkey(),
        ) else {
            return false;
        };

        for (pubkey, socket_addr) in &sampled_validators {
            repair_stats
                .ancestor_requests
                .update(pubkey, duplicate_slot, 0);
            let ancestor_hashes_repair_type = AncestorHashesRepairType(duplicate_slot);
            let nonce = outstanding_requests
                .write()
                .unwrap()
                .add_request(ancestor_hashes_repair_type, timestamp());
            let Ok(request_bytes) = serve_repair.ancestor_repair_request_bytes(
                identity_keypair,
                pubkey,
                duplicate_slot,
                nonce,
            ) else {
                continue;
            };
            let _ = ancestor_hashes_request_socket.send_to(&request_bytes, socket_addr);
        }
```

**File:** core/src/repair/outstanding_requests.rs (L60-94)
```rust
    pub fn register_response<R>(
        &mut self,
        nonce: u32,
        response: &S,
        now: u64,
        success_fn: impl Fn(&T) -> R,
    ) -> Option<R> {
        let mut should_delete = false;
        let response = self.requests.get_mut(&nonce).and_then(|status| {
            if status.num_expected_responses == 0 {
                // No more expected responses
                return None;
            }

            if now >= status.expire_timestamp || !status.request.verify_response(response) {
                // Invalid/expired response should invalidate this nonce.
                should_delete = true;
                return None;
            }

            status.num_expected_responses -= 1;
            if status.num_expected_responses == 0 && status.metadata.is_none() {
                // No metadata, and no more expected responses safe to delete eagerly.
                should_delete = true;
            }
            Some(success_fn(&status.request))
        });

        if should_delete {
            self.requests
                .pop(&nonce)
                .expect("request must exist when marked for deletion");
        }
        response
    }
```
