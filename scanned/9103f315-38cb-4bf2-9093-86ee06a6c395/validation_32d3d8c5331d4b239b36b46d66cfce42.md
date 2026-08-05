Good — `add_response` in `duplicate_repair_status.rs` does check `sampled_validators.get_mut(from_addr)`, so a response is only accepted if it comes from a `SocketAddr` that was in the sampled set for that specific slot. That check does properly reject unsampled peers, so this specific path is not a clean analog once that gate is reached. However, the weaker link is one layer earlier, in the nonce/response-authentication step itself.

### Title
Ancestor-hashes repair responses are accepted with only a 32-bit guessable nonce and no sender authentication - ([File: core/src/repair/ancestor_hashes_service.rs])

### Summary
`verify_and_process_ancestor_response` in `core/src/repair/ancestor_hashes_service.rs` accepts an `AncestorHashesResponse::Hashes` payload as long as it matches an outstanding nonce via `OutstandingRequests::register_response`, and `RequestResponse::verify_response` for this type only checks `hashes.len() <= MAX_ANCESTOR_RESPONSES` [1](#0-0) . Unlike the signed repair *requests* (`verify_signed_packet`, which validates a keypair signature) [2](#0-1) , the `Hashes` *response* carries no signature at all — only the `Ping`/`Pong` variant is cryptographically verified [3](#0-2) .

### Finding Description
This is the analog of the reported bug class: a message-authenticity check that validates *some* metadata (chain id / sender address in the Axiom case; nonce match in Agave) but omits the check that actually ties the message to a specific trusted originator (the oracle-adapter set in Axiom; the sender socket address in Agave).

The nonce is a 32-bit random value (`rng().random_range(0..Nonce::MAX)`), generated purely client-side and never bound cryptographically to the peer it was sent to [4](#0-3) . When a response arrives, `register_response` only checks that:
1. the nonce exists in the LRU cache,
2. it hasn't expired, and
3. `verify_response()` passes (for `Hashes`, just a length bound) [5](#0-4) 

There is no check anywhere in this path that the response came from an address/port to which the corresponding request nonce was actually sent — that binding only happens later, inside `AncestorRequestStatus::add_response`, which checks `sampled_validators.get_mut(from_addr)` [6](#0-5) . But this later, correct check only gates whether that response counts toward the sampled-agreement quorum for a *particular* `requested_mismatched_slot`. The `register_response()` call happens first and unconditionally consumes the nonce regardless of `from_addr` — meaning **any peer, whitelisted or not, staked or not**, that guesses/observes a live nonce can burn it with a garbage `Hashes` payload before the legitimate response bearing that nonce is registered:

```rust
// core/src/repair/ancestor_hashes_service.rs:399-411
let request_slot = outstanding_requests.write().unwrap().register_response(
    nonce,
    &response,
    timestamp(),
    |ancestor_hashes_request| ancestor_hashes_request.0,
);
if request_slot.is_none() {
    stats.invalid_packets += 1;
    return None;
}
```

Because `register_response` decrements `num_expected_responses` and deletes the entry once it hits zero (`num_expected_responses -= 1; ... should_delete = true`) [7](#0-6) , and `AncestorHashesRepairType::num_expected_responses()` returns `1` [8](#0-7) , an attacker who sends a single spoofed UDP packet with the correct nonce (source socket address is attacker-controlled and irrelevant to this check) causes the *legitimate* response from the real sampled validator — arriving with the same nonce shortly after — to be dropped as "no more expected responses," since the entry is already gone from the LRU. This is a remote, unauthenticated denial-of-service on the ancestor-hashes dead/pruned-slot repair protocol, which is exactly the class of bug described in the report: a check that stops at "does this token/nonce exist" without validating "did this come from the trusted set of responders."

### Impact Explanation
This does not let an attacker directly corrupt consensus (the later `add_response`/`sampled_validators` check still filters by sender for the actual decision), but it does let any unauthenticated network peer:
- Selectively suppress/poison ancestor-hashes repair responses for dead or popular-pruned slots by winning the race with a single well-timed guessed-nonce packet, delaying or repeatedly retrying (`InvalidSample`/timeout) resolution of duplicate/pruned-fork repairs.
- This is a non-RPC remote degradation/DoS on the repair subsystem (an unprivileged network primitive: nonce is 32-bit and not bound to peer identity), which matches the "non-RPC remote exhaustion/crash" impact category.

### Likelihood Explanation
Nonce space is 32 bits and outstanding requests are stored in a 16K-entry LRU (`LruCache::new(16 * 1024)`), so an attacker flooding recently-observed or brute-forced nonces at the `ancestor_hashes_requests` UDP socket has a workable, if probabilistic, ability to intercept/consume in-flight nonces, especially since ancestor-hashes requests are relatively rare/low-volume events (making a targeted nonce guess more tractable within the request's `DEFAULT_REQUEST_EXPIRATION_MS` window) [9](#0-8) .

### Recommendation
Bind the nonce validation to the expected responder: `register_response` (or its caller) should verify that `from_addr` matches one of the addresses the specific outstanding request was sent to, before decrementing/consuming the entry — mirroring the existing, correct check already present in `AncestorRequestStatus::add_response`. Alternatively, require a per-response signature (as already done for `Ping`/`Pong`) so a spoofed `Hashes` response cannot be crafted by an arbitrary UDP sender.

### Proof of Concept
1. Requester `R` sends `AncestorHashes` requests for slot `S` to a real sampled peer `P`, with random nonce `n`, via `serve_repair.ancestor_repair_request_bytes()` [10](#0-9) .
2. Attacker observes/guesses `n` (e.g., through traffic analysis on the UDP socket, or by brute-forcing outstanding nonces before expiration) and sends a spoofed `AncestorHashesResponse::Hashes(garbage)` packet with nonce `n` from an arbitrary, non-whitelisted source address to `R`'s `ancestor_hashes_requests` socket.
3. `verify_and_process_ancestor_response` calls `register_response(n, ...)`, which succeeds (length check passes), decrements `num_expected_responses` to 0, and deletes the nonce entry from the LRU — all without checking `from_addr` against `P`.
4. The subsequent legitimate response from `P` carrying the same nonce `n` is then rejected in `register_response` (`status.num_expected_responses == 0` → `None`), so `R` never processes `P`'s real ancestor-hash data for slot `S`, delaying/blocking the ancestor-hashes duplicate/pruned-fork repair protocol.

### Citations

**File:** core/src/repair/serve_repair.rs (L210-214)
```rust
impl RequestResponse for AncestorHashesRepairType {
    type Response = AncestorHashesResponse;
    fn num_expected_responses(&self) -> u32 {
        1
    }
```

**File:** core/src/repair/serve_repair.rs (L215-220)
```rust
    fn verify_response(&self, response: &AncestorHashesResponse) -> bool {
        match response {
            AncestorHashesResponse::Hashes(hashes) => hashes.len() <= MAX_ANCESTOR_RESPONSES,
            AncestorHashesResponse::Ping(ping) => ping.verify(),
        }
    }
```

**File:** core/src/repair/serve_repair.rs (L1441-1481)
```rust
            RepairProtocol::Pong(pong) => {
                if !pong.verify() {
                    return Err(Error::from(RepairVerifyError::SigVerify));
                }
            }
            RepairProtocol::WindowIndex { header, .. }
            | RepairProtocol::HighestWindowIndex { header, .. }
            | RepairProtocol::Orphan { header, .. }
            | RepairProtocol::AncestorHashes { header, .. }
            | RepairProtocol::ParentAndFecSetCount { header, .. }
            | RepairProtocol::FecSetRoot { header, .. }
            | RepairProtocol::WindowIndexForBlockId { header, .. } => {
                if &header.recipient != my_id {
                    return Err(Error::from(RepairVerifyError::IdMismatch));
                }
                let time_diff_ms = timestamp().abs_diff(header.timestamp);
                if u128::from(time_diff_ms) > SIGNED_REPAIR_TIME_WINDOW.as_millis() {
                    return Err(Error::from(RepairVerifyError::TimeSkew));
                }
                let Some(leading_buf) = bytes.get(..4) else {
                    debug_assert!(
                        false,
                        "request should have failed deserialization: {request:?}",
                    );
                    return Err(Error::from(RepairVerifyError::Malformed));
                };
                let Some(trailing_buf) = bytes.get(4 + SIGNATURE_BYTES..) else {
                    debug_assert!(
                        false,
                        "request should have failed deserialization: {request:?}",
                    );
                    return Err(Error::from(RepairVerifyError::Malformed));
                };
                let Some(from_id) = request.sender() else {
                    return Err(Error::from(RepairVerifyError::SigVerify));
                };
                let signed_data = [leading_buf, trailing_buf].concat();
                if !header.signature.verify(from_id.as_ref(), &signed_data) {
                    return Err(Error::from(RepairVerifyError::SigVerify));
                }
            }
```

**File:** core/src/repair/serve_repair.rs (L1627-1646)
```rust
    pub fn ancestor_repair_request_bytes(
        &self,
        keypair: &Keypair,
        repair_peer_id: &Pubkey,
        request_slot: Slot,
        nonce: Nonce,
    ) -> Result<Vec<u8>> {
        let header = RepairRequestHeader {
            signature: Signature::default(),
            sender: keypair.pubkey(),
            recipient: *repair_peer_id,
            timestamp: timestamp(),
            nonce,
        };
        let request = RepairProtocol::AncestorHashes {
            header,
            slot: request_slot,
        };
        Self::repair_proto_to_bytes(&request, keypair)
    }
```

**File:** core/src/repair/ancestor_hashes_service.rs (L446-461)
```rust
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

**File:** core/src/repair/outstanding_requests.rs (L8-8)
```rust
pub const DEFAULT_REQUEST_EXPIRATION_MS: u64 = 60_000;
```

**File:** core/src/repair/outstanding_requests.rs (L20-44)
```rust
    pub fn add_request(&mut self, request: T, now: u64) -> Nonce {
        self.add_request_with_metadata(request, now, None)
    }

    /// Similar to `add_request` but additionally specifies an associated metadata
    /// for the nonce that can be fetched with `fetch_metadata_for_nonce`.
    pub fn add_request_with_metadata(
        &mut self,
        request: T,
        now: u64,
        metadata: Option<U>,
    ) -> Nonce {
        let num_expected_responses = request.num_expected_responses();
        let nonce = rng().random_range(0..Nonce::MAX);
        self.requests.put(
            nonce,
            RequestStatus {
                expire_timestamp: now + DEFAULT_REQUEST_EXPIRATION_MS,
                num_expected_responses,
                request,
                metadata,
            },
        );
        nonce
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

**File:** core/src/repair/duplicate_repair_status.rs (L203-217)
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
```
