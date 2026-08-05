[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** gossip/src/ping_pong.rs (L75-80)
```rust
/// max number of slots in [`PingCache::pings`] to probe when looking for a
/// reclaimable entry for a new ping. Probing only happens once the cache is
/// full. The chance of hitting at least one timed-out (evictable) slot is
/// `1 - (1 - f)^MAX_PING_PROBES`, where `f` is the fraction of entries that
/// have timed out. E.g. with `f = 0.5` that is `1 - 0.5^8` ~ 99.6%.
const MAX_PING_PROBES: usize = 8;
```

**File:** gossip/src/ping_pong.rs (L233-241)
```rust
        // If the existing ping is still in-flight don't send another one.
        let is_new_key = if let Some((expiry, _)) = self.pings.get(&remote_node) {
            if now < *expiry {
                return None;
            }
            false // existing entry will be updated in-place
        } else {
            true // no entry for this node yet
        };
```

**File:** gossip/src/ping_pong.rs (L243-264)
```rust
        // If this is a new entry and the pings store is at capacity,
        // probe random existing entries and evict the first timed-out one
        // (expiry in the past, peer never responded).
        // Decline if all probes are in-flight — avoids evicting challenges
        // still awaiting a Pong.
        if is_new_key && self.pings.len() >= self.max_pings {
            let n = self.pings.len();
            let mut evicted = false;
            for _ in 0..MAX_PING_PROBES {
                let idx = rng.random_range(0..n);
                if let Some((_, (expiry, _))) = self.pings.get_index(idx)
                    && now >= *expiry
                {
                    self.pings.swap_remove_index(idx);
                    evicted = true;
                    break;
                }
            }
            if !evicted {
                return None;
            }
        }
```

**File:** gossip/src/ping_pong.rs (L303-322)
```rust
        let (check, should_ping) = match self.pongs.get(&remote_node) {
            None => (false, true),
            Some(t) => {
                let age = now.saturating_duration_since(*t);
                // Pop if the pong message has expired.
                if age > self.ttl {
                    self.pongs.pop(&remote_node);
                    (false, true)
                } else {
                    // If the pong message is not too recent, generate a new ping
                    // message to extend remote node verification.
                    (true, age > self.ttl / 4)
                }
            }
        };
        let ping = should_ping
            .then(|| self.maybe_ping(rng, keypair, now, remote_node))
            .flatten();
        (check, ping)
    }
```
