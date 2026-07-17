### Title
Attacker-Controlled `addr` in `StateHeaderRequest`/`StatePartRequest` Causes Snapshot Host to Initiate TCP Connections to Arbitrary Addresses — (File: `chain/network/src/peer_manager/peer_manager_actor.rs`, `chain/network/src/peer_manager/network_state/mod.rs`)

---

### Summary

The Tier3 state-sync protocol embeds the requester's public `SocketAddr` inside the routed `StateHeaderRequest` and `StatePartRequest` messages. The snapshot host receiving these messages uses that `addr` field verbatim — without any validation against the actual sender's network address — to initiate an outbound TCP connection. Any unprivileged peer on the NEAR network can forge an arbitrary `addr` (e.g., `127.0.0.1:22`, `10.0.0.1:8080`) and cause every reachable snapshot host to open a TCP connection to that address. This is the direct nearcore analog of the SSRF vulnerability in the token-logo endpoint: both involve a server making outbound network connections to a URL/address supplied by an untrusted external party.

---

### Finding Description

**Data invariant broken:** The `addr` field in `StateHeaderRequest` and `StatePartRequest` is supposed to be the requesting node's own public `SocketAddr`, so the snapshot host can connect back to deliver the state part over a direct Tier3 connection. The invariant is that `addr` equals the actual network address of the authenticated sender. There is no enforcement of this invariant anywhere in the receive path.

**Attacker-controlled data flow:**

1. Any peer constructs a routed `T2MessageBody::StateHeaderRequest` or `T2MessageBody::StatePartRequest` with `addr` set to an arbitrary `SocketAddr` (e.g., `127.0.0.1:3030`, `192.168.1.1:22`, or any internal service). [1](#0-0) [2](#0-1) 

2. The snapshot host receives the routed message. In `handle_peer_message`, `request.addr` is extracted directly and placed into a `PeerInfo` without any check that it matches the actual TCP source address of the sender: [3](#0-2) 

3. The resulting `Tier3Request` is dispatched to `PeerManagerActor::handle(Tier3Request)`. After computing the state part, the handler calls `transport.connect_to_peer` using the attacker-supplied `request.peer_info` (which contains the forged `addr`): [4](#0-3) 

The snapshot host thus initiates an outbound TCP connection to the attacker-specified address. The NEAR Tier3 handshake will fail if the target does not speak the NEAR protocol, but the TCP SYN/ACK exchange still occurs, and the connection is fully established at the OS level before the handshake is attempted.

---

### Impact Explanation

**Severity: High**

- **Internal network scanning (SSRF):** A snapshot host may be deployed inside a private network (cloud VPC, data-center LAN). An attacker can enumerate internal hosts and ports by observing connection timing and success/failure. Addresses like `127.0.0.1:3030` (local RPC), `127.0.0.1:3031` (local metrics), or internal database/admin endpoints are reachable from the snapshot host but not from the public internet.
- **Amplification:** A single attacker message, routed to multiple snapshot hosts, causes all of them to connect to the same target simultaneously.
- **Side-channel disclosure:** Whether `connect_to_peer` succeeds or logs a timeout reveals whether the target port is open, leaking internal network topology.
- **Potential interaction with internal services:** Some internal services (e.g., Redis, Postgres, internal HTTP APIs) react to TCP connection establishment in ways that can be exploited even without a valid application-layer handshake.

The corrupted value is `request.peer_info.addr` inside `Tier3Request`, which is set to the attacker-forged `SocketAddr` rather than the legitimate sender address.

---

### Likelihood Explanation

Any node that can send a routed Tier2 message to a snapshot host can trigger this. Routing on the NEAR P2P network is open to all participants; no validator or privileged role is required. Snapshot hosts are specifically advertised via `SyncSnapshotHosts` gossip, making them trivially enumerable. The attack requires only a valid NEAR node identity and knowledge of the target snapshot host's `PeerId`.

---

### Recommendation

Validate that `request.addr` matches the actual source address of the authenticated Tier2 connection before using it as the Tier3 callback address. Concretely:

- In the `T2MessageBody::StateHeaderRequest` / `T2MessageBody::StatePartRequest` handler in `handle_peer_message`, compare `request.addr.ip()` against the peer's known TCP address (available from the connection context). Reject or clamp the address if it does not match.
- Alternatively, discard the `addr` field entirely and derive the callback address from the authenticated connection's `peer_addr` (the OS-level TCP source address), which cannot be forged.
- As a defense-in-depth measure, restrict outbound Tier3 connections to addresses that appear in the node's peer store or that match the peer's advertised address in `PeerInfo`.

---

### Proof of Concept

```
1. Attacker node A connects to the NEAR P2P network (standard node identity).
2. A observes SyncSnapshotHosts gossip to learn snapshot host peer_id S.
3. A constructs a routed T2 message targeting S:
       StatePartRequest {
           shard_id: <any valid shard>,
           sync_hash: <any known sync hash>,
           part_id: 0,
           addr: SocketAddr::from(([127, 0, 0, 1], 22)),  // target: localhost SSH
       }
4. A routes the message to S over Tier2.
5. S receives the message, extracts addr = 127.0.0.1:22, and calls:
       transport.connect_to_peer(&clock,
           PeerInfo { id: A.peer_id, addr: Some(127.0.0.1:22), .. },
           Tier::T3)
6. S's OS initiates a TCP SYN to 127.0.0.1:22.
   - If port 22 is open: TCP handshake completes; NEAR handshake then fails.
   - If port 22 is closed: RST received immediately.
   Timing difference reveals whether the port is open on S's localhost.
7. Repeat with different addr values to map S's internal network.
``` [5](#0-4) [6](#0-5)

### Citations

**File:** chain/network/src/network_protocol/state_sync.rs (L123-130)
```rust
pub struct StateHeaderRequest {
    /// Requested shard id
    pub shard_id: ShardId,
    /// Sync block hash
    pub sync_hash: CryptoHash,
    /// Public address of the node making the request
    pub addr: std::net::SocketAddr,
}
```

**File:** chain/network/src/network_protocol/state_sync.rs (L144-152)
```rust
pub struct StatePartRequest {
    /// Requested shard id
    pub shard_id: ShardId,
    /// Sync block hash
    pub sync_hash: CryptoHash,
    /// Requested part id
    pub part_id: u64,
    /// Public address of the node making the request
    pub addr: std::net::SocketAddr,
```

**File:** chain/network/src/peer_manager/network_state/mod.rs (L1005-1033)
```rust
                T2MessageBody::StateHeaderRequest(request) => {
                    self.peer_manager_adapter.send(Tier3Request {
                        peer_info: PeerInfo {
                            id: msg_author,
                            addr: Some(request.addr),
                            account_id: None,
                        },
                        body: Tier3RequestBody::StateHeader(StateHeaderRequestBody {
                            shard_id: request.shard_id,
                            sync_hash: request.sync_hash,
                        }),
                    });
                    None
                }
                T2MessageBody::StatePartRequest(request) => {
                    self.peer_manager_adapter.send(Tier3Request {
                        peer_info: PeerInfo {
                            id: msg_author,
                            addr: Some(request.addr),
                            account_id: None,
                        },
                        body: Tier3RequestBody::StatePart(StatePartRequestBody {
                            shard_id: request.shard_id,
                            sync_hash: request.sync_hash,
                            part_id: request.part_id,
                        }),
                    });
                    None
                }
```

**File:** chain/network/src/peer_manager/peer_manager_actor.rs (L1546-1646)
```rust
    fn handle(&mut self, request: Tier3Request) {
        let _timer = metrics::PEER_MANAGER_TIER3_REQUEST_TIME
            .with_label_values::<&str>(&[(&request.body).into()])
            .start_timer();

        let state = self.state.clone();
        let clock = self.clock.clone();
        let transport = self.transport.clone();
        self.handle.spawn("handle tier3 request",
            async move {
                // Process the request.
                // Unconditionally produce an ack to be sent back over tier2.
                // Optionally produce a response to be sent over tier3.
                let (tier2_ack, maybe_tier3_response) = match request.body {
                    Tier3RequestBody::StateHeader(StateHeaderRequestBody { shard_id, sync_hash }) => {
                        let (ack, response) = match state.state_request_adapter.send_async(StateRequestHeader { shard_id, sync_hash }).await {
                            Ok(Some(client_response)) => {
                                (StateRequestAckBody::WillRespond, Some(PeerMessage::VersionedStateResponse(*client_response.0)))
                            }
                            Ok(None) => {
                                tracing::debug!(target: "network", ?request, "client declined to respond");
                                (StateRequestAckBody::Busy, None)
                            }
                            Err(err) => {
                                tracing::error!(target: "network", ?request, ?err, "client failed to respond");
                                (StateRequestAckBody::Error, None)
                            }
                        };

                        (
                            T2MessageBody::StateRequestAck(StateRequestAck {
                                shard_id,
                                sync_hash,
                                part_id_or_header: PartIdOrHeader::Header,
                                body: ack,
                            }).into(),
                            response
                        )
                    }
                    Tier3RequestBody::StatePart(StatePartRequestBody { shard_id, sync_hash, part_id }) => {
                        let (ack, response) = match state.state_request_adapter.send_async(StateRequestPart { shard_id, sync_hash, part_id }).await {
                            Ok(Some(client_response)) => {
                                (StateRequestAckBody::WillRespond, Some(PeerMessage::VersionedStateResponse(*client_response.0)))
                            }
                            Ok(None) => {
                                tracing::debug!(target: "network", ?request, "client declined to respond");
                                (StateRequestAckBody::Busy, None)
                            }
                            Err(err) => {
                                tracing::error!(target: "network", ?err, ?request, "client failed to respond");
                                (StateRequestAckBody::Error, None)
                            }
                        };

                        (
                            T2MessageBody::StateRequestAck(StateRequestAck {
                                shard_id,
                                sync_hash,
                                part_id_or_header: PartIdOrHeader::Part { part_id },
                                body: ack,
                            }).into(),
                            response
                        )
                    }
                };

                let sender: PeerId = request.peer_info.id.clone();

                // Send an ack for the request
                tracing::debug!(target: "network", ?tier2_ack, %sender, "ack state request from host");
                let routed_message = state.sign_message(
                    &clock,
                    RawRoutedMessage {
                        target: PeerIdOrHash::PeerId(sender.clone()),
                        body: tier2_ack,
                    },
                );
                if !state.send_message_to_peer(&clock, tcp::Tier::T2, routed_message, transport.as_ref()) {
                    tracing::debug!(target: "network", sender = %sender, "failed to route ack");
                }

                let Some(tier3_response) = maybe_tier3_response else {
                    return;
                };

                // Establish a tier3 connection if we don't have one already.
                let already_connected_t3 =
                    state.peers.is_connected_on_tier(&sender, tcp::Tier::T3);
                if !already_connected_t3 {
                    if let Err(err) = transport
                        .connect_to_peer(&clock, request.peer_info.clone(), tcp::Tier::T3)
                        .await
                    {
                        tracing::debug!(target: "network", ?err, peer_info = %request.peer_info, "tier3 failed to connect");
                    }
                }

                transport.send_message(tcp::Tier::T3, sender, Arc::new(tier3_response));
            }
        );
    }
```
