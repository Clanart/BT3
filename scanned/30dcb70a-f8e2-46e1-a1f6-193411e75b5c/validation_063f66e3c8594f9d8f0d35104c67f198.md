## Title
Per-IP `TokenBucket` clones inherit a stale zero `last_update` checkpoint relative to a shared `base_time`, causing every newly-seen IP to be granted a full `max_burst` token allowance instead of the configured throttled starting allotment — ([File: net-utils/src/token_bucket.rs])

### Summary
`KeyedRateLimiter` (used by `ConnectionRateLimiter` to gate QUIC/TPU connection acceptance per source IP) lazily creates a new `TokenBucket` for each unseen key by cloning a single `prototype_bucket`. The `Clone` impl copies the prototype's `last_update` checkpoint and `base_time` verbatim, but the prototype is never itself consumed, so its `last_update` stays `0` forever while `base_time` is fixed at limiter construction (effectively process/service start). This is structurally the same bug class as the reported `lastRewardBlock` issue: a checkpoint value is not reset to "now" when a new accrual period begins, so the elapsed-time calculation silently spans an interval that should not be counted, over-crediting the bucket.

### Finding Description
`TokenBucket` tracks tokens using an elapsed-time checkpoint (`last_update`, measured in µs since `base_time`): [1](#0-0) 

`TokenBucket::new` sets `last_update = 0` relative to a freshly captured `base_time = Instant::now()`: [2](#0-1) 

`update_state` mints tokens proportional to `elapsed = now - last_update`, capped only by `add_tokens`'s `min(self.max_tokens)`: [3](#0-2) 

The `Clone` impl, used to spawn a bucket for each new key, copies `base_time` and `last_update` straight from `self` (the prototype), instead of resetting `base_time` to the clone's own creation instant and `last_update` to `0` relative to that new `base_time`: [4](#0-3) 

`KeyedRateLimiter::consume_tokens` creates a new per-key bucket by cloning the prototype whenever a key (IP) is first seen or has been evicted by the LRU shrink logic: [5](#0-4) 

Because the `prototype_bucket` object is *never* itself passed to `consume_tokens`/`current_tokens` (it's only cloned), its `last_update` remains `AtomicU64::new(0)` for the lifetime of the limiter, while `base_time` is fixed to the moment the limiter (and its prototype) was constructed — i.e., approximately validator/service start. Every clone therefore starts with `last_update = 0` measured against a `base_time` that is already far in the past for any IP seen after startup.

On the very first `consume_tokens`/`current_tokens` call for that new bucket, `now` (µs since `base_time`) is large, so `elapsed = now - 0 = now` is large, and `new_tokens_f64 = elapsed * new_tokens_per_us` almost immediately exceeds `max_tokens`. `add_tokens` then saturates the bucket straight to `max_tokens` (i.e., `max_burst`) on the bucket's very first touch — regardless of the `initial_tokens` value (`limit_per_minute`) the caller configured.

This is used directly to gate QUIC connection admission per source IP: [6](#0-5) [7](#0-6) 

`ConnectionRateLimiter::new(limit_per_minute, max_burst, ...)` intentionally seeds each bucket at `initial_tokens = limit_per_minute` (the sustained rate) while `max_tokens = max_burst` is the higher burst ceiling reachable only by accumulating tokens over time. Because of the checkpoint bug above, that ramp-up is bypassed entirely: any IP first observed after the limiter has been running for more than roughly `(max_burst - limit_per_minute) / (limit_per_minute/60)` seconds (typically well under a minute in practice) is handed the full `max_burst` allowance on its very first connection attempt.

### Impact Explanation
This weakens the QUIC/TPU per-IP connection admission control that Agave relies on to bound unprivileged remote connection floods. An unprivileged attacker rotating or spraying many source IPs against a long-running validator's QUIC listener gets each fresh IP bucket pre-filled to `max_burst` instead of the intended throttled `limit_per_minute` starting point, letting each IP burst far more aggressively than the configured policy intends. Combined with the LRU-based `maybe_shrink` eviction (which evicts least-recently-used buckets and forces re-creation via the same buggy clone path), even a single attacker IP can effectively "refresh" back to a full `max_burst` allotment by going idle long enough to be evicted, then reconnecting. This directly weakens a non-RPC remote resource-exhaustion defense on the QUIC/TPU ingest path, matching the in-scope "QUIC/TPU ... non-RPC remote exhaustion/crash" impact category.

### Likelihood Explanation
High likelihood of being triggered unintentionally by any legitimate churn of client IPs, and trivially and deterministically exploitable by an attacker: the condition only requires the limiter/process to have been alive for a short time before the attacker's IP is first seen (true for essentially any running validator), no race condition or timing precision is required, and no privileged or trusted assumptions are needed — it is purely a consequence of local, deterministic checkpoint-initialization logic in `TokenBucket::clone`.

### Recommendation
When cloning a `TokenBucket` for a new key (or re-creating an evicted one), reset `base_time` to `Instant::now()` for the new instance and set `last_update` to `0` relative to that fresh `base_time`, instead of copying the prototype's stale `base_time`/`last_update`. Equivalently, store `last_update` as an absolute wall/monotonic instant per bucket rather than an offset from a shared, distant `base_time`, so each new bucket's elapsed-time accounting starts from its own creation time and correctly reflects only tokens accrued during a real elapsed period, analogous to explicitly setting `lastRewardBlock` to the current block on resume in the original report.

### Proof of Concept
1. Start a `KeyedRateLimiter<IpAddr>` with `prototype_bucket = TokenBucket::new(initial_tokens=60, max_tokens=600, rate=1.0/sec)` (e.g., `ConnectionRateLimiter::new(60, 600, shards)`), as done for QUIC connection admission.
2. Let the limiter run idle for >~10 minutes (so `base_time`-relative `now` when a new key first hits is large — any realistic validator uptime satisfies this).
3. Attacker sends its first QUIC connection from IP `A`. `KeyedRateLimiter::consume_tokens` takes the `Entry::Vacant` path, clones `prototype_bucket` (inheriting `last_update = 0`, `base_time` = limiter construction time).
4. On this first `consume_tokens` call, `update_state` computes `elapsed = now (~600+ seconds in µs) - 0`, mints far more than 600 tokens, and `add_tokens` saturates the bucket to `max_tokens = 600` instead of the intended `initial_tokens = 60`.
5. Attacker from IP `A` can now immediately open up to 600 connections in quick succession (the full burst ceiling) instead of being limited to the intended 60-token starting allowance, and can repeat this for every new source IP it rotates through. [4](#0-3) [5](#0-4)

### Citations

**File:** net-utils/src/token_bucket.rs (L19-33)
```rust
pub struct TokenBucket {
    new_tokens_per_us: f64,
    max_tokens: u64,
    /// bucket creation
    base_time: Instant,
    tokens: AtomicU64,
    /// time of last update in us since base_time
    last_update: AtomicU64,
    /// time unused in last token creation round
    credit_time_us: AtomicU64,
    /// Per-bucket time source for shuttle tests, replacing Instant::now().
    /// Shared via Arc so cloned buckets (e.g. in KeyedRateLimiter) use the same clock.
    #[cfg(feature = "shuttle-test")]
    pub time_us_override: Arc<AtomicU64>,
}
```

**File:** net-utils/src/token_bucket.rs (L37-60)
```rust
impl TokenBucket {
    /// Allocate a new TokenBucket
    pub fn new(initial_tokens: u64, max_tokens: u64, new_tokens_per_second: f64) -> Self {
        assert!(
            new_tokens_per_second > 0.0,
            "Token bucket can not have zero influx rate"
        );
        assert!(
            initial_tokens <= max_tokens,
            "Can not have more initial tokens than max tokens"
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

**File:** net-utils/src/token_bucket.rs (L162-214)
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
                // Save unused elapsed time for other threads
                self.credit_time_us
                    .fetch_add(time_to_return, Ordering::Relaxed);
            }
            Err(_) => {
                // Another thread advanced last_update first → nothing we can do now.
            }
        }
    }
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

**File:** streamer/src/nonblocking/quic.rs (L483-493)
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
```
