## Analysis

The external report's core defect is a **single shared, identity-agnostic budget with a hard cap** that any unprivileged actor can exhaust, denying access to *all other* actors regardless of legitimacy or priority (rate limiter in `RateLimiter.sol` applied uniformly to every L2→L1 message, blocking honest withdrawals once a whale/attacker fills the 1000 ETH/24h bucket).

The closest Agave analog is the **global QUIC connection admission gate** in the TPU/TPU-forward streamer, which is deliberately designed with per-IP and stake-weighted (SwQoS) admission controls, but is gated behind one *stake-agnostic, IP-agnostic* shared token bucket that is checked before any staked-vs-unstaked prioritization occurs.

### Title
Global QUIC connection rate limiter is stake/IP-agnostic and can be exhausted by distributed unstaked connections, starving staked TPU traffic - (File: streamer/src/nonblocking/quic.rs)

### Summary
`run_server` in `streamer/src/nonblocking/quic.rs` gates every incoming QUIC connection (TPU / TPU-forward) behind a single shared `TokenBucket` (`overall_connection_rate_limiter`) with a fixed capacity (`MAX_CONNECTION_BURST = 1000`) refilled at `TOTAL_CONNECTIONS_PER_SECOND = 2500`. This bucket is consumed identically for staked and unstaked peers, and is checked/consumed *before* the SwQoS logic (`qos.try_add_connection`, stake-weighted stream limits) ever runs. [1](#0-0) [2](#0-1) 

### Finding Description
The per-IP `ConnectionRateLimiter` (`DEFAULT_MAX_CONNECTIONS_PER_IPADDR_PER_MINUTE = 8`) only limits a single source IP, but is entirely separate from — and subordinate to — the global `overall_connection_rate_limiter`, which has no notion of stake or identity at all: [3](#0-2) 

The actual token consumption happens in `setup_connection`, right after a peer's QUIC handshake completes but *before* `qos.try_add_connection` (which applies `max_connections_per_staked_peer` / `max_connections_per_unstaked_peer` and SwQoS prioritization) is invoked: [4](#0-3) 

Because each unique source IP only needs to stay under 8 connections/minute individually, an attacker controlling many distinct IPs (cheap/rotating VPS IPs, botnets, or a NAT-diverse residential proxy pool — none of which require stake, a leaked key, or trust) can complete handshakes fast enough to continuously drain the shared 1000-token / 2500-per-second budget. Every connection attempt from *any* IP — staked or not — hits the exact same bucket, so once it's drained, `overall_connection_rate_limiter.consume_tokens(1)` fails for legitimate staked validators/RPC forwarders trying to open new TPU connections, and their connection is closed with `CONNECTION_CLOSE_CODE_DISALLOWED`: [5](#0-4) 

The existing guards — per-IP `ConnectionRateLimiter` and `SwQos`'s `max_connections_per_staked_peer`/`max_connections_per_unstaked_peer` — do **not** protect against this path because they only apply *after* the shared global gate is passed. There is no stake-weighted carve-out or reservation in the global bucket itself, so an unprivileged, low-stake/no-stake distributed flood can starve all new TPU connections cluster/node-wide, exactly mirroring the original bug class: a single shared, hard-capped resource that any unprivileged party can exhaust to deny others access.

### Impact Explanation
If new TPU connections cannot be established with the current leader, transactions (including time-sensitive ones — swaps, liquidations, redemptions) fail to be submitted, which can cause direct fund loss or missed execution windows for users relying on that validator's TPU port during the attack window. This falls under the accepted category of "non-RPC remote exhaustion/crash" affecting QUIC/TPU, causing degraded transaction submission and potential fund loss due to missed execution.

### Likelihood Explanation
The attack requires no stake, no privileged role, and no malicious-peer/validator assumption — only the ability to open many QUIC connections from a diverse set of IPs, which is inexpensive (cloud IP churn, IPv6 address diversity, etc.) and does not need to be sustained per-IP above the existing per-IP threshold of 8/min. The likelihood is moderate-to-high given the constants (`MAX_CONNECTION_BURST = 1000`, `TOTAL_CONNECTIONS_PER_SECOND = 2500`) are shared cluster-wide defaults, not scaled per validator's expected legitimate load, and refill happens on wall-clock time regardless of legitimate demand spikes (e.g., during network congestion when connection churn is naturally higher).

### Recommendation
Reserve a portion of the global connection budget exclusively for stake-verified connections (post-handshake, pre-SwQoS), or move the SwQoS stake-based admission check ahead of/alongside the global token consumption so that staked peers are not competing for the same pool as anonymous/unstaked peers. Alternatively, split `overall_connection_rate_limiter` into staked and unstaked sub-buckets analogous to `max_staked_connections`/`max_unstaked_connections`.

### Proof of Concept
1. From N distinct source IPs (N large enough that each individually stays under 8 conns/min), continuously open new QUIC connections to a validator's TPU endpoint.
2. Each successful handshake calls `rate_limiter.register_connection` (passes, since each IP is under its own cap) then `overall_connection_rate_limiter.consume_tokens(1)`.
3. Once aggregate connection attempts exceed `TOTAL_CONNECTIONS_PER_SECOND` (2500/s) sustained, the shared bucket empties.
4. A legitimate staked validator's new connection attempt now hits `overall_connection_rate_limiter.consume_tokens(1).is_err()` at [6](#0-5)  and is closed with `CONNECTION_CLOSE_CODE_DISALLOWED`, before SwQoS stake-prioritization logic is ever consulted.

### Citations

**File:** streamer/src/nonblocking/quic.rs (L61-65)
```rust
pub(crate) const CONNECTION_CLOSE_CODE_DISALLOWED: u32 = 2;
pub(crate) const CONNECTION_CLOSE_REASON_DISALLOWED: &[u8] = b"disallowed";

const CONNECTION_CLOSE_CODE_TOO_MANY: u32 = 4;
const CONNECTION_CLOSE_REASON_TOO_MANY: &[u8] = b"too_many";
```

**File:** streamer/src/nonblocking/quic.rs (L70-76)
```rust
/// Total new connection counts per second. Heuristically taken from
/// the default staked and unstaked connection limits. Might be adjusted
/// later.
const TOTAL_CONNECTIONS_PER_SECOND: f64 = 2500.0;

/// Max burst of connections above sustained rate to pass through
const MAX_CONNECTION_BURST: u64 = 1000;
```

**File:** streamer/src/nonblocking/quic.rs (L277-281)
```rust
    let overall_connection_rate_limiter = Arc::new(TokenBucket::new(
        MAX_CONNECTION_BURST,
        MAX_CONNECTION_BURST,
        TOTAL_CONNECTIONS_PER_SECOND,
    ));
```

**File:** streamer/src/nonblocking/quic.rs (L331-369)
```rust
        if let Ok(Some(incoming)) = timeout_connection {
            // our connection/handshake abuse mitigation policy is one of shed
            // fast and bound resource consumption. attempting to be "smarter"
            // before a peer has asserted control over their ip address by
            // completing the retry challenge creates a scenario whereby peers
            // can attack one another via ip spoofing. employ the following
            // * limit duration of in-flight connection attempts with a timeout
            // * protect against connection attempt bursts with a global rate-limiter
            // * rate-limit abusive peers by (control-asserted) ip
            // * cap total connections per-peer/ip

            stats
                .total_incoming_connection_attempts
                .fetch_add(1, Ordering::Relaxed);

            // check overall connection request rate limiter
            if overall_connection_rate_limiter.current_tokens() == 0 {
                stats
                    .connection_rate_limited_across_all
                    .fetch_add(1, Ordering::Relaxed);
                debug!(
                    "Ignoring incoming connection from {} due to overall rate limit.",
                    incoming.remote_address()
                );
                incoming.ignore();
                continue;
            }
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

**File:** streamer/src/nonblocking/quic.rs (L483-508)
```rust
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

                if overall_connection_rate_limiter.consume_tokens(1).is_err() {
                    debug!(
                        "Reject connection from {:?} -- total rate limiting exceeded",
                        from.ip()
                    );
                    stats
                        .connection_rate_limited_across_all
                        .fetch_add(1, Ordering::Relaxed);
                    new_connection.close(
                        CONNECTION_CLOSE_CODE_DISALLOWED.into(),
                        CONNECTION_CLOSE_REASON_DISALLOWED,
                    );
                    return;
                }
```
