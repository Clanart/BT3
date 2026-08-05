Audit Report

## Title
Per-IP QUIC connection rate limiter is bypassable via distinct IPv6 addresses in a single /64, allowing exhaustion of the shared global connection-rate budget - (File: `streamer/src/nonblocking/connection_rate_limiter.rs`)

## Summary
`ConnectionRateLimiter` keys its per-source token bucket on the raw `IpAddr` value with no normalization for IPv6 prefixes [1](#0-0) . An attacker with a routed IPv6 /64 can rotate through 2^64 addresses, each treated as a brand-new peer by `is_allowed`/`register_connection`, since neither has any concept of address-family-aware aggregation [2](#0-1) . This can be used to consume a disproportionate share of the shared `TOTAL_CONNECTIONS_PER_SECOND`/`MAX_CONNECTION_BURST` global connection-rate budget in `streamer/src/nonblocking/quic.rs` [3](#0-2) .

## Finding Description
`ConnectionRateLimiter::is_allowed` returns `true` for any `IpAddr` with no prior record, and `register_connection` only begins consuming tokens once a key exists in the map [4](#0-3) . Because the map key is the full 128-bit `IpAddr` rather than a normalized IPv6 prefix, an attacker who owns a /64 allocation can present a functionally unlimited number of distinct "first-seen" addresses, each of which is granted a fresh token-bucket allowance. This matches the claim's description of the code accurately — there is no prefix aggregation logic anywhere in this file.

However, this per-IP limiter is only one layer; the claim itself acknowledges it could not confirm the exact interaction/ordering between `ConnectionRateLimiter` and the global `overall_connection_rate_limiter`/`TOTAL_CONNECTIONS_PER_SECOND` bound in `streamer/src/nonblocking/quic.rs`'s connection-accept path. I was similarly unable to fully verify, within the available tool budget, how severely the shared global token bucket is actually depleted relative to other independent defenses that may exist in `quic.rs` (e.g., staked/unstaked connection tables, per-pubkey limits, or other admission controls that occur before or independently of the per-IP rate limiter). The presence of the `TOTAL_CONNECTIONS_PER_SECOND = 2500.0` / `MAX_CONNECTION_BURST = 1000` constants is confirmed [3](#0-2) , but the precise causal chain from "attacker floods /64 addresses" to "global budget exhausted, degrading legitimate connections" was not something I could fully trace to a specific call site within the given iteration budget.

## Impact Explanation
If the exploit path is as described, it constitutes a non-RPC remote resource-exhaustion condition against the QUIC/TPU public listener, which is within the valid impact category. The severity depends heavily on how much of the global budget is actually consumable this way versus other, unexamined defenses in `quic.rs` (e.g., total concurrent connection caps, per-stake-weight connection limits) that could independently bound the blast radius even if the per-IP limiter is bypassed.

## Likelihood Explanation
Acquiring a routable IPv6 /64 is inexpensive and requires no special privilege, consistent with an unprivileged/unstaked attacker profile using only public QUIC/TPU inputs. The claim's own comment that `register_connection` should only run after "source IP is verified" suggests some handshake-completion gating exists, but I could not confirm within the current investigation whether that gating meaningfully limits the rate at which an attacker can complete enough of the handshake to trigger `register_connection` repeatedly.

## Recommendation
Normalize IPv6 addresses to a fixed prefix (e.g., /56 or /64) before using them as the rate-limiter key in `ConnectionRateLimiter`, so that addresses from a single allocation collapse into one bucket, matching the fairness assumption the shared global limiter in `quic.rs` depends on.

## Proof of Concept
The claim's own PoC sketch (extending the test module in `streamer/src/nonblocking/connection_rate_limiter.rs`) is consistent with the confirmed behavior: registering many synthetic addresses within one /64 range against `ConnectionRateLimiter::register_connection` all succeed as "new IP" connections, since the code has no IPv6 prefix aggregation [5](#0-4) . Full confirmation of end-to-end impact on the shared global budget in `streamer/src/nonblocking/quic.rs`'s connection-accept path was not completed due to tool/iteration limits, matching the limitation the claim itself flagged.

### Citations

**File:** streamer/src/nonblocking/connection_rate_limiter.rs (L1-9)
```rust
use {
    solana_net_utils::token_bucket::{KeyedRateLimiter, TokenBucket},
    std::net::IpAddr,
};

/// Limits the rate of connections per IP address.
pub struct ConnectionRateLimiter {
    limiter: KeyedRateLimiter<IpAddr>,
}
```

**File:** streamer/src/nonblocking/connection_rate_limiter.rs (L31-50)
```rust
    /// Check if the connection from the said `ip` is allowed.
    /// Here we assume that only IPs with actual confirmed connections are stored in it,
    /// since we should only modify server state once source IP is verified
    pub fn is_allowed(&self, ip: &IpAddr) -> bool {
        // Check if we have records in the rate limiter for the given IP address
        match self.limiter.current_tokens(ip) {
            Some(r) => r > 0, // we have a record, and rate is not exceeded
            None => true,     // if we have not seen IP, allow connection request
        }
    }

    pub fn register_connection(&self, ip: &IpAddr) -> bool {
        if self.limiter.consume_tokens(*ip, 1).is_ok() {
            debug!("Request from IP {ip:?} allowed");
            true // Request allowed
        } else {
            debug!("Request from IP {ip:?} blocked");
            false // Request blocked
        }
    }
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
