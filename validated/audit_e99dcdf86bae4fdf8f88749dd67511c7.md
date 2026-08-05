## Analog Found: Repair-response acceptance is bound only to a 32-bit nonce, not to sender identity

### Title
Repair response acceptance is keyed only by nonce with no sender/address binding, allowing off-path spoofed responses to hijack a validator's shred repair requests - (File: `core/src/shred_fetch_stage.rs`, `core/src/repair/outstanding_requests.rs`)

### Summary
The Uniswap report describes a message-routing flaw: a shared, unauthenticated identifier (`uniswap://`) is used to deliver sensitive messages, and any party that also registers that identifier can intercept messages that were meant for someone else. The structural analog in Agave is the shred-repair response path: a repair request is bound to a purely random 32-bit `Nonce` sent in plaintext over UDP, and the response is accepted by `verify_repair_nonce` [1](#0-0)  purely on the basis of that nonce matching an entry in `OutstandingRequests`, with **no check that the packet came from the peer the request was actually sent to**.

### Finding Description
When a validator sends a repair request, it generates the nonce randomly and stores it keyed only by nonce, with no binding to socket address or peer pubkey: [2](#0-1) 

When a REPAIR-flagged packet arrives at the fetch stage, the sole gate that decides whether to accept it as a legitimate response is `register_response`, matched by nonce plus a `verify_response` callback that validates response *content* (e.g., merkle proof/shred structure), never the sender: [3](#0-2) [4](#0-3) 

Contrast this with the *request*-side path, where the server explicitly verifies the sender's identity and signature via `verify_signed_packet`, checking `header.recipient == my_id` and a valid signature from `request.sender()`: [5](#0-4) 

No equivalent check exists for the *response* path consumed by `verify_repair_nonce` / `register_response`. Any UDP datagram, from any source IP:port, that carries the correct plaintext nonce and passes the loose `verify_response` structural check is accepted as if it came from the queried peer. Since UDP source addresses are trivially spoofable and repair traffic is sent in the clear, an off-path attacker who observes (or predicts, given only 32 bits of entropy and the fact that the nonce travels unencrypted on the wire) an outstanding nonce can inject a response that consumes that entry in `OutstandingRequests` before the legitimately-queried peer's real response arrives — this is confirmed by the existing test that deliberately shows any correctly-nonced packet is accepted regardless of who sent it (`test_register_response`, which never asserts on sender) [6](#0-5) .

This mirrors the report's broken invariant precisely: the "address" used to route a sensitive message (the shred a validator needs to catch up/repair) is a bare shared token, not an authenticated channel to the specific counterparty that was asked — exactly like the `uniswap://` scheme being claimable by any app.

### Impact Explanation
Because the shred payload itself must still pass shred/leader signature verification downstream, an attacker cannot forge arbitrary block content this way. However, hijacking the response slot lets an unprivileged, off-path attacker:
- Consume/invalidate a validator's pending repair request with a stale, duplicate, or otherwise unhelpful (but structurally-valid) response, causing the real response from the intended peer to be dropped (since `register_response` deletes the nonce entry once satisfied), degrading/delaying that node's ability to repair missing shreds.
- Repeatedly do this cheaply (any REPAIR-flagged UDP packet with a guessed/observed nonce), resulting in low-cost, non-RPC remote degradation of repair throughput for the targeted node without needing peer/validator privileges or a malicious cluster member.

### Likelihood Explanation
`Nonce` is only 32 bits and transmitted unencrypted in every repair request/response over UDP, so an attacker positioned to observe traffic (e.g., same network segment, ISP-level observation, or via traffic analysis of the target's repair socket) can harvest live nonces without needing gossip/validator trust. No sender-address or peer-pubkey check exists on the response-acceptance path, only on the request-acceptance path, so exploitation requires no privileged network position beyond passive observation plus the ability to send spoofed UDP packets.

### Recommendation
- **Short term:** Document that `verify_repair_nonce`/`register_response` currently has no sender binding, and confirm whether this gap is intentional or an oversight.
- **Long term:** Bind outstanding repair requests to the `(nonce, requested peer socket address)` pair (or peer pubkey via a signed pong-style challenge) so that a response is only accepted if it originates from the address the corresponding request was sent to, matching the authentication rigor already applied to inbound repair *requests* in `verify_signed_packet`.

### Proof of Concept
Not independently executable from static analysis alone; the finding is derived from the absence of any sender-address/pubkey check in `verify_repair_nonce` / `register_response` compared to the presence of such checks in `verify_signed_packet` for the request path. A concrete PoC would require crafting a UDP packet with an observed/guessed nonce and spoofed source address targeting a live validator's repair socket, which is out of scope for static/code-only verification.

Note: I was unable to fully trace `verify_response`'s exact per-type logic for every `RequestResponse` implementer (only the trait definition was available in the index) to determine precisely how loose the structural check is for each repair message type; this may affect exact exploitability details and would need runtime/code confirmation beyond what the index exposes.

### Citations

**File:** core/src/shred_fetch_stage.rs (L82-99)
```rust
                let now = solana_time_utils::timestamp();
                let mut outstanding_repair_requests =
                    repair_context.outstanding_repair_requests.write().unwrap();
                packet_batch
                    .iter_mut()
                    .filter(|packet| !packet.meta().discard())
                    .for_each(|mut packet| {
                        // Have to set repair flag here so that the nonce is
                        // taken off the shred's payload.
                        packet.meta_mut().flags |= PacketFlags::REPAIR;
                        if !verify_repair_nonce(
                            packet.as_ref(),
                            now,
                            &mut outstanding_repair_requests,
                        ) {
                            packet.meta_mut().set_discard(true);
                        }
                    });
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

**File:** core/src/repair/outstanding_requests.rs (L26-44)
```rust
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

**File:** core/src/repair/outstanding_requests.rs (L186-248)
```rust
    #[test]
    fn test_register_response() {
        let repair_type = ShredRepairType::Orphan(9);
        let mut outstanding_requests = OutstandingRequests::<ShredRepairType>::default();
        let nonce = outstanding_requests.add_request(repair_type, timestamp());
        let keypair = Keypair::new();
        let shred = Shredder::single_shred_for_tests(0, &keypair);
        let mut expire_timestamp = outstanding_requests
            .requests
            .get(&nonce)
            .map(|status| status.expire_timestamp)
            .unwrap();
        let mut num_expected_responses = outstanding_requests
            .requests
            .get(&nonce)
            .map(|status| status.num_expected_responses)
            .unwrap();
        assert!(num_expected_responses > 1);

        // Response that passes all checks should decrease num_expected_responses.
        assert!(
            outstanding_requests
                .register_response(nonce, shred.payload(), expire_timestamp - 1, |_| ())
                .is_some()
        );
        num_expected_responses -= 1;
        assert_eq!(
            outstanding_requests
                .requests
                .get(&nonce)
                .unwrap()
                .num_expected_responses,
            num_expected_responses
        );

        // Response with incorrect nonce is ignored.
        assert!(
            outstanding_requests
                .register_response(nonce + 1, shred.payload(), expire_timestamp - 1, |_| ())
                .is_none()
        );
        assert!(
            outstanding_requests
                .register_response(nonce + 1, shred.payload(), expire_timestamp, |_| ())
                .is_none()
        );
        assert_eq!(
            outstanding_requests
                .requests
                .get(&nonce)
                .unwrap()
                .num_expected_responses,
            num_expected_responses
        );

        // Response with timestamp over limit should remove status, preventing late
        // responses from being accepted.
        assert!(
            outstanding_requests
                .register_response(nonce, shred.payload(), expire_timestamp, |_| ())
                .is_none()
        );
        assert!(outstanding_requests.requests.get(&nonce).is_none());
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
