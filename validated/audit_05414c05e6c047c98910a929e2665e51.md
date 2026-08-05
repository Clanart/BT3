[1](#0-0) [2](#0-1) [3](#0-2) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8) [10](#0-9) [8](#0-7)

### Citations

**File:** streamer/src/nonblocking/stream_throttle.rs (L70-72)
```rust
        let staked_throttling_on_load_threshold = (STAKED_THROTTLING_ON_LOAD_THRESHOLD_RATIO
            * (max_staked_load_in_ema_interval as f64))
            as u64;
```

**File:** streamer/src/nonblocking/stream_throttle.rs (L103-144)
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

        self.current_load_ema
            .store(updated_load_ema, Ordering::Relaxed);
        self.stats
            .stream_load_ema
            .store(updated_load_ema as usize, Ordering::Relaxed);
    }
```

**File:** streamer/src/nonblocking/stream_throttle.rs (L160-165)
```rust
    pub(crate) fn increment_load(&self, peer_type: ConnectionPeerType) {
        if peer_type.is_staked() {
            self.load_in_recent_interval.fetch_add(1, Ordering::Relaxed);
        }
        self.update_ema_if_needed();
    }
```

**File:** streamer/src/nonblocking/stream_throttle.rs (L172-186)
```rust
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
```

**File:** streamer/src/nonblocking/stream_throttle.rs (L233-271)
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

**File:** streamer/src/nonblocking/stream_throttle.rs (L322-350)
```rust
    #[test]
    fn test_staked_capacity_shares_when_throttled() {
        let mut load_ema = StakedStreamLoadEMA::new(
            Arc::new(StreamerStats::default()),
            DEFAULT_MAX_UNSTAKED_CONNECTIONS,
            DEFAULT_MAX_STREAMS_PER_MS,
        );

        load_ema
            .staked_throttling_enabled
            .store(true, Ordering::Relaxed);
        load_ema.max_staked_load_in_throttling_window = 100;
        load_ema.max_unstaked_load_in_throttling_window = 20;

        assert_eq!(
            load_ema.available_load_capacity_in_throttling_duration(
                ConnectionPeerType::Staked(10),
                100
            ),
            load_ema.max_unstaked_load_in_throttling_window + 1
        );
        assert_eq!(
            load_ema.available_load_capacity_in_throttling_duration(
                ConnectionPeerType::Staked(50),
                100
            ),
            50
        );
    }
```

**File:** streamer/src/nonblocking/stream_throttle.rs (L352-373)
```rust
    #[test]
    fn test_no_throttle_below_threshold() {
        let mut load_ema = StakedStreamLoadEMA::new(
            Arc::new(StreamerStats::default()),
            DEFAULT_MAX_UNSTAKED_CONNECTIONS,
            DEFAULT_MAX_STREAMS_PER_MS,
        );

        load_ema
            .staked_throttling_enabled
            .store(false, Ordering::Relaxed);
        load_ema.max_staked_load_in_throttling_window = 100;
        load_ema.max_unstaked_load_in_throttling_window = 20;

        assert_eq!(
            load_ema.available_load_capacity_in_throttling_duration(
                ConnectionPeerType::Staked(10),
                100
            ),
            load_ema.max_staked_load_in_throttling_window
        );
    }
```

**File:** streamer/src/nonblocking/swqos.rs (L314-329)
```rust
            |(pubkey, stake, total_stake)| {
                // The heuristic is that the stake should be large enough to have 1 stream pass through within one throttle
                // interval during which we allow max (MAX_STREAMS_PER_MS * STREAM_THROTTLING_INTERVAL_MS) streams.

                let peer_type = {
                    let max_streams_per_ms = self.staked_stream_load_ema.max_streams_per_ms();
                    let min_stake_ratio =
                        1_f64 / (max_streams_per_ms * STREAM_THROTTLING_INTERVAL_MS) as f64;
                    let stake_ratio = stake as f64 / total_stake as f64;
                    if stake_ratio < min_stake_ratio {
                        // If it is a staked connection with ultra low stake ratio, treat it as unstaked.
                        ConnectionPeerType::Unstaked
                    } else {
                        ConnectionPeerType::Staked(stake)
                    }
                };
```
