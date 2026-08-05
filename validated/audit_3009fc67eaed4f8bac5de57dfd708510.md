### Title
Stale `TokenBucket` timestamp inherited via `KeyedRateLimiter` clone lets any new key instantly obtain full burst capacity, bypassing the QUIC per-IP connection-rate limiter - (File: `net-utils/src/token_bucket.rs`)

### Summary
`TokenBucket::update_state` computes newly minted tokens from `now - last_update`, where `last_update` is meant to represent "time since this bucket started being used." [1](#0-0)  For a freshly constructed bucket this is correct because `base_time` is set to `Instant::now()` at construction and `last_update` starts at `0`, i.e., "now." [2](#0-1)  However, `KeyedRateLimiter` never constructs per-key buckets with `TokenBucket::new`; it clones a long-lived `prototype_bucket` whose `base_time` was fixed at server startup and whose `last_update` is copied verbatim. [3](#0-2) [4](#0-3)  Since the prototype's own `consume_tokens`/`current_tokens` are never invoked directly (only cloned), its `last_update` remains `0` indefinitely, meaning every freshly-inserted per-key bucket believes it has existed — and gone unused — since server startup rather than since the moment it was actually created. This is the same class of bug as the OCE_ZVE report: a decay/refill timer anchored to a stale point in time instead of to the moment the "distribution"/window actually begins, so idle time is wrongly counted as elapsed refill time.

### Finding Description
`TokenBucket::new` seeds a fresh bucket correctly: `base_time = Instant::now()`, `last_update = 0` (representing "no time has elapsed since base_time"), `tokens = initial_tokens`. [2](#0-1) 

`Clone` for `TokenBucket` copies `base_time` and `last_update` byte-for-byte from the source bucket instead of resetting them relative to the moment of cloning: [3](#0-2) 

`KeyedRateLimiter::consume_tokens`'s vacant-entry path clones `self.prototype_bucket` to seed a new per-key bucket: [4](#0-3) 

The `prototype_bucket` is created once at server startup (e.g. in `ConnectionRateLimiter::new`) and is never itself passed to `consume_tokens`/`current_tokens` — it only ever serves as a clone template. [5](#0-4)  Consequently its `last_update` stays at the initial value `0` for the lifetime of the process, while `base_time` is pinned to process startup.

When a brand-new key (IP address) is first seen — potentially hours or days into validator uptime — `KeyedRateLimiter` clones the prototype, producing a bucket whose `base_time` is "server start" and whose `last_update` is `0`. The very first call to `consume_tokens`/`current_tokens` on this new bucket computes `now = elapsed_since_server_start` (a huge value) and `last = 0`, so `elapsed = now - last` is effectively the entire server uptime rather than the true "0 seconds since bucket creation." [6](#0-5)  This mints far more tokens than the configured refill rate would allow in the real elapsed time, and `add_tokens` saturates the bucket straight to `max_tokens` on this very first access. [7](#0-6) 

`ConnectionRateLimiter` is instantiated with `initial_tokens = limit_per_minute` and `max_tokens = max_burst = limit_per_minute * 10`, explicitly intending new IPs to start at the base rate and only accumulate up to a 10x burst allowance over time: [8](#0-7) [9](#0-8)  Because of the stale-timestamp bug, that gradual-ramp invariant never holds for any newly-seen IP: it is granted the full 10x burst allowance immediately on first contact, rather than needing to wait and accumulate tokens over time as the code and its comments assume.

The LRU eviction in `KeyedRateLimiter::maybe_shrink` sorts on `last_update` to keep the most-recently-active keys and evict idle ones. [10](#0-9)  Any key that gets evicted and later reappears (e.g. via reconnecting after their bucket was shrunk out) is re-seeded from the same stale prototype and again receives an instant full 10x burst, indefinitely repeatable.

### Impact Explanation
This weakens the per-IP QUIC connection-rate limiter (`streamer/src/nonblocking/quic.rs`, used for TPU/QUIC ingestion) that exists specifically to defend against unprivileged remote connection-flooding/exhaustion. [11](#0-10)  Any unprivileged remote party can immediately connect using new source addresses (or exploit the LRU-shrink/re-insertion cycle for a fixed address) and always receive the maximum configured burst rate (10x the intended steady-state limit) rather than being throttled to the intended base rate and needing time to earn the burst. This directly degrades the resource-exhaustion protection this component is supposed to provide, matching the "non-RPC remote exhaustion/crash" impact category — it does not itself crash the validator, but it removes/weakens a load-shedding control on an unprivileged, remotely-reachable network surface (QUIC/TPU ingestion).

### Likelihood Explanation
The bug fires deterministically on every first-touch of a new key in any `KeyedRateLimiter`-backed `TokenBucket` (not a race or edge case) — as soon as the process has been running longer than a negligible amount of time, which is essentially always true in production. No special privileges, malicious peers, or timing races are required; a normal, unprivileged network client simply needs to be the first (or a repeat, post-eviction) connector from a given source IP.

### Recommendation
When cloning a `TokenBucket` for a new key in `KeyedRateLimiter`, reset `base_time` to `Instant::now()` and `last_update` to `0` for the new instance instead of copying the prototype's stale values, so that the refill/decay clock for a new key starts at the moment the key is actually created rather than at prototype-construction time.

### Proof of Concept
1. Start the streamer with a `ConnectionRateLimiter::new(limit_per_minute, max_burst = limit_per_minute*10, num_shards)` as done in `run_server`. [8](#0-7) 
2. Let the process run for any amount of time (even a few seconds is enough given microsecond-resolution `time_us()`).
3. From a source IP never seen before, call `register_connection(ip)` (first-ever access to that key). [12](#0-11) 
4. Internally this triggers `KeyedRateLimiter::consume_tokens`'s `Entry::Vacant` branch, cloning `prototype_bucket` (whose `base_time`/`last_update` reflect server startup, not "now"). [4](#0-3) 
5. The clone's first `update_state` call computes `elapsed = time_since_process_start`, which is far larger than needed to fill the bucket to `max_tokens`; `add_tokens` saturates to `max_burst`. [13](#0-12) 
6. `current_tokens(ip)` immediately reports `max_burst` (10x `limit_per_minute`) instead of `limit_per_minute`, letting the new IP open connections at the full burst rate from its very first request, contrary to the intended gradual-ramp design documented in `ConnectionRateLimiter::new`. [5](#0-4)

### Citations

**File:** net-utils/src/token_bucket.rs (L47-60)
```rust
        );
        let base_time = Instant::now();
        TokenBucket {
            // recompute into us to avoid FP division on every update
            new_tokens_per_us: new_tokens_per_second / 1e6,
            max_tokens,
            tokens: AtomicU64::new(initial_tokens),
            last_update: AtomicU64::new(0),
            base_time,
            credit_time_us: AtomicU64::new(0),
            #[cfg(feature = "shuttle-test")]
            time_us_override: Arc::new(AtomicU64::new(0)),
        }
    }
```

**File:** net-utils/src/token_bucket.rs (L119-127)
```rust
    /// Adds given amount of tokens, up to a maximum of self.max_tokens.
    #[inline]
    pub fn add_tokens(&self, new_tokens: u64) {
        let _ = self.tokens.fetch_update(
            Ordering::AcqRel,  // writer publishes new amount
            Ordering::Acquire, //we fetch the correct amount
            |tokens| Some(tokens.saturating_add(new_tokens).min(self.max_tokens)),
        );
    }
```

**File:** net-utils/src/token_bucket.rs (L162-205)
```rust
    fn update_state(&self, now: u64) {
        // fetch last update time
        let last = self.last_update.load(Ordering::SeqCst);

        // If time has not advanced, nothing to do.
        if now <= last {
            return;
        }

        // Try to claim the interval [last, now].
        // If we can not claim it, someone else will claim [last..some other time] when they
        // touch the bucket.
        // If we can claim interval [last, now], no other thread can credit tokens for it anymore.
        // If [last, now] is too short to mint any tokens, spare time will be preserved in credit_time_us.
        match self.last_update.compare_exchange(
            last,
            now,
            Ordering::AcqRel,  // winner publishes new timestamp
            Ordering::Acquire, // loser observes updates
        ) {
            Ok(_) => {
                // This thread won the race and is responsible for minting tokens
                let elapsed = now.saturating_sub(last);

                // also add leftovers from previous conversion attempts.
                // we do not care about who uses the spare_time_us, so relaxed is ok here.
                let elapsed =
                    elapsed.saturating_add(self.credit_time_us.swap(0, Ordering::Relaxed));

                let new_tokens_f64 = elapsed as f64 * self.new_tokens_per_us;

                // amount of full tokens to be minted
                let new_tokens = new_tokens_f64.floor() as u64;

                let time_to_return = if new_tokens >= 1 {
                    // Credit tokens, saturating at max_tokens
                    self.add_tokens(new_tokens);
                    // Fractional remainder of elapsed time (not enough to mint a whole token)
                    // that will be credited to other minters
                    (new_tokens_f64.fract() / self.new_tokens_per_us) as u64
                } else {
                    // No whole tokens minted → return whole interval
                    elapsed
                };
```

**File:** net-utils/src/token_bucket.rs (L217-234)
```rust
impl Clone for TokenBucket {
    /// Clones the TokenBucket with approximate state
    /// of the original. While this will never return an object in an
    /// invalid state, using this in a contended environment is not recommended.
    fn clone(&self) -> Self {
        Self {
            new_tokens_per_us: self.new_tokens_per_us,
            max_tokens: self.max_tokens,
            base_time: self.base_time,
            tokens: AtomicU64::new(self.tokens.load(Ordering::Relaxed)),
            last_update: AtomicU64::new(self.last_update.load(Ordering::Relaxed)),
            credit_time_us: AtomicU64::new(self.credit_time_us.load(Ordering::Relaxed)),
            // Cloned buckets share the same time source so they see the same clock
            #[cfg(feature = "shuttle-test")]
            time_us_override: Arc::clone(&self.time_us_override),
        }
    }
}
```

**File:** net-utils/src/token_bucket.rs (L303-316)
```rust
    pub fn consume_tokens(&self, key: K, request_size: u64) -> Result<u64, u64> {
        let (entry_added, res) = {
            let bucket = self.data.entry(key);
            match bucket {
                Entry::Occupied(entry) => (false, entry.get().consume_tokens(request_size)),
                Entry::Vacant(entry) => {
                    // if the key is not in the LRU, we need to allocate a new bucket
                    let bucket = self.prototype_bucket.clone();
                    let res = bucket.consume_tokens(request_size);
                    entry.insert(bucket);
                    (true, res)
                }
            }
        };
```

**File:** net-utils/src/token_bucket.rs (L354-390)
```rust
    #[allow(clippy::arithmetic_side_effects)]
    fn maybe_shrink(&self) {
        let mut actual_len = 0;
        let target_shard_size = self.target_capacity / self.data.shards().len();
        if target_shard_size == 0 {
            return;
        }
        let mut entries = Vec::with_capacity(target_shard_size * 2);
        for shardlock in self.data.shards() {
            let mut shard = shardlock.write();

            if shard.len() <= target_shard_size * 3 / 2 {
                actual_len += shard.len();
                continue;
            }
            entries.clear();
            entries.extend(
                shard.drain().map(|(key, value)| {
                    (key, value.get().last_update.load(Ordering::SeqCst), value)
                }),
            );

            entries.select_nth_unstable_by_key(target_shard_size, |(_, last_update, _)| {
                Reverse(*last_update)
            });

            shard.extend(
                entries
                    .drain(..)
                    .take(target_shard_size)
                    .map(|(key, _last_update, value)| (key, value)),
            );
            debug_assert!(shard.len() <= target_shard_size);
            actual_len += shard.len();
        }
        self.approx_len.store(actual_len, Ordering::Relaxed);
    }
```

**File:** streamer/src/nonblocking/connection_rate_limiter.rs (L16-29)
```rust
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

**File:** streamer/src/nonblocking/connection_rate_limiter.rs (L42-50)
```rust
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
