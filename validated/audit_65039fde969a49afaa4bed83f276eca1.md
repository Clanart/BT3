### Title
Staked stream throttling gate lags real load, letting synchronized stake-weighted stream bursts bypass QUIC/TPU rate limiting - ([File: streamer/src/nonblocking/stream_throttle.rs])

### Summary
The Hyperdrive report shows a circuit breaker that only compares the *current* value to a *smoothed/weighted average from the previous window*, letting an attacker cause a large deviation right at the window boundary and then trade again before the average catches up. Agave's QUIC/TPU staked-stream throttling gate (`StakedStreamLoadEMA`) has the same structural weakness: whether staked connections get throttled at all is gated by a single boolean, `staked_throttling_enabled`, that is derived from a slow exponential moving average of load rather than the instantaneous/actual load in the current 100ms throttling window.

### Finding Description
`StakedStreamLoadEMA::update_ema` computes an EMA of load sampled every `STREAM_LOAD_EMA_INTERVAL_MS` (5ms) with a smoothing window of `STREAM_LOAD_EMA_INTERVAL_COUNT = 40` intervals (≈200ms half-life), and only flips `staked_throttling_enabled` when the *smoothed* value crosses `staked_throttling_on_load_threshold`: [1](#0-0) 

This flag is a single global gate consulted by `available_load_capacity_in_throttling_duration`, which is the only thing that decides whether staked connections get the full `max_staked_load_in_throttling_window` (i.e., effectively unthrottled) or a stake-proportional share: [2](#0-1) 

Because the EMA is smoothed over a 40-sample (≈200ms) window and is only updated lazily on `increment_load`/`update_ema_if_needed`, the *actual instantaneous load in the current interval* is not what gates throttling — the *lagging average* is. This mirrors exactly the Hyperdrive flaw: the guard is checked against a delayed/aggregated metric instead of the value that matters at the moment of the check, so a load spike concentrated at the start of a measurement window is not reflected in the gate until the EMA has decayed several samples later.

Per-connection enforcement (`ConnectionStreamCounter` / `throttle_stream`) only resets and starts counting once every `STREAM_THROTTLING_INTERVAL_MS` (100ms) per connection, using its own independent timer: [3](#0-2) 

Since each connection's 100ms window boundary is set by when that connection first opens a stream (`reset_throttling_params_if_needed`), and the global EMA gate updates on a separate, coarser cadence, an attacker controlling many staked connections (or opening many short-lived connections in quick succession, since stake per connection determines allotment) can align bursts to land in the "blind" period before `staked_throttling_enabled` flips true, exhausting `available_load_capacity_in_throttling_duration()` capacity for many connections' 100ms windows before the smoothed EMA ever detects sustained load — exactly analogous to the Hyperdrive attacker landing a large trade right before the checkpoint boundary so it never enters the `weightedSpotPrice` used by the next check.

### Impact Explanation
If the staked throttling gate lags actual load, an unprivileged staked client (or a small set of staked/unstaked-but-numerous connections) can push disproportionate stream volume into the TPU/QUIC stack during the EMA's blind window, before `staked_throttling_enabled` engages and before any individual connection's `ConnectionStreamCounter` accumulates enough count to be throttled. This is a resource-exhaustion / remote degradation vector against a leader's TPU ingestion path (packet processing, sig-verify, banking stage queueing), which falls under the "non-RPC remote exhaustion/crash" category in scope.

### Likelihood Explanation
The mechanism requires no malicious/trusted peer assumption beyond holding some stake and being able to open QUIC connections/streams at the TPU port — a capability every staked validator's transaction path already exercises. The 200ms EMA smoothing window and independent per-connection 100ms timers are both explicit, intentional design parameters (see comments in the code), so the timing gap is inherent to the mechanism rather than a rare race. However, exploiting it to meaningfully overload a leader still requires coordinating enough streams/connections within the sub-window to matter, and downstream layers (rate limiter, `ConnectionRateLimiter`, `TokenBucket`, per-IP/per-connection caps) provide some mitigation, so the practical severity depends on cluster-wide stake distribution and concurrent connection limits.

### Recommendation
Base the staked throttling decision on the actual load observed in the current (or a short, non-decaying) window rather than solely on a heavily smoothed EMA, or shorten the EMA window / add a fast-acting instantaneous cap that trips independently of the smoothed average — analogous to Delv/Spearbit's suggestion of incorporating multiple preceding windows/checkpoints rather than a single lagging aggregate. Aligning the per-connection `STREAM_THROTTLING_INTERVAL_MS` reset cadence with the EMA sampling cadence would also reduce the blind-window mismatch.

### Proof of Concept
Conceptual (no test harness available in the index to confirm exact numeric bypass):
1. At `t=0`, `staked_throttling_enabled` is `false` and `current_load_ema` is low (see `StakedStreamLoadEMA::new` defaults) — `available_load_capacity_in_throttling_duration` returns the full `max_staked_load_in_throttling_window` for every staked connection.
2. An attacker opens many staked connections (or reuses a moderate number of connections with proportional stake) and floods streams simultaneously within a single 5ms EMA sampling tick and within each connection's own fresh 100ms `ConnectionStreamCounter` window.
3. `update_ema` only updates on `increment_load`, and with `STREAM_LOAD_EMA_INTERVAL_COUNT = 40`, the smoothing factor `2/(41)` means several samples must show sustained load before `staked_throttling_enabled` flips to `true`.
4. Until that flip occurs, `available_load_capacity_in_throttling_duration` continues to hand out the unthrottled `max_staked_load_in_throttling_window` to every connection, letting the burst pass through unthrottled in `throttle_stream` for the current 100ms window, then repeat once each connection's window resets. [4](#0-3)

### Citations

**File:** streamer/src/nonblocking/stream_throttle.rs (L16-44)
```rust
/// Max TPS allowed for unstaked connection
const MAX_UNSTAKED_TPS: u64 = 200;
/// Expected fraction of max TPS to be consumed by unstaked connections
const EXPECTED_UNSTAKED_STREAMS_RATIO: f64 = 0.20;

pub const STREAM_THROTTLING_INTERVAL_MS: u64 = 100;
pub const STREAM_THROTTLING_INTERVAL: Duration =
    Duration::from_millis(STREAM_THROTTLING_INTERVAL_MS);
const STREAM_LOAD_EMA_INTERVAL_MS: u64 = 5;
// EMA smoothing window to reduce sensitivity to short-lived load spikes at the start
// of a leader slot. Throttling is only triggered when saturation is sustained.
// The value 40 was chosen based on simulations: at a max target TPS of ~400K,
// it allows the system to absorb a burst of ~50K transactions over ~40 ms
// before throttling activates.
const STREAM_LOAD_EMA_INTERVAL_COUNT: u64 = 40;

const STAKED_THROTTLING_ON_LOAD_THRESHOLD_RATIO: f64 = 0.95;

pub(crate) struct StakedStreamLoadEMA {
    current_load_ema: AtomicU64,
    load_in_recent_interval: AtomicU64,
    last_update: RwLock<Instant>,
    stats: Arc<StreamerStats>,
    max_staked_load_in_throttling_window: u64,
    max_unstaked_load_in_throttling_window: u64,
    max_streams_per_ms: u64,
    staked_throttling_on_load_threshold: u64, // in streams/STREAM_LOAD_EMA_INTERVAL_MS
    staked_throttling_enabled: AtomicBool,
}
```

**File:** streamer/src/nonblocking/stream_throttle.rs (L103-137)
```rust
    fn update_ema(&self, time_since_last_update_ms: u128) {
        // if time_since_last_update_ms > STREAM_LOAD_EMA_INTERVAL_MS, there might be intervals where ema was not updated.
        // count how many updates (1 + missed intervals) are needed.
        let num_extra_updates =
            time_since_last_update_ms.saturating_sub(1) / u128::from(STREAM_LOAD_EMA_INTERVAL_MS);

        let load_in_recent_interval =
            u128::from(self.load_in_recent_interval.swap(0, Ordering::Relaxed));

        let mut updated_load_ema = Self::ema_function(
            u128::from(self.current_load_ema.load(Ordering::Relaxed)),
            load_in_recent_interval,
        );

        for _ in 0..num_extra_updates {
            updated_load_ema = Self::ema_function(updated_load_ema, 0);
            if updated_load_ema == 0 {
                break;
            }
        }

        let Ok(updated_load_ema) = u64::try_from(updated_load_ema) else {
            error!("Failed to convert EMA {updated_load_ema} to a u64. Not updating the load EMA");
            self.stats
                .stream_load_ema_overflow
                .fetch_add(1, Ordering::Relaxed);
            return;
        };

        if self.staked_throttling_on_load_threshold > 0 {
            self.staked_throttling_enabled.store(
                updated_load_ema >= self.staked_throttling_on_load_threshold,
                Ordering::Relaxed,
            );
        }
```

**File:** streamer/src/nonblocking/stream_throttle.rs (L167-188)
```rust
    pub(crate) fn available_load_capacity_in_throttling_duration(
        &self,
        peer_type: ConnectionPeerType,
        total_stake: u64,
    ) -> u64 {
        match peer_type {
            ConnectionPeerType::Unstaked => self.max_unstaked_load_in_throttling_window,
            ConnectionPeerType::Staked(stake) => {
                if self.staked_throttling_enabled.load(Ordering::Relaxed) {
                    // 1 is added to `max_unstaked_load_in_throttling_window` to guarantee that staked
                    // clients get at least 1 more number of streams than unstaked connections.
                    self.max_staked_load_in_throttling_window
                        .saturating_mul(stake)
                        .checked_div(total_stake)
                        .unwrap_or(self.max_unstaked_load_in_throttling_window + 1)
                        .max(self.max_unstaked_load_in_throttling_window + 1)
                } else {
                    self.max_staked_load_in_throttling_window
                }
            }
        }
    }
```

**File:** streamer/src/nonblocking/stream_throttle.rs (L211-271)
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
}

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

        if !throttle_duration.is_zero() {
            debug!(
                "Throttling stream from {remote_addr:?}, peer type: {peer_type:?}, \
                 max_streams_per_interval: {max_streams_per_throttling_interval}, \
                 read_interval_streams: {streams_read_in_throttle_interval} throttle_duration: \
                 {throttle_duration:?}"
            );
            stats.throttled_streams.fetch_add(1, Ordering::Relaxed);
            match peer_type {
                ConnectionPeerType::Unstaked => {
                    stats
                        .throttled_unstaked_streams
                        .fetch_add(1, Ordering::Relaxed);
                }
                ConnectionPeerType::Staked(_) => {
                    stats
                        .throttled_staked_streams
                        .fetch_add(1, Ordering::Relaxed);
                }
            }
            sleep(throttle_duration).await;
        }
    }
}
```
