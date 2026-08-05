Note on verification limits: I was unable to fully inspect `ShredRepairType::verify_response` (in `core/src/repair/serve_repair.rs`) in this session due to running out of tool iterations, so I cannot confirm every content-check performed on a shred response beyond nonce matching. That said, the core structural flaw — accepting a UDP response as authentic purely because it carries a matching request-correlation nonce, without checking that it originated from the peer the request was actually sent to — is directly supported by the code I did read.

### Title
Repair response acceptance relies solely on a spoofable 32-bit nonce, not sender authentication - (core/src/shred_fetch_stage.rs)

### Summary
The reported Uniswap bug is a "trust-by-correlation-id" flaw: `sendRequestAsync`'s `handleDappRequest` listener accepts any `postMessage` whose `requestId` matches an outstanding request, without checking `event.source`/origin, letting any window/origin that can guess or observe the id inject a fake response. The closest Agave analog is in the shred-repair response path: incoming repair-response packets are accepted as satisfying an outstanding repair request purely by matching a `Nonce` (`u32`) carried in the packet, with no check that the response actually came from the peer address the corresponding request was sent to.

### Finding Description
When a validator sends a repair request, it records the expected response under a randomly generated `nonce` via `OutstandingRequests::add_request` [1](#0-0) . When a UDP packet flagged as a repair response arrives, `verify_repair_nonce` extracts the shred/nonce and calls `register_response`, and if it matches an outstanding nonce and the request "hasn't expired," the packet is treated as a satisfying response and kept (not discarded) — with no comparison of the packet's source `SocketAddr` to the destination the original request was sent to: [2](#0-1) 

`OutstandingRequests::register_response` itself only keys off `nonce` and the request's own `verify_response(response)` check; the sender's network address is not part of the matching criteria at all: [3](#0-2) 

This is structurally identical to the reported flaw: authentication of "is this really the peer I asked" is reduced to "does an opaque, attacker-observable/guessable correlation value match," exactly as the report calls out for `requestId` ("checks only the requestId, which can be guessed or sniffed by malicious websites").

By contrast, other repair paths in the same codebase do bind identity into validation — e.g. `verify_signed_packet` checks that `header.recipient == my_id`, timestamp freshness, and an Ed25519 signature over the request bytes before honoring a *request* [4](#0-3) , and `verify_and_process_ancestor_response` additionally threads `from_addr` into `add_response` for use in duplicate-slot decision aggregation [5](#0-4) . The plain shred-repair response path (`verify_repair_nonce`) lacks any equivalent per-response sender check.

### Impact Explanation
An off-path attacker who can guess (32-bit search space) or observe a validator's outstanding repair nonce can send a spoofed UDP packet claiming to satisfy that repair request from an arbitrary source address. Because matching is nonce-only, the fabricated response consumes the outstanding-request slot (`register_response` decrements/removes it), causing the legitimate response — when it later arrives — to be treated as an unmatched/invalid nonce and discarded. This can suppress the actual repair of a targeted shred, contributing to `repair`-subsystem degradation (a listed valid-impact category: "non-RPC remote exhaustion/crash" / degraded repair/blockstore completion) without requiring a malicious validator, leaked key, or trusted-plugin assumption — only the ability to send UDP packets to the victim's repair socket with a matching nonce.

### Likelihood Explanation
Exploitation requires guessing/observing a 32-bit nonce within the request's short expiration window (`DEFAULT_REQUEST_EXPIRATION_MS`) — nontrivial via blind brute force over the network, but feasible for an attacker who can passively observe traffic to/from the target's repair UDP port (e.g., a peer on path, or a validator that was itself asked to repair and thus already knows the nonce) or who floods candidate nonces. This is a lower-likelihood, but structurally real, gap compared to fully signature/address-authenticated protocol paths that exist elsewhere in the same file (`verify_signed_packet`).

### Recommendation
Bind the responder's source `SocketAddr` (and/or expected responder pubkey, for signed-repair paths) into the outstanding-request record at `add_request` time, and require it to match in `register_response`/`verify_repair_nonce` before accepting a response as valid — mirroring the `event.source === window` check recommended in the original report, adapted to "response socket address == request destination address."

### Proof of Concept
Conceptual (network-level, not exploit code): 
1. Validator V sends a `ShredRepairType` request to peer P, registering `nonce = N` via `add_request`.
2. Attacker A, having guessed or observed `N` (e.g., via traffic observation or brute-force flood), sends a UDP packet to V's repair-response port containing a shred payload plus trailing `nonce = N`, spoofing any source address.
3. `verify_repair_nonce` → `register_response(N, ...)` succeeds purely on the nonce match [6](#0-5) , consuming the outstanding-request slot.
4. When P's legitimate response later arrives with the same nonce, `register_response` finds no matching entry (already consumed) and discards it as invalid, leaving V's genuine repair unfulfilled.

### Citations

**File:** core/src/repair/outstanding_requests.rs (L20-22)
```rust
    pub fn add_request(&mut self, request: T, now: u64) -> Nonce {
        self.add_request_with_metadata(request, now, None)
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

**File:** core/src/shred_fetch_stage.rs (L250-264)
```rust
// Returns false if repair nonce is invalid and packet should be discarded.
#[must_use]
fn verify_repair_nonce(
    packet: PacketRef,
    now: u64, // solana_time_utils::timestamp()
    outstanding_repair_requests: &mut OutstandingShredRepairs,
) -> bool {
    debug_assert!(packet.meta().flags.contains(PacketFlags::REPAIR));
    let Some((shred, Some(nonce))) = shred::layout::get_shred_and_repair_nonce(packet) else {
        return false;
    };
    outstanding_repair_requests
        .register_response(nonce, shred, now, |_| ())
        .is_some()
}
```

**File:** core/src/repair/serve_repair.rs (L1446-1480)
```rust
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
```

**File:** core/src/repair/ancestor_hashes_service.rs (L417-424)
```rust
                if let Occupied(mut ancestor_hashes_status_ref) =
                    ancestor_hashes_request_statuses.entry(request_slot)
                {
                    let decision = ancestor_hashes_status_ref.get_mut().add_response(
                        &from_addr,
                        hashes.clone(),
                        blockstore,
                    );
```
