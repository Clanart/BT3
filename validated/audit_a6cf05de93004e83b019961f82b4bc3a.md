### Title
Truncating integer division in QUIC staked-stream load EMA lets an unprivileged client bias the load estimate and evade staked-throttling - (File: `streamer/src/nonblocking/stream_throttle.rs`)

### Summary
The Sherlock report's root cause is a periodic accrual value computed with a truncating integer division of elapsed time by a fixed period (`(blockTime - lastBlockTime) * debaseValue / deltaDebase`), whose result is silently discarded/reset when it floors to too small a value, letting an attacker who controls the timing of the triggering call bias the accrual away from the true continuous-time value. Agave's QUIC staked-stream load estimator (`StakedStreamLoadEMA`) uses the same pattern: it decays/updates a load EMA using `num_extra_updates = time_since_last_update_ms.saturating_sub(1) / STREAM_LOAD_EMA_INTERVAL_MS`, a floor-division of attacker-observable elapsed time by a fixed constant (5 ms), and the update is triggered directly by unprivileged, remote QUIC clients opening streams (`increment_load`).

### Finding Description
`StakedStreamLoadEMA::update_ema` decays the load EMA using a discretized step model instead of a continuous decay: it applies `ema_function` once for the just-elapsed slice, then loops `num_extra_updates` additional times to account for "missed" 5 ms intervals: [1](#0-0) 

`num_extra_updates` is computed via truncating integer division: [2](#0-1) 

This update is only invoked from `update_ema_if_needed`, which is itself called from `increment_load`, i.e. every time *any* staked QUIC peer opens a stream to the TPU: [3](#0-2) 

Because `increment_load`/`update_ema_if_needed` are triggered by the unprivileged act of opening a QUIC stream, a remote staked client fully controls *when* the elapsed-time sample (`since_last_update.as_millis()`) is captured, and therefore controls the input to the floor-division. By spacing stream submissions so that `time_since_last_update_ms` always lands just under a multiple of `STREAM_LOAD_EMA_INTERVAL_MS` (5 ms) - e.g. 9 ms, 14 ms, 19 ms, ... instead of 10 ms, 15 ms, 20 ms - the attacker can make `num_extra_updates` consistently round down by one step relative to the true number of elapsed 5 ms windows. Each such call also unconditionally resets the timing reference (`*last_update_w = Instant::now()` in `update_ema_if_needed`), so the truncated fraction of elapsed time is not preserved for a future accrual (unlike, e.g., the `credit_time_us` carry-forward mechanism used in `net-utils/src/token_bucket.rs`, which explicitly avoids losing sub-threshold time). This is precisely the guard the original report shows to be insufficient: resetting the time reference on every call, instead of banking the truncated remainder, allows systematic under-application of the decay function.

The practical effect: `ema_function` is applied fewer times than the real elapsed time warrants, biasing `current_load_ema` toward staying artificially stable/stale relative to the true instantaneous load, especially when `load_in_recent_interval` is manipulated across the timed calls. Since `current_load_ema` directly drives `staked_throttling_enabled` (the flag gating whether staked-connection stream throttling is engaged) via a fixed threshold comparison: [4](#0-3) 

an attacker who can consistently bias the decay computation can keep `staked_throttling_enabled` from reflecting the true sustained load, or can drive it into a state that penalizes legitimate staked clients longer than warranted via `available_load_capacity_in_throttling_duration`: [5](#0-4) 

### Impact Explanation
`StakedStreamLoadEMA` is the mechanism that governs TPU/QUIC stream admission for staked clients under load; it's meant to detect sustained overload and throttle staked connections proportionally to stake once the EMA crosses a threshold. If the EMA can be biased low/stale via attacker-controlled call timing, an attacker can sustain higher-than-intended stream throughput toward the TPU without ever tripping `staked_throttling_enabled`, amounting to a remote QUIC/TPU resource-exhaustion vector against validator ingestion capacity - fitting the "non-RPC remote exhaustion/crash" impact category. Conversely, if biased the other direction, it can cause `staked_throttling_enabled` to stay incorrectly engaged, unfairly throttling other staked validators' transaction ingestion (degrading legitimate throughput), which is also an availability concern.

### Likelihood Explanation
Likelihood is moderate-to-low in practice: the effect size per exploited timing window is small (one 5 ms EMA step per crafted call), the EMA still incorporates `load_in_recent_interval` from actual traffic, and `STREAM_LOAD_EMA_INTERVAL_COUNT`-based smoothing (40 intervals) dampens single-step bias. An attacker would need fine-grained, sustained control over stream-open timing (sub-5ms precision) across many iterations to meaningfully skew `current_load_ema`, and other staked/unstaked traffic on the same estimator adds noise that works against a precise bias. This is a real logic gap (unbounded truncation without remainder carry-forward, reset unconditionally on every unprivileged trigger) but exploitation to a materially impactful degree requires sustained precision timing.

### Recommendation
Preserve the truncated remainder of elapsed time (analogous to `credit_time_us` in `net-utils/src/token_bucket.rs`) instead of discarding it via the unconditional reset in `update_ema_if_needed`, so that fractional/sub-threshold elapsed time carries forward to the next update rather than being lost. Alternatively, compute the decay using the exact elapsed time (e.g., raise the per-ms decay factor to a fractional power via a closed-form continuous decay, or accumulate `since_last_update` into a persisted counter that is only zeroed after being fully consumed by whole 5 ms steps) so that attacker-controlled call timing cannot bias `num_extra_updates` away from the true number of elapsed intervals.

### Proof of Concept
Conceptual attack sequence (based on `update_ema`/`update_ema_if_needed` logic at `streamer/src/nonblocking/stream_throttle.rs:103-158`):
1. A staked QUIC client opens streams to the TPU, each call reaching `increment_load` → `update_ema_if_needed`.
2. The client spaces successive stream-opens so that `since_last_update.as_millis()` observed at `update_ema_if_needed` is always `k * STREAM_LOAD_EMA_INTERVAL_MS - 1` (e.g., 9 ms, 14 ms, 19 ms) rather than an exact multiple of 5 ms.
3. Each call computes `num_extra_updates = (time_since_last_update_ms - 1) / 5`, which is one step lower than the number of full 5 ms windows that have truly elapsed, and `*last_update_w` is reset to `Instant::now()` regardless, discarding the sub-5ms remainder each time.
4. Repeated over many iterations, `current_load_ema` diverges from the value a continuous-time (or remainder-preserving) decay model would produce, allowing the attacker to keep `staked_throttling_enabled` from reflecting sustained load while continuing to submit streams, or to keep it pinned on to degrade other stakers' throughput.

Note: I could not fully trace all callers of `increment_load`/the QUIC stream-accept path in `streamer/src/nonblocking/quic.rs` within the indexed context (only `stream_throttle.rs` and `swqos.rs` matched the search), so I cannot confirm every code path that triggers `increment_load` per incoming stream, nor precisely quantify achievable bias magnitude under production concurrency. A Devin session with full repository access would be needed to trace all call sites and empirically validate the achievable skew.

### Citations

**File:** streamer/src/nonblocking/stream_throttle.rs (L103-122)
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
```

**File:** streamer/src/nonblocking/stream_throttle.rs (L132-137)
```rust
        if self.staked_throttling_on_load_threshold > 0 {
            self.staked_throttling_enabled.store(
                updated_load_ema >= self.staked_throttling_on_load_threshold,
                Ordering::Relaxed,
            );
        }
```

**File:** streamer/src/nonblocking/stream_throttle.rs (L146-165)
```rust
    pub(crate) fn update_ema_if_needed(&self) {
        const EMA_DURATION: Duration = Duration::from_millis(STREAM_LOAD_EMA_INTERVAL_MS);
        // Read lock enables multiple connection handlers to run in parallel if interval is not expired
        if Instant::now().duration_since(*self.last_update.read().unwrap()) >= EMA_DURATION {
            let mut last_update_w = self.last_update.write().unwrap();
            // Recheck as some other thread might have updated the ema since this thread tried to acquire the write lock.
            let since_last_update = Instant::now().duration_since(*last_update_w);
            if since_last_update >= EMA_DURATION {
                *last_update_w = Instant::now();
                self.update_ema(since_last_update.as_millis());
            }
        }
    }

    pub(crate) fn increment_load(&self, peer_type: ConnectionPeerType) {
        if peer_type.is_staked() {
            self.load_in_recent_interval.fetch_add(1, Ordering::Relaxed);
        }
        self.update_ema_if_needed();
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
