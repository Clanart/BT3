## Summary

The external report's broken invariant is: **a shared, mutable "capacity" counter is read once and then acted upon later, with the decision to grant more resource splitting the check from the consumption**, so multiple actors can each observe the *same* stale counter value and all be granted the resource, exceeding the counter's actual capacity. In Agave, the closest unprivileged-exploitable analog is not in a bonding-curve mint (Agave has none), but in the **QUIC connection-acceptance rate limiter**, where the "should I admit this connection" check (`ConnectionRateLimiter::is_allowed` / `TokenBucket::current_tokens`) is a non-consuming peek that is separated in time (and across an `await` boundary/task spawn) from the actual token-consuming decision (`register_connection` / `consume_tokens`).

## Finding Description

In `streamer/src/nonblocking/quic.rs::run_server`, incoming QUIC connections are first screened with a **peek**, not a **reservation**: [1](#0-0) 

Both checks here (`overall_connection_rate_limiter.current_tokens() == 0` and `rate_limiter.is_allowed(...)`) only *read* the token bucket state without decrementing it: [2](#0-1) 

The actual atomic consumption only happens later, inside the spawned per-connection task `setup_connection`, **after** the QUIC/TLS handshake has already completed: [3](#0-2) 

`register_connection` and the overall bucket's `consume_tokens` do use an atomic `fetch_update` CAS loop internally, so each individual consumption is race-free with respect to other consumptions: [4](#0-3) 

However, the **check (peek) and the act (consume) are two separate, non-atomic operations spanning an expensive intervening step (the QUIC handshake)**. Any number of connection attempts from the same IP (or globally) that arrive concurrently will all observe `current_tokens() > 0` / `is_allowed() == true` on the *same* pre-handshake state, because none of them has decremented the bucket yet — the decrement only happens post-handshake in `register_connection`/`consume_tokens`. This is structurally identical to the reported bug's root cause: two "quote" computations reading the same shared counter independently, before either updates it, so the sum of grants exceeds what the counter should allow.

The code's own comments make clear the *intended* invariant that this breaks: [5](#0-4) 

"protect against connection attempt bursts with a global rate-limiter" and "shed fast" — but because the shedding decision is a stale peek, not a reservation, the burst-shedding guarantee does not hold for concurrent bursts.

## Impact Explanation

An unprivileged, unauthenticated remote peer (no stake, no special trust) can open many QUIC connections simultaneously against the TPU/QUIC-serving port. Because `is_allowed`/`current_tokens` are peeks rather than reservations, all of these concurrent attempts pass the cheap pre-handshake filters and proceed into the CPU-expensive QUIC/TLS handshake (`incoming.accept()` / `connecting.accept()`), and only afterwards — one by one, in the spawned tasks — does `register_connection`/`consume_tokens` actually enforce the limit and reject the excess. This lets a single IP force the validator to perform far more concurrent expensive handshakes than the configured `max_connections_per_ipaddr_per_min` and the global `TOTAL_CONNECTIONS_PER_SECOND`/`MAX_CONNECTION_BURST` are meant to permit, amplifying CPU consumption on the QUIC/TPU ingestion path. This matches the "QUIC/TPU ... non-RPC remote exhaustion/crash" impact category: it degrades the validator's ability to process legitimate transactions/gossip during the attack window without requiring any peer/validator trust or malicious-validator assumption — only an ordinary remote client capable of opening many parallel QUIC connections.

The blast radius is bounded somewhat by other hard caps (e.g., `ClientConnectionTracker`'s `max_concurrent_connections`), but those caps are shared across *all* peers, so this bug lets one abusive IP disproportionately consume that shared concurrent-handshake budget before the per-IP/global throttles ever get a chance to act, defeating their "shed fast" design purpose.

## Likelihood Explanation

Likelihood is high: the trigger requires nothing more than opening many QUIC connections to a validator's TPU/QUIC port from one IP within a short window — no stake, no protocol-level state, no cooperation from other nodes. The race window is not a narrow nanosecond race but is bounded by an entire TLS/QUIC handshake, which is comparatively long, making it easy in practice for an attacker to fire many parallel connection attempts before any single one completes its handshake and calls `register_connection`.

## Recommendation

Convert the pre-handshake filters from "peek" to "reserve": consume/reserve a token from both `overall_connection_rate_limiter` and `rate_limiter` (per-IP) *before* accepting/handshaking the connection, and refund/release the reservation if the handshake fails or times out. This makes the check-then-act sequence atomic with respect to the shared counters, matching the "Fair" fix pattern recommended in the source report (compute against the combined/atomic state before granting, not read stale shared state independently in multiple places).

## Proof of Concept

Conceptual PoC (would need to be run against a live/local validator or test harness by a background agent with execution access, since this environment is read-only):
1. Configure a local `streamer` QUIC server with a small `max_connections_per_ipaddr_per_min` and small `MAX_CONNECTION_BURST`.
2. From a single test client IP, open `N` (e.g., 500) concurrent QUIC connection attempts simultaneously (before any of them completes its TLS handshake).
3. Observe via `stats.total_incoming_connection_attempts` / `stats.outstanding_incoming_connection_attempts` that far more than `max_connections_per_ipaddr_per_min`/`MAX_CONNECTION_BURST` handshakes are attempted concurrently, because `is_allowed`/`current_tokens` never decremented before the handshake stage — only `register_connection` (called post-handshake) starts rejecting, by which point the CPU cost of the handshakes has already been paid.
4. Compare against the intended limiter capacity to show the excess concurrent handshake count exceeds the configured burst/rate limits. [6](#0-5)

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

**File:** streamer/src/nonblocking/quic.rs (L331-341)
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

```

**File:** streamer/src/nonblocking/quic.rs (L346-369)
```rust
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

**File:** streamer/src/nonblocking/quic.rs (L471-508)
```rust
    let from = connecting.remote_address();
    let res = timeout(QUIC_CONNECTION_HANDSHAKE_TIMEOUT, connecting).await;
    stats
        .outstanding_incoming_connection_attempts
        .fetch_sub(1, Ordering::Relaxed);
    if let Ok(connecting_result) = res {
        match connecting_result {
            Ok(new_connection) => {
                debug!("Got a connection {from:?}");
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

**File:** streamer/src/nonblocking/connection_rate_limiter.rs (L34-40)
```rust
    pub fn is_allowed(&self, ip: &IpAddr) -> bool {
        // Check if we have records in the rate limiter for the given IP address
        match self.limiter.current_tokens(ip) {
            Some(r) => r > 0, // we have a record, and rate is not exceeded
            None => true,     // if we have not seen IP, allow connection request
        }
    }
```

**File:** net-utils/src/token_bucket.rs (L76-94)
```rust
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
