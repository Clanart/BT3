### Title
Global (non-keyed) QUIC connection-rate token bucket can be drained by any unprivileged multi-source client to block legitimate transaction submission - (File: `streamer/src/nonblocking/quic.rs`)

### Summary
The Scroll report describes a rate limiter whose *total* (shared) capacity can be exhausted by a single unprivileged actor cycling deposits/withdrawals, denying service to everyone else until an admin manually resets it. Agave's QUIC/TPU ingress path contains a structurally identical primitive: `overall_connection_rate_limiter`, a single global `TokenBucket` shared by every inbound connection regardless of source, sitting alongside a *per-IP* `ConnectionRateLimiter`. The per-IP limiter isolates individual IPs from each other, but nothing isolates the shared global bucket from being drained by many distinct IPs acting in aggregate, each individually staying under its own per-IP cap.

### Finding Description
In `run_server` the server constructs two limiters: [1](#0-0) 

- `rate_limiter`: a `ConnectionRateLimiter` keyed by `IpAddr` — isolates abuse per source IP.
- `overall_connection_rate_limiter`: a single, un-keyed `TokenBucket::new(MAX_CONNECTION_BURST, MAX_CONNECTION_BURST, TOTAL_CONNECTIONS_PER_SECOND)` (burst 1000, refill 2500/s) — a *global total limit* shared by all peers. [2](#0-1) 

The accept loop first does a cheap peek of the global bucket (`current_tokens() == 0`) before accepting, and after a real QUIC handshake completes, `setup_connection` performs the real per-IP check followed by consumption of the shared global bucket: [3](#0-2) 

The corrupted/exhausted value is the shared `tokens` counter inside this single `TokenBucket` instance (`net-utils/src/token_bucket.rs`). Because `consume_tokens` on this bucket is called with no key, every successfully-handshaked connection from *any* IP decrements the same counter: [4](#0-3) 

The per-IP `ConnectionRateLimiter` (default 8/min, burst up to 80) only prevents a *single* IP from monopolizing the pool; it does nothing to protect the shared global pool from being drained by many distinct IPs each individually complying with their own per-IP quota. An attacker who can complete QUIC handshakes from a modest pool of source IPs (which is the exact, by-design, unprivileged threat model for the public TPU/QUIC ingress port — it must accept connections from unstaked/unknown senders to allow ordinary transaction submission) can sustain draining of the shared bucket, causing `incoming.ignore()` / `new_connection.close(CONNECTION_CLOSE_CODE_DISALLOWED, ...)` to be applied to legitimate, unrelated senders (including staked validators forwarding transactions) whose connection attempts arrive while the global pool is empty: [5](#0-4) 

### Impact Explanation
This is a non-RPC remote exhaustion/degradation of the validator's transaction-ingestion (TPU/QUIC) path: once the shared global bucket is empty, *all* new incoming connections — not just the attacker's — are refused or ignored until the bucket refills, degrading transaction submission for the whole validator regardless of sender identity or stake. This falls squarely into the "QUIC/TPU... non-RPC remote exhaustion/crash" valid-impact category, since it is triggerable by unprivileged clients with no special protocol/leader/staking assumptions.

### Likelihood Explanation
Sustaining exhaustion requires completing real QUIC handshakes at a rate near `TOTAL_CONNECTIONS_PER_SECOND` (2500/s) from a sufficiently diverse set of source IPs to avoid the per-IP rate limiter (each IP capped at 8/min, burst 80). This is a real but non-trivial bar — it requires either a modest pool of source IPs (cheap cloud/NAT egress addresses) or repeated short-lived connections that complete the handshake and then disconnect (each new handshake still consumes one token). No stake-weighting or per-key isolation exists at this specific chokepoint, so the cost of the attack scales only with the number of distinct source IPs the attacker can realistically use, not with stake or any other economic cost — matching the original report's "only cost is [connection attempt] fees."

### Recommendation
- Consider making the global connection admission budget stake/identity-aware, or partitioning it (e.g., a portion reserved for handshakes that subsequently present a staked identity) rather than a single first-come-first-served shared bucket consumed equally by any source.
- Consider raising `MAX_CONNECTION_BURST`/`TOTAL_CONNECTIONS_PER_SECOND` dynamically or applying a secondary check that weighs the *diversity* of recent source IPs so that a burst originating from many distinct low-reputation IPs is throttled more aggressively than organic traffic.
- Add metrics/alerting keyed on the ratio of distinct-IP connection attempts to `connection_rate_limited_across_all` to detect this specific distributed-drain pattern in production (the stats field already exists — `connection_rate_limited_across_all`). [6](#0-5) 

### Proof of Concept
1. Spin up a pool of N distinct client IPs (containers/VPS with different egress addresses), each configured to stay below the per-IP threshold `max_connections_per_ipaddr_per_min` (default 8/min, burst 80) as enforced by `ConnectionRateLimiter::register_connection`.
2. From each IP, repeatedly open-and-drop minimal QUIC connections to the validator's TPU/QUIC port at a moderate rate (e.g., a few per second per IP), aggregating to approach `TOTAL_CONNECTIONS_PER_SECOND` (2500/s) sustained.
3. Each successful handshake decrements the single shared `overall_connection_rate_limiter` bucket via `setup_connection`'s `overall_connection_rate_limiter.consume_tokens(1)` call.
4. Once the shared bucket is empty, observe that a legitimate, previously-uninvolved client's connection attempt is rejected at `overall_connection_rate_limiter.current_tokens() == 0` (or fails consumption post-handshake), even though that legitimate client never violated its own per-IP quota — confirmed by the `connection_rate_limited_across_all` stat incrementing for connections unrelated to the attacker's own IPs. [7](#0-6) 

**Note on completeness:** I also located `TokenBucket`/`consume_tokens` usage in `core/src/repair/serve_repair.rs` and `core/src/forwarding_stage.rs` that may contain a similar or stronger analog (e.g., in the repair-response or forwarding-bandwidth budgets), but I was unable to inspect their surrounding logic in the remaining tool budget to confirm whether those buckets are keyed per-peer or globally shared. If a stronger analog exists there, it was not verified here.

### Citations

**File:** streamer/src/nonblocking/quic.rs (L70-76)
```rust
/// Total new connection counts per second. Heuristically taken from
/// the default staked and unstaked connection limits. Might be adjusted
/// later.
const TOTAL_CONNECTIONS_PER_SECOND: f64 = 2500.0;

/// Max burst of connections above sustained rate to pass through
const MAX_CONNECTION_BURST: u64 = 1000;
```

**File:** streamer/src/nonblocking/quic.rs (L270-281)
```rust
    let rate_limiter = Arc::new(ConnectionRateLimiter::new(
        quic_server_params.max_connections_per_ipaddr_per_min,
        // allow for 10x burst to make sure we can accommodate legitimate
        // bursts from container environments running multiple pods on same IP
        quic_server_params.max_connections_per_ipaddr_per_min * 10,
        num_shards,
    ));
    let overall_connection_rate_limiter = Arc::new(TokenBucket::new(
        MAX_CONNECTION_BURST,
        MAX_CONNECTION_BURST,
        TOTAL_CONNECTIONS_PER_SECOND,
    ));
```

**File:** streamer/src/nonblocking/quic.rs (L342-357)
```rust
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

**File:** net-utils/src/token_bucket.rs (L77-94)
```rust
    pub fn consume_tokens(&self, request_size: u64) -> Result<u64, u64> {
        let now = self.time_us();
        self.update_state(now);
        match self.tokens.fetch_update(
            Ordering::AcqRel,  // winner publishes new amount
            Ordering::Acquire, // everyone observed correct number
            |tokens| {
                if tokens >= request_size {
                    Some(tokens.saturating_sub(request_size))
                } else {
                    None
                }
            },
        ) {
            Ok(prev) => Ok(prev.saturating_sub(request_size)),
            Err(prev) => Err(request_size.saturating_sub(prev)),
        }
    }
```

**File:** streamer/src/quic.rs (L207-212)
```rust
    // Number of connections to the endpoint exceeding the allowed limit
    // regardless of the source IP address.
    pub(crate) connection_rate_limited_across_all: AtomicUsize,
    // Per IP rate-limiting is triggered each time when there are too many connections
    // opened from a particular IP address.
    pub(crate) connection_rate_limited_per_ipaddr: AtomicUsize,
```
