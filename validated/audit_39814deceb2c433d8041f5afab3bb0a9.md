The strongest local analog to the OpenTerm "reset the penalty clock with a brief compliant pulse" bug is the fixed-window per-connection stream throttle used on the QUIC/TPU ingestion path.

### Title
QUIC stream throttle uses a hard-reset fixed window instead of a sliding/continuous limiter, letting an unprivileged client sustain up to ~2x its allotted stream rate indefinitely - (File: streamer/src/nonblocking/stream_throttle.rs)

### Summary
`ConnectionStreamCounter` enforces the per-connection cap on new QUIC streams (i.e. transaction submissions) using a counter that is fully zeroed once `STREAM_THROTTLING_INTERVAL` (100ms) has elapsed since the last reset, rather than a continuously-refilling limiter. Just like the OpenTerm borrower who clears `delinquencyStartTime` back to zero with a minimal compliant action and then immediately resumes the abusive behavior, a QUIC client can burst right up to the limit just before the window boundary, and burst again immediately after the reset, doubling its effective allowed throughput across the boundary. This can be repeated at every 100ms boundary, indefinitely.

### Finding Description
`throttle_stream()` reads `stream_count` and compares it against `max_streams_per_throttling_interval`; the counter is reset to `0` (not decayed/refilled) whenever `reset_throttling_params_if_needed()` observes that more than `STREAM_THROTTLING_INTERVAL` has passed since `last_throttling_instant`: [1](#0-0) 

The check in `throttle_stream` only fires once the count within the *current* discrete window reaches the max; it has no memory of activity from the prior window: [2](#0-1) 

`on_stream_accepted` increments `stream_count` for every accepted stream, and `on_new_stream` calls `throttle_stream` before allowing the stream to proceed, using `max_streams_per_throttling_interval` derived from the peer's stake-based allotment: [3](#0-2) 

Because the window is a hard reset rather than a sliding/token-bucket accounting (contrast with `ConnectionRateLimiter`, which correctly uses a continuously-refilling `TokenBucket` for per-IP connection admission): [4](#0-3) 

an attacker-controlled client can:
1. Open streams up to `max_streams_per_throttling_interval - 1` just before the 100ms window elapses (no throttling triggered).
2. Immediately after crossing the boundary, `reset_throttling_params_if_needed()` zeroes `stream_count`, and the client opens another full batch of `max_streams_per_throttling_interval` streams with zero delay.
3. Repeat at every subsequent 100ms boundary.

This yields a sustained rate approaching 2x the value the EMA-based stake allotment (`available_load_capacity_in_throttling_duration`) was calibrated to allow, forever, without ever being penalized by the throttle sleep.

### Impact Explanation
This is the QUIC ingestion path for the TPU (transaction submission), reachable by any unstaked or low-stake remote client without needing to be a validator or trusted peer. Sustained ~2x-over-budget stream admission per connection undermines the intended stake-weighted QoS/anti-spam design (`StakedStreamLoadEMA`), letting a single low-cost client consume disproportionate TPU stream-processing capacity and contribute to non-RPC remote exhaustion/degradation of transaction ingestion for legitimate, properly-staked traffic.

### Likelihood Explanation
The primitive requires no special privilege, no validator identity, and no coordination — a single QUIC client controlling its own send timing relative to a 100ms wall-clock boundary is sufficient. The boundary condition is deterministic and easy to detect/time from the client side (the throttle sleep duration reveals when the window resets).

### Recommendation
Replace `ConnectionStreamCounter`'s discrete reset-to-zero window with a token-bucket/sliding-window accounting scheme (as already used in `ConnectionRateLimiter`) so admitted stream counts decay continuously rather than being erased entirely at each interval boundary, eliminating the ability to double the allotted rate across window edges.

### Proof of Concept
1. Establish a QUIC connection as an unstaked (or low-stake) peer against the TPU streamer.
2. Track `last_throttling_instant` behavior by sending `max_streams_per_throttling_interval - 1` streams, then observing no throttle sleep.
3. Wait until just after the 100ms boundary (detectable because the previous throttle call's `throttle_duration` reveals the window's remaining time), then immediately send another `max_streams_per_throttling_interval` streams.
4. Repeat step 3 every ~100ms; measure sustained throughput approaching 2x `available_load_capacity_in_throttling_duration` for the connection's peer type/stake, with `throttled_streams`/`throttled_unstaked_streams` counters growing far slower than actual admitted stream volume.

### Citations

**File:** streamer/src/nonblocking/stream_throttle.rs (L211-230)
```rust
    /// Reset the counter and last throttling instant and
    /// return last_throttling_instant regardless it is reset or not.
    pub(crate) fn reset_throttling_params_if_needed(&self) -> tokio::time::Instant {
        let last_throttling_instant = *self.last_throttling_instant.read().unwrap();
        if tokio::time::Instant::now().duration_since(last_throttling_instant)
            > STREAM_THROTTLING_INTERVAL
        {
            let mut last_throttling_instant = self.last_throttling_instant.write().unwrap();
            // Recheck as some other thread might have done throttling since this thread tried to acquire the write lock.
            if tokio::time::Instant::now().duration_since(*last_throttling_instant)
                > STREAM_THROTTLING_INTERVAL
            {
                *last_throttling_instant = tokio::time::Instant::now();
                self.stream_count.store(0, Ordering::Relaxed);
            }
            *last_throttling_instant
        } else {
            last_throttling_instant
        }
    }
```

**File:** streamer/src/nonblocking/stream_throttle.rs (L233-247)
```rust
pub(crate) async fn throttle_stream(
    stats: &StreamerStats,
    peer_type: ConnectionPeerType,
    remote_addr: std::net::SocketAddr,
    stream_counter: &Arc<ConnectionStreamCounter>,
    max_streams_per_throttling_interval: u64,
) {
    let throttle_interval_start = stream_counter.reset_throttling_params_if_needed();
    let streams_read_in_throttle_interval = stream_counter.stream_count.load(Ordering::Relaxed);
    if streams_read_in_throttle_interval >= max_streams_per_throttling_interval {
        // The peer is sending faster than we're willing to read. Sleep for what's
        // left of this read interval so the peer backs off.
        let throttle_duration =
            STREAM_THROTTLING_INTERVAL.saturating_sub(throttle_interval_start.elapsed());

```

**File:** streamer/src/nonblocking/swqos.rs (L445-516)
```rust
    fn on_stream_accepted(&self, conn_context: &SwQosConnectionContext) {
        self.staked_stream_load_ema
            .increment_load(conn_context.peer_type);
        conn_context
            .stream_counter
            .as_ref()
            .unwrap()
            .stream_count
            .fetch_add(1, Ordering::Relaxed);
    }

    fn on_stream_error(&self, _conn_context: &SwQosConnectionContext) {
        self.staked_stream_load_ema.update_ema_if_needed();
    }

    fn on_stream_closed(&self, _conn_context: &SwQosConnectionContext) {
        self.staked_stream_load_ema.update_ema_if_needed();
    }

    #[allow(clippy::manual_async_fn)]
    fn remove_connection(
        &self,
        conn_context: &SwQosConnectionContext,
        connection: Connection,
    ) -> impl Future<Output = usize> + Send {
        async move {
            let mut lock = if conn_context.in_staked_table {
                self.staked_connection_table.lock().await
            } else {
                self.unstaked_connection_table.lock().await
            };

            let stable_id = connection.stable_id();
            let remote_addr = conn_context.remote_address;

            let removed_count = lock.remove_connection(
                ConnectionTableKey::new(remote_addr.ip(), conn_context.remote_pubkey()),
                remote_addr.port(),
                stable_id,
            );
            update_open_connections_stat(&self.stats, &lock);
            removed_count
        }
    }

    fn on_stream_finished(&self, context: &SwQosConnectionContext) {
        context
            .last_update
            .store(timing::timestamp(), Ordering::Relaxed);
    }

    #[allow(clippy::manual_async_fn)]
    fn on_new_stream(&self, context: &SwQosConnectionContext) -> impl Future<Output = ()> + Send {
        async move {
            let peer_type = context.peer_type();
            let remote_addr = context.remote_address;
            let stream_counter: &Arc<ConnectionStreamCounter> =
                context.stream_counter.as_ref().unwrap();

            let max_streams_per_throttling_interval =
                self.max_streams_per_throttling_interval(context);

            throttle_stream(
                &self.stats,
                peer_type,
                remote_addr,
                stream_counter,
                max_streams_per_throttling_interval,
            )
            .await;
        }
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
