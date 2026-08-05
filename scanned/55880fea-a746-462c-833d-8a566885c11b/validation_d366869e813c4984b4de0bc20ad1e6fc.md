## Analysis

The external report's core defect is a **rate-limit collapsed onto the wrong identity**: `checkLastBlockAction` should isolate per-end-user cooldown, but instead keys the check on a shared caller (`msg.sender`), so unrelated legitimate users sharing that key starve each other out.

The closest Agave analog is the QUIC/TPU **per-IP connection rate limiter**, which gates *all* incoming connections purely by source IP address before any peer/stake identity is established, so unrelated legitimate senders sharing an IP (NAT, corporate egress, hosting/cloud infra, RPC-provider proxies) can be starved by each other's normal traffic.

### Title
Per-IP QUIC connection rate limiter conflates independent unprivileged peers sharing an IP, causing legitimate transaction-submission starvation - (File: streamer/src/nonblocking/connection_rate_limiter.rs)

### Summary
`ConnectionRateLimiter` enforces `max_connections_per_ipaddr_per_min` keyed solely by `IpAddr`, before any staked/pubkey identity is known. Any independent, unprivileged clients that happen to share a public IP (common with NAT, corporate proxies, or RPC-provider relays that terminate many end-user connections from one egress IP) are treated as a single rate-limit bucket. One legitimate high-traffic tenant behind that IP can exhaust the whole IP's budget, causing the server to `ignore()`/close QUIC connections from every other unrelated user at that IP, regardless of their stake or intent.

### Finding Description
`ConnectionRateLimiter::is_allowed`/`register_connection` use a `KeyedRateLimiter<IpAddr>` [1](#0-0) , and in `run_server` this check is applied to every incoming QUIC connection attempt purely by `incoming.remote_address().ip()` before pubkey/stake is known: [2](#0-1) 
and again after handshake in `setup_connection`, still keyed only by IP: [3](#0-2) 

The default budget is small — `DEFAULT_MAX_CONNECTIONS_PER_IPADDR_PER_MINUTE = 8` [4](#0-3)  — with only a 10x burst allowance acknowledged in code comments as a mitigation for "container environments running multiple pods on same IP" [5](#0-4) . This shows the maintainers are aware that IP is a *lossy* proxy for identity, yet the burst multiplier is a fixed heuristic, not a fix: it only raises the shared bucket size, it does not separate distinct principals sharing the IP.

This mirrors the `checkLastBlockAction` defect exactly: the check's *intended* subject is "one distinct actor," but the actual key used (`IpAddr`, analogous to `msg.sender` in a shared relay) can represent many unrelated actors. Unlike `SwQos`'s later per-peer connection table, which keys by `(ip, pubkey)` [6](#0-5) , the earlier `ConnectionRateLimiter` gate has no such disambiguation and runs first, so it can reject/close connections before pubkey-based logic is ever reached.

### Impact Explanation
Any unprivileged/unstaked user sharing a public IP with other traffic (NAT gateways, VPN exit nodes, cloud NAT gateways, or third-party transaction relayers that proxy many end users through one egress IP) can have their legitimate, low-rate QUIC/TPU connection attempts dropped due to unrelated traffic from the same IP exceeding the shared budget. This is a remote, non-RPC (TPU/QUIC) degradation vector: connections are refused/closed (`incoming.ignore()`, `connection_rate_limited_per_ipaddr`) purely due to IP co-location, not due to any action by the affected user, and without any malicious intent required from any party. This can delay time-sensitive transactions (e.g., liquidations, arbitrage, or time-boxed swaps), producing fund-loss-adjacent outcomes for the victims stuck behind a busy IP.

### Likelihood Explanation
Medium: shared-IP scenarios (corporate NAT, cloud NAT gateways, VPNs, and third-party relayer/forwarding services that submit many users' transactions through one process/IP to reduce infrastructure cost) are common in production Solana usage. No attacker action is required — ordinary heavy but legitimate use by any single tenant behind a shared IP triggers the effect on cohabiting tenants.

### Recommendation
Do not gate solely on `IpAddr` for the pre-handshake/post-handshake QUIC connection admission decision. Options:
- Combine IP with a lightweight distinguishing signal earlier (e.g., defer strict per-IP throttling until after the pubkey is known, and rate-limit `(ip, pubkey)` similar to `SwQos`'s connection table key, rather than IP alone).
- Make the per-IP burst/limit configurable and scaled based on observed distinct-pubkey diversity behind an IP, rather than a fixed heuristic multiplier.
- At minimum, document this as a known limitation and expose per-IP limiter stats so operators can detect and mitigate false throttling of legitimate co-located clients.

### Proof of Concept
1. Configure a validator with default TPU QUIC settings (`max_connections_per_ipaddr_per_min = 8`, burst = 80, see `DEFAULT_MAX_CONNECTIONS_PER_IPADDR_PER_MINUTE`).
2. Two unrelated, unstaked clients (A and B) both originate from the same public IP (e.g., behind the same corporate NAT/cloud NAT gateway).
3. Client A alone opens/closes ≥80 QUIC connections within a minute (normal behavior for a busy legitimate service, no malicious intent).
4. Client B's subsequent connection attempts from the same IP are rejected by `rate_limiter.is_allowed`/`register_connection` (`connection_rate_limited_per_ipaddr` stat increments) purely due to A's activity, even though B never exceeded any limit itself [2](#0-1) .

### Citations

**File:** streamer/src/nonblocking/connection_rate_limiter.rs (L6-29)
```rust
/// Limits the rate of connections per IP address.
pub struct ConnectionRateLimiter {
    limiter: KeyedRateLimiter<IpAddr>,
}

/// The threshold of the size of the connection rate limiter map. When
/// the map size is above this, we will trigger a cleanup of older
/// entries used by past requests.
const CONNECTION_RATE_LIMITER_CLEANUP_SIZE_THRESHOLD: usize = 100_000;

impl ConnectionRateLimiter {
    /// Create a new rate limiter per IpAddr. The rate is specified as the count per minute to allow for
    /// less frequent connections. Higher limit also allows higher bursts.
    /// num_shards controls how many shards are used in the underlying dashmap,
    /// should be set >= number of contending threads.
    pub fn new(limit_per_minute: u64, max_burst: u64, num_shards: usize) -> Self {
        Self {
            limiter: KeyedRateLimiter::new(
                CONNECTION_RATE_LIMITER_CLEANUP_SIZE_THRESHOLD,
                TokenBucket::new(limit_per_minute, max_burst, limit_per_minute as f64 / 60.0),
                num_shards,
            ),
        }
    }
```

**File:** streamer/src/nonblocking/quic.rs (L270-276)
```rust
    let rate_limiter = Arc::new(ConnectionRateLimiter::new(
        quic_server_params.max_connections_per_ipaddr_per_min,
        // allow for 10x burst to make sure we can accommodate legitimate
        // bursts from container environments running multiple pods on same IP
        quic_server_params.max_connections_per_ipaddr_per_min * 10,
        num_shards,
    ));
```

**File:** streamer/src/nonblocking/quic.rs (L358-369)
```rust
            // then perform per IpAddr rate limiting
            if !rate_limiter.is_allowed(&incoming.remote_address().ip()) {
                stats
                    .connection_rate_limited_per_ipaddr
                    .fetch_add(1, Ordering::Relaxed);
                debug!(
                    "Ignoring incoming connection from {} due to per-IP rate limiting.",
                    incoming.remote_address()
                );
                incoming.ignore();
                continue;
            }
```

**File:** streamer/src/nonblocking/quic.rs (L480-493)
```rust
                // now that we have observed the handshake we can be certain
                // that the initiator owns an IP address, we can update rate
                // limiters on the server
                if !rate_limiter.register_connection(&from.ip()) {
                    debug!("Reject connection from {from:?} -- rate limiting exceeded");
                    stats
                        .connection_rate_limited_per_ipaddr
                        .fetch_add(1, Ordering::Relaxed);
                    new_connection.close(
                        CONNECTION_CLOSE_CODE_DISALLOWED.into(),
                        CONNECTION_CLOSE_REASON_DISALLOWED,
                    );
                    return;
                }
```

**File:** streamer/src/quic.rs (L53-56)
```rust
/// The new connections per minute from a particular IP address.
/// Heuristically set to the default maximum concurrent connections
/// per IP address. Might be adjusted later.
pub const DEFAULT_MAX_CONNECTIONS_PER_IPADDR_PER_MINUTE: u64 = 8;
```

**File:** streamer/src/nonblocking/swqos.rs (L209-219)
```rust
        if let Some((last_update, cancel_connection, stream_counter)) = connection_table_l
            .try_add_connection(
                ConnectionTableKey::new(remote_addr.ip(), conn_context.remote_pubkey),
                remote_addr.port(),
                client_connection_tracker,
                Some(connection.clone()),
                conn_context.peer_type(),
                conn_context.last_update.clone(),
                max_connections_per_peer,
                || Arc::new(ConnectionStreamCounter::new()),
            )
```
