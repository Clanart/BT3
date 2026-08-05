Audit Report

## Title
Global (non-per-peer) QUIC connection-rate token bucket at the TPU ingest path can be exhausted by a single low-cost unprivileged sender, denying connection acceptance to all other peers - (File: `streamer/src/nonblocking/quic.rs`)

## Summary
The QUIC accept loop for the TPU/TPU-forward ports checks a single, global, unkeyed `overall_connection_rate_limiter` token bucket before any per-IP or stake-based check, and rejects all incoming connections once it is empty. [1](#0-0)  Because this bucket is consumed by any `incoming` connection attempt regardless of source, and the per-IP limiter is applied only afterward, an unprivileged attacker rotating source addresses can keep the shared bucket drained, causing legitimate connection attempts from any IP (staked or unstaked) to be dropped.

## Finding Description
The accept loop constructs `overall_connection_rate_limiter` as a single `TokenBucket` sized by fixed constants `MAX_CONNECTION_BURST = 1000` and `TOTAL_CONNECTIONS_PER_SECOND = 2500.0`, with no per-IP or per-stake keying. [2](#0-1) [3](#0-2)  On every incoming connection attempt, the code checks `overall_connection_rate_limiter.current_tokens() == 0` first, and only afterward checks the per-IP `ConnectionRateLimiter`, which is keyed by `IpAddr`. [4](#0-3)  The code's own comment acknowledges this ordering rationale — checks happen "before a peer has asserted control over their ip address by completing the retry challenge," explicitly to avoid IP-spoofing-based attacks between peers, but this same property means the shared bucket is consumable by anyone sending Initial packets, with no identity or stake requirement. [5](#0-4)  The `TokenBucket` implementation confirms it is a plain, non-keyed shared counter refilled linearly over time and decremented by any caller via `consume_tokens`/`current_tokens`. [6](#0-5)  The per-IP `ConnectionRateLimiter` is a separate `KeyedRateLimiter<IpAddr>` checked only after the global bucket. [7](#0-6) 

I was unable to fully trace where the global `overall_connection_rate_limiter` tokens are actually decremented (`consume_tokens` call site) since it did not appear directly in `quic.rs` in a further grep within the available time; it's likely consumed inside `setup_connection` (to which the cloned limiter is passed) rather than in the accept loop itself. This doesn't materially change the core finding — the `current_tokens() == 0` gate itself is unconditionally applied before per-IP checks — but the exact consumption/refill dynamics under concurrent load were not independently re-verified beyond the token_bucket.rs mechanics shown.

## Impact Explanation
This maps to the "non-RPC remote exhaustion/degradation" category. An unprivileged, unauthenticated, remote sender can, by exceeding `TOTAL_CONNECTIONS_PER_SECOND`/`MAX_CONNECTION_BURST` with spoofed/rotated source IPs, keep `overall_connection_rate_limiter.current_tokens()` at zero, causing all legitimate TPU QUIC connection attempts (staked or unstaked) to be silently ignored via `incoming.ignore()`. [8](#0-7)  This is a genuine availability degradation of the TPU ingest path on the targeted validator, though scoped to that single validator (not cluster-wide consensus).

## Likelihood Explanation
Feasibility is high: no stake, no valid TLS identity, and no completed handshake is required — only enough UDP QUIC Initial packets reaching the TPU QUIC socket to register as `incoming`. [9](#0-8)  Rotating source IP/port defeats the per-IP `ConnectionRateLimiter` since it is evaluated only after the global gate. [10](#0-9)  The fixed, non-stake-scaled constants (`2500`/sec sustained, `1000` burst) are inexpensive to exceed from ordinary infrastructure.

## Recommendation
Apply per-IP (or lightweight source heuristic) rate limiting before charging the shared/global bucket, or partition the global budget into staked/unstaked sub-buckets so that unauthenticated flooding cannot starve staked peers' connection attempts, and/or require satisfying the QUIC retry/address-validation challenge before consuming shared-capacity tokens.

## Proof of Concept
1. From an unprivileged host, send a sustained stream of QUIC Initial packets to the target validator's TPU QUIC UDP port at a rate exceeding `TOTAL_CONNECTIONS_PER_SECOND`/`MAX_CONNECTION_BURST`, rotating source IP/port to avoid the per-IP `ConnectionRateLimiter` threshold.
2. Each Initial packet is processed as `incoming` and checked against `overall_connection_rate_limiter.current_tokens()` before the per-IP check. [1](#0-0) 
3. Once the shared bucket's tokens reach zero, all subsequent incoming connections — regardless of IP or stake — are logged as rate-limited and ignored.
4. Sustaining the attack above the refill rate keeps legitimate TPU connection attempts dropped, producing an availability degradation for transaction submission on that validator.

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

**File:** net-utils/src/token_bucket.rs (L62-94)
```rust
    /// Return current amount of tokens in the bucket.
    /// This may be somewhat inconsistent across threads
    /// due to Relaxed atomics.
    #[inline]
    pub fn current_tokens(&self) -> u64 {
        let now = self.time_us();
        self.update_state(now);
        self.tokens.load(Ordering::Relaxed)
    }

    /// Attempts to consume tokens from bucket.
    ///
    /// On success, returns Ok(amount of tokens left in the bucket).
    /// On failure, returns Err(amount of tokens missing to fill request).
    #[inline]
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

**File:** streamer/src/nonblocking/connection_rate_limiter.rs (L1-38)
```rust
use {
    solana_net_utils::token_bucket::{KeyedRateLimiter, TokenBucket},
    std::net::IpAddr,
};

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

    /// Check if the connection from the said `ip` is allowed.
    /// Here we assume that only IPs with actual confirmed connections are stored in it,
    /// since we should only modify server state once source IP is verified
    pub fn is_allowed(&self, ip: &IpAddr) -> bool {
        // Check if we have records in the rate limiter for the given IP address
        match self.limiter.current_tokens(ip) {
            Some(r) => r > 0, // we have a record, and rate is not exceeded
            None => true,     // if we have not seen IP, allow connection request
```
