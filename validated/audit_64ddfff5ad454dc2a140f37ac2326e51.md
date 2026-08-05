### Title
Single low-stake QUIC connection can flip the global `staked_throttling_enabled` switch and starve all other staked TPU clients of stream bandwidth - (File: streamer/src/nonblocking/stream_throttle.rs)

### Summary
`StakedStreamLoadEMA` computes a single, cluster-wide binary flag (`staked_throttling_enabled`) from an EMA of *aggregate* stream load contributed by all staked QUIC connections. While the flag is `false`, **every** staked connection — regardless of its own stake — is granted the entire staked stream budget (`max_staked_load_in_throttling_window`) for the interval. This lets one minimally-staked, unprivileged attacker unilaterally burst enough streams to flip the flag to `true`, at which point every other staked connection's per-connection stream allowance drops from "full budget" to a tiny `stake / total_stake` share. The attacker can then sustain a small trickle of streams to keep the shared EMA above threshold, indefinitely suppressing legitimate stakers' TPU throughput — the same "single unprivileged actor manipulates a shared aggregate switch at minimal cost to degrade service for everyone else" pattern as the PoolTogether `largestTierClaimed` bug.

### Finding Description
`StakedStreamLoadEMA::available_load_capacity_in_throttling_duration` decides how many QUIC streams (i.e., transactions) a connection may push to the validator's TPU in one `STREAM_THROTTLING_INTERVAL_MS` window: [1](#0-0) 

```
Staked(stake) => {
    if self.staked_throttling_enabled.load(...) {
        max_staked_load_in_throttling_window * stake / total_stake  // clamped
    } else {
        max_staked_load_in_throttling_window   // full budget, independent of `stake`
    }
}
```

Confirmed by the test `test_no_throttle_below_threshold`, which shows a connection with `stake=10` out of `total_stake=100` still receives the *entire* `max_staked_load_in_throttling_window` when throttling is off: [2](#0-1) 

The `staked_throttling_enabled` flag itself is a single, shared, cluster-wide value computed from an EMA of the sum of streams accepted from *all* staked connections combined (not per-connection), and is flipped on once the EMA crosses 95% of the max staked load: [3](#0-2) [4](#0-3) 

Every accepted stream from any staked connection calls `increment_load`, which bumps the shared `load_in_recent_interval` counter used to drive that EMA: [5](#0-4) [6](#0-5) 

The classification threshold for being treated as a "Staked" (rather than "Unstaked") peer is only that the peer's stake ratio exceeds `1 / (max_streams_per_ms * STREAM_THROTTLING_INTERVAL_MS)` of total network stake — a very small bar given typical total stake and default throttling parameters: [7](#0-6) 

**Broken invariant:** the per-connection stream quota is supposed to be proportional to a connection's own stake share so no single peer can dominate TPU ingest bandwidth. But this proportionality only kicks in *after* the shared EMA crosses a global threshold — a threshold that any single minimally-staked connection can push past on its own (since the "off" branch grants the *entire* budget to one connection), and can keep suppressed by drip-feeding a small stream rate afterward. There is no per-connection accounting or minimum-contributor requirement gating who can move the shared `staked_throttling_enabled` switch; a single low-stake actor's minimal, self-serving action (analogous to PoolTogether's single loss-making claim keeping `largestTierClaimed` elevated) determines a state that controls resource allocation for every other unprivileged staked client.

### Impact Explanation
Once the attacker flips `staked_throttling_enabled` to `true`, every other staked connection's allowed streams-per-interval collapses to `max_staked_load_in_throttling_window * stake / total_stake` (clamped to at least `max_unstaked_load_in_throttling_window + 1`), i.e. most legitimate stakers (who are not whales) get a bandwidth allocation close to the unstaked minimum. Since `throttle_stream` sleeps out the remainder of the interval once a connection's quota is exhausted, this directly increases TPU transaction-submission latency/loss for the wider staked client population, while the attacker — a single, low-stake, unprivileged peer — pays only the (small) cost of a burst plus a sustaining trickle. This is a non-RPC remote-exhaustion/degradation of TPU-ingest capacity for the cluster's staked clients, matching the "unfair advantage via DoS on a shared resource" class from the original report (validators/relayers with real transactions to submit are starved while an attacker maintains the throttled state cheaply).

### Likelihood Explanation
The attacker only needs: (1) enough stake to clear the low `min_stake_ratio` bar to be classified `Staked` (a trivially small fraction of total network stake given default `max_streams_per_ms`/`STREAM_THROTTLING_INTERVAL_MS`), and (2) the ability to open a QUIC connection and push streams at the full unthrottled rate momentarily, then a modest sustained rate. No validator/gossip/peer trust, no cross-validator collusion, and no special software modification are required — any staked QUIC client already meets the preconditions coded into `build_connection_context`/`try_add_connection`. This is a purely mechanical consequence of the throttling algorithm, not a race requiring precise timing beyond normal EMA update cadence (`STREAM_LOAD_EMA_INTERVAL_MS` = 5ms).

### Recommendation
Do not grant the full unthrottled budget to a single connection irrespective of its own stake share. Compute per-connection quota from `stake/total_stake` at all times (with a floor for small stakers, as already exists), rather than only after a global switch trips; alternatively, gate the transition of `staked_throttling_enabled` on load contributed by a sufficiently diverse set of connections/stake (e.g., require the aggregate load from any single connection/IP to be capped before counting toward the shared EMA), so a lone low-stake peer cannot single-handedly move the shared throttling state that governs everyone else's allocation.

### Proof of Concept
1. Attacker acquires stake just above `min_stake_ratio * total_stake` (computed in `build_connection_context`, `streamer/src/nonblocking/swqos.rs:318-329`), enough to be classified `ConnectionPeerType::Staked`.
2. Attacker opens one QUIC connection to a leader's TPU and, while `staked_throttling_enabled == false`, drives streams up to `max_staked_load_in_throttling_window` in successive `STREAM_THROTTLING_INTERVAL_MS` windows (`available_load_capacity_in_throttling_duration`, `stream_throttle.rs:167-188`, else-branch). Because the else-branch ignores the caller's own stake, this single connection alone can push `load_in_recent_interval`/`current_load_ema` past `staked_throttling_on_load_threshold` (`stream_throttle.rs:70-72,103-144`).
3. Once `staked_throttling_enabled` flips to `true`, every other staked connection's quota is reduced to its `stake/total_stake` share (`stream_throttle.rs:174-186`), which for the vast majority of staked clients is far smaller than the previously-available full budget.
4. Attacker maintains a smaller, self-sustaining stream rate to keep the shared EMA above threshold (`update_ema`, `stream_throttle.rs:103-144`), keeping the throttled state active and other legitimate staked clients bandwidth-starved, at a fraction of the cost originally required to trip the switch.

Note: I could not fully verify the exact default numeric values for `DEFAULT_MAX_STREAMS_PER_MS` / `DEFAULT_MAX_STAKED_CONNECTIONS` from the index in this session (the grep for `streamer/src/quic.rs` matched but content wasn't retrieved before the tool budget ran out), so the concrete magnitude of "how small the required attacker stake is" and "how large the achievable burst is" in a live deployment would need to be confirmed against `streamer/src/quic.rs` and `validator/src/cli.rs` defaults in a follow-up session.

### Citations

**File:** streamer/src/nonblocking/stream_throttle.rs (L32-32)
```rust
const STAKED_THROTTLING_ON_LOAD_THRESHOLD_RATIO: f64 = 0.95;
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

**File:** streamer/src/nonblocking/swqos.rs (L445-454)
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
```
