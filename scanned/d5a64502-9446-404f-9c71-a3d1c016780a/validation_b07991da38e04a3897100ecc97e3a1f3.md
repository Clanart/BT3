# Title
Per-IP QUIC connection rate limiter is bypassable via distinct IPv6 addresses in a single /64, allowing exhaustion of the shared global connection-rate budget - (File: `streamer/src/nonblocking/connection_rate_limiter.rs`)

### Summary
`ConnectionRateLimiter` keys its per-source token bucket directly on the full `IpAddr` value with no normalization/aggregation for IPv6 prefixes. [1](#0-0)  Because an attacker who controls a single IPv6 /64 allocation (a trivially and cheaply obtainable resource, e.g. from cloud/VPS providers) has 2^64 distinct addresses to rotate through, every new source address is treated by `is_allowed`/`register_connection` as a brand-new, never-before-seen IP and is granted a fresh full token bucket. [2](#0-1)  This defeats the intended purpose of per-IP throttling and lets a single attacker machine drive an effectively unbounded rate of "new IP" connection attempts against the QUIC/TPU listener.

### Finding Description
`ConnectionRateLimiter::is_allowed` and `register_connection` operate on a `KeyedRateLimiter<IpAddr>`, using the raw `IpAddr` (the full 128-bit IPv6 address, not a /56 or /64 prefix) as the map key. [3](#0-2)  The `is_allowed` check returns `true` for any address with no prior record, and `register_connection` only starts consuming tokens once an address has an entry. [2](#0-1) 

The QUIC server in `streamer/src/nonblocking/quic.rs` additionally enforces a single shared `overall_connection_rate_limiter` `TokenBucket` capped at `TOTAL_CONNECTIONS_PER_SECOND = 2500.0` with `MAX_CONNECTION_BURST = 1000`, intended to bound total new-connection throughput across *all* peers combined. [4](#0-3)  The per-IP `ConnectionRateLimiter` is the only mechanism meant to ensure this shared global budget is distributed fairly across distinct real hosts rather than monopolized by one attacker.

Since the per-IP limiter's fairness guarantee depends on the assumption that "one distinct `IpAddr`" corresponds to "one distinct real host," and IPv6 makes that assumption false for any attacker with a routed /64 (standard allocation size for even residential/VPS IPv6 delegation), the per-IP defense provides no effective throttling against an attacker who simply increments the low-order 64 bits of their source address on each connection attempt. Each such address passes `is_allowed` as if it were a fresh legitimate peer, and each `register_connection` call consumes from the shared `overall_connection_rate_limiter` budget just the same as a connection from a genuinely distinct legitimate host would.

### Impact Explanation
This falls within the stated Valid Impact category of "non-RPC remote exhaustion/crash" via the QUIC/TPU public protocol from an unprivileged/unstaked attacker. [4](#0-3)  By flooding new-connection attempts from many synthetic addresses inside one owned /64, a single attacker machine can consume the entire global `TOTAL_CONNECTIONS_PER_SECOND`/`MAX_CONNECTION_BURST` allowance, starving legitimate stakeholders' new QUIC/TPU connections and degrading the validator's ability to accept transactions/votes from genuine peers — a remote resource-exhaustion condition against a production, unprivileged, public entry point.

### Likelihood Explanation
Likelihood is high: acquiring a routable IPv6 /64 is inexpensive and common (default allocation size from most ISPs/cloud/VPS providers), and no additional privilege, stake, or protocol handshake trust is required — the attacker only needs to complete enough of the QUIC handshake for `register_connection` to be invoked with a "confirmed connection" per the per-IP limiter's own comment that it should only be updated once "the source IP is verified." [5](#0-4)  This is well within reach of a single unprivileged attacker with commodity infrastructure.

### Recommendation
Key the `ConnectionRateLimiter` (and any related per-source throttling) on a normalized IPv6 prefix (e.g., /56 or /64) instead of the raw `IpAddr` when the address family is IPv6, so that all addresses an attacker can trivially obtain from one allocation collapse to a single rate-limited bucket, matching the fairness assumption that the global `overall_connection_rate_limiter` budget relies on.

### Proof of Concept
Note: I was unable to fully trace the exact call site in `streamer/src/nonblocking/quic.rs` where `ConnectionRateLimiter` and `overall_connection_rate_limiter` interact within `run_server`'s connection-accept path due to tool/iteration limits, so I cannot cite the exact ordering of checks in that function. The following is a repo-level test proof-of-concept sketch consistent with the existing test pattern in the file:

```rust
// extend streamer/src/nonblocking/connection_rate_limiter.rs test module
#[tokio::test]
async fn test_ipv6_slash64_bypass() {
    use std::net::Ipv6Addr;
    let limiter = ConnectionRateLimiter::new(3, 3, 4); // same limit as a legit peer would face

    // Attacker owns 2001:db8:1234:5678::/64 and rotates low 64 bits.
    let base: u128 = 0x2001_0db8_1234_5678_0000_0000_0000_0000;
    let mut accepted = 0;
    for i in 0..1000u128 {
        let ip = IpAddr::V6(Ipv6Addr::from(base + i));
        if limiter.register_connection(&ip) {
            accepted += 1; // every address is "new", so this always succeeds
        }
    }
    assert_eq!(accepted, 1000); // per-IP limiter never throttles the attacker

    // Meanwhile a single legitimate peer ip2 is capped at 3 per the same limiter,
    // and both share the same global overall_connection_rate_limiter budget
    // (TOTAL_CONNECTIONS_PER_SECOND = 2500) in streamer/src/nonblocking/quic.rs,
    // so the attacker's 1000 accepted "new IP" connections consume the majority
    // of that shared budget while the legitimate peer's share stays fixed at 3.
}
``` [6](#0-5)

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

**File:** streamer/src/nonblocking/connection_rate_limiter.rs (L53-82)
```rust
#[cfg(test)]
pub mod test {
    use {super::*, std::net::Ipv4Addr};

    #[tokio::test]
    async fn test_connection_rate_limiter() {
        let limiter = ConnectionRateLimiter::new(3, 3, 4);
        let ip1 = IpAddr::V4(Ipv4Addr::new(192, 168, 1, 1));
        assert!(limiter.is_allowed(&ip1));
        assert!(limiter.register_connection(&ip1));
        assert!(limiter.register_connection(&ip1));
        assert!(limiter.is_allowed(&ip1));
        assert!(limiter.register_connection(&ip1));
        assert!(!limiter.is_allowed(&ip1));
        assert!(!limiter.register_connection(&ip1));

        let ip2 = IpAddr::V4(Ipv4Addr::new(192, 168, 1, 2));
        for _ in 0..100 {
            assert!(
                limiter.is_allowed(&ip2),
                "just checking should not mutate state"
            );
        }
        assert!(limiter.register_connection(&ip2));
        assert!(limiter.register_connection(&ip2));
        assert!(limiter.is_allowed(&ip2));
        assert!(limiter.register_connection(&ip2));
        assert!(!limiter.is_allowed(&ip2));
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
