[1](#0-0) [2](#0-1) [1](#0-0) [3](#0-2) [4](#0-3) [5](#0-4) [5](#0-4) [6](#0-5)

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
