## Title
Stake-weighted stream throttling permanently disabled when `staked_throttling_on_load_threshold` rounds to zero — analogous "threshold gate blocks intended control" bug - (`streamer/src/nonblocking/stream_throttle.rs`)

## Summary
The C4 finding describes a mechanism (DAO-controlled `tailEmissionRate`) that is supposed to take unconditional effect once a phase/epoch condition is met, but is instead wrapped in an extra numeric-threshold conditional that can suppress it entirely, silently defeating the control. The Agave analog is `StakedStreamLoadEMA::update_ema` in `streamer/src/nonblocking/stream_throttle.rs`, which is supposed to continuously re-evaluate and toggle `staked_throttling_enabled` (the flag that turns on stake-proportional QUIC/TPU stream throttling) every EMA interval. That toggle logic is wrapped in `if self.staked_throttling_on_load_threshold > 0 { ... }`, so if the precomputed threshold degenerates to `0`, the flag is never updated again after construction and remains stuck at its default `false` for the lifetime of the streamer.

## Finding Description
`StakedStreamLoadEMA::new` computes the threshold once at construction: [1](#0-0) 

and initializes `staked_throttling_enabled` to `false`: [2](#0-1) 

The only place that flips this flag afterward is inside `update_ema`, gated by a check on the *threshold* rather than on load itself: [3](#0-2) 

Because `staked_throttling_on_load_threshold` is `(0.95 * max_staked_load_in_ema_interval) as u64` (integer truncation of a `f64`), if `max_staked_load_in_ema_interval` is small enough (i.e., a low `max_streams_per_ms` configuration, which scales `max_staked_load_in_ms` and then `STREAM_LOAD_EMA_INTERVAL_MS`), the product truncates to `0`. Once `staked_throttling_on_load_threshold == 0`, the `if` guard is permanently false, so `staked_throttling_enabled` can never be set to `true` — exactly mirroring the C4 pattern where the intended control ("apply proportional adjustment once a documented condition is reached") is nullified by an extra conditional that was supposed to gate *activation timing*, not *whether the mechanism ever runs at all*.

The consequence is visible directly in `available_load_capacity_in_throttling_duration`: [4](#0-3) 

With `staked_throttling_enabled` stuck at `false`, every `ConnectionPeerType::Staked(stake)` peer — regardless of how small its stake is relative to `total_stake` — is granted the full `max_staked_load_in_throttling_window` stream budget (the `else` branch at line 184), instead of the stake-proportional share the design intends. The intended safeguard ("higher stake gets more streams, low stake gets throttled toward the unstaked floor") never activates.

## Impact Explanation
This defeats Agave's stake-weighted QoS (SWQoS) defense in the QUIC/TPU ingestion path, whose entire purpose is to prevent low-stake or minimally-staked connections from consuming disproportionate stream/transaction-processing capacity relative to their economic weight. If the flag is stuck `false`, every staked connection (including nodes with negligible stake, which the code elsewhere treats specially only via the separate `min_stake_ratio` unstaked-reclassification path) receives the same unthrottled `max_staked_load_in_throttling_window` streams as a top validator. This directly enables non-RPC remote resource exhaustion of the TPU/QUIC ingestion pipeline by any staked (even minimally staked) peer, without requiring a malicious/admin/trusted assumption — only a specific low-throughput validator configuration.

## Likelihood Explanation
The condition is deterministic and configuration-driven rather than attacker-driven: it requires `max_streams_per_ms` to be configured low enough that `0.95 * max_staked_load_in_ms * STREAM_LOAD_EMA_INTERVAL_MS` truncates to `0`. Under the shipped defaults (`DEFAULT_MAX_STREAMS_PER_MS = 500`) this does not trigger, since `staked_throttling_on_load_threshold` is comfortably above zero. The bug therefore only manifests for validators/operators who lower `--tpu-max-streams-per-ms`-style throughput limits significantly, making it a real but config-dependent latent defect rather than an out-of-the-box exploitable path.

## Recommendation
Remove the `if self.staked_throttling_on_load_threshold > 0` guard around the `staked_throttling_enabled` update (or clamp `staked_throttling_on_load_threshold` to a minimum of `1` at construction time), so the toggle is always re-evaluated against `updated_load_ema` on every EMA tick regardless of how the threshold rounds. This restores the invariant that stake-weighted throttling activates whenever load is sustained, independent of the specific configured throughput cap.

## Proof of Concept
1. Construct `StakedStreamLoadEMA::new(stats, max_unstaked_connections, max_streams_per_ms)` with a `max_streams_per_ms` small enough that `(0.95 * max_staked_load_in_ms * STREAM_LOAD_EMA_INTERVAL_MS) as u64 == 0` (e.g., `max_streams_per_ms` on the order of a few streams/ms with unstaked connections allowed, per the formula at lines 53-72).
2. Drive `load_in_recent_interval` arbitrarily high and call `update_ema` — `staked_throttling_on_load_threshold` is `0`, so the `if` body at lines 132-137 never executes and `staked_throttling_enabled` remains `false` (its constructed default).
3. Call `available_load_capacity_in_throttling_duration(ConnectionPeerType::Staked(1), total_stake)` for a peer with stake `1` versus `total_stake` in the billions — per lines 172-186 the `else` branch returns the full `max_staked_load_in_throttling_window`, identical to what a top validator would receive, confirming the stake-proportional cap never engages regardless of sustained load.

### Citations

**File:** streamer/src/nonblocking/stream_throttle.rs (L60-72)
```rust
        let max_staked_load_in_ema_interval = max_staked_load_in_ms * STREAM_LOAD_EMA_INTERVAL_MS;
        let max_staked_load_in_throttling_window =
            max_staked_load_in_ms * STREAM_THROTTLING_INTERVAL_MS;

        let max_unstaked_load_in_throttling_window = if allow_unstaked_streams {
            MAX_UNSTAKED_TPS * STREAM_THROTTLING_INTERVAL_MS / 1000
        } else {
            0
        };

        let staked_throttling_on_load_threshold = (STAKED_THROTTLING_ON_LOAD_THRESHOLD_RATIO
            * (max_staked_load_in_ema_interval as f64))
            as u64;
```

**File:** streamer/src/nonblocking/stream_throttle.rs (L74-84)
```rust
        Self {
            current_load_ema: AtomicU64::default(),
            load_in_recent_interval: AtomicU64::default(),
            last_update: RwLock::new(Instant::now()),
            stats,
            max_staked_load_in_throttling_window,
            max_unstaked_load_in_throttling_window,
            max_streams_per_ms,
            staked_throttling_on_load_threshold,
            staked_throttling_enabled: AtomicBool::new(false),
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
