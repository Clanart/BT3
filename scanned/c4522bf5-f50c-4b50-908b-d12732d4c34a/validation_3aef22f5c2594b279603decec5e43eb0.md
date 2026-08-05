Based on the code I was able to examine, I found a genuine structural analog to the reported bug class (a percentage-based limit calculation that omits a necessary secondary cap/guard), located in the QUIC stream-throttling logic.

### Title
Per-peer stream throttling allows exceeding the staked capacity pool when stake exceeds total_stake - ([File: streamer/src/nonblocking/stream_throttle.rs])

### Summary
`StakedStreamLoadEMA::available_load_capacity_in_throttling_duration` computes a staked peer's allowed stream quota as a stake-weighted fraction of a shared pool (`max_staked_load_in_throttling_window * stake / total_stake`), analogous to the reported bug's ratio-based limit. Unlike the sibling calculation in `swqos.rs`, it never validates that `stake <= total_stake` before applying the ratio, so an inconsistent stake snapshot can produce a per-peer quota larger than the intended pool cap, exactly the "missing max cap in a percentage-based limit" bug class described in the report.

### Finding Description
`available_load_capacity_in_throttling_duration` computes: [1](#0-0) 

```
self.max_staked_load_in_throttling_window
    .saturating_mul(stake)
    .checked_div(total_stake)
    .unwrap_or(self.max_unstaked_load_in_throttling_window + 1)
    .max(self.max_unstaked_load_in_throttling_window + 1)
```

The intent (mirrored by the sibling QUIC stream-cap function) is that a peer's share of `max_staked_load_in_throttling_window` should never exceed that window itself — i.e., the ratio `stake/total_stake` is assumed to be `<= 1`. The only defensive check here is for `total_stake == 0` (handled via `checked_div`/`unwrap_or`). There is **no check that `stake <= total_stake`**.

Contrast this with the closely related function in the same crate that computes per-connection concurrent-stream limits, which explicitly guards against `peer_stake > total_stake`: [2](#0-1) 

That function treats `peer_stake > total_stake` as an "invalid stake values" condition and falls back to a safe minimum. `available_load_capacity_in_throttling_duration` has no equivalent guard, so if the per-connection `stake` (from `ConnectionPeerType::Staked(stake)`) and the separately-supplied `total_stake` argument are ever inconsistent (e.g., read from different/stale `StakedNodes` snapshots at different points in the connection lifecycle, which is plausible since QUIC connections are long-lived while stake maps are refreshed periodically), `stake` could numerically exceed `total_stake`. In that case `stake.saturating_mul(...) / total_stake` yields a ratio greater than 1, producing an `available_load_capacity` larger than `max_staked_load_in_throttling_window` — the pool cap the mechanism is supposed to enforce.

This mirrors the reported bug precisely: a ratio/percentage-derived limit (`us_investors_limit`-equivalent = `max_staked_load_in_throttling_window`) is computed without clamping to a secondary invariant (`max_us_investors_percentage`-equivalent = "ratio must be ≤ 1 / result ≤ window cap"), allowing the effective value to exceed the intended ceiling.

### Impact Explanation
If exploitable, an over-computed `available_load_capacity_in_throttling_duration` for one staked peer would let that peer send more QUIC streams (transactions) within a throttling interval than the design intends for the entire staked pool share, undermining the QoS/anti-DoS mechanism for one client. This is a non-RPC, TPU/QUIC-path resource-exhaustion/degradation concern (single high-stake or spoofed-stake-window connection could consume disproportionate throttling budget), potentially degrading TPU ingestion for other staked/unstaked peers.

### Likelihood Explanation
This is **not confirmed as exploitable** with the evidence gathered — I was not able to trace, within the available tool budget, the exact call site(s) that supply `stake` and `total_stake` to `available_load_capacity_in_throttling_duration` to confirm whether they are always sourced from the same atomic snapshot (in which case `stake <= total_stake` would always hold structurally) or from potentially divergent reads. The presence of an identical, explicit guard in the sibling `swqos.rs` function strongly suggests the authors considered this a real, guarded-against invariant violation elsewhere in the same subsystem, but the guard is absent here. This should be treated as a **missing defensive check / inconsistent invariant enforcement** rather than a confirmed proven-exploitable bug.

### Recommendation
Add the same guard used in `swqos.rs`'s `compute_max_allowed_uni_streams_with_rtt`: before computing the ratio, check `total_stake == 0 || stake > total_stake` and fall back to a safe minimum (e.g., `max_unstaked_load_in_throttling_window + 1`) instead of computing an unguarded ratio, in `available_load_capacity_in_throttling_duration` at [1](#0-0) .

### Proof of Concept
Not fully constructible from local static analysis alone — it depends on whether `stake` and `total_stake` can diverge at the call site (unverified due to tool-budget exhaustion). Conceptually: 
```
load_ema.max_staked_load_in_throttling_window = 100;
load_ema.available_load_capacity_in_throttling_duration(
    ConnectionPeerType::Staked(150), // stake > total_stake
    100,                              // total_stake
);
// => 100 * 150 / 100 = 150, exceeding max_staked_load_in_throttling_window (100)
```
This numeric case (unlike the `swqos.rs` equivalent) is not rejected by any guard in the current code, matching the test file's existing pattern of directly manipulating internal fields (see `test_staked_capacity_shares_when_throttled` in the same file) but with `stake > total_stake` rather than `stake <= total_stake`. [3](#0-2)

### Citations

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

**File:** streamer/src/nonblocking/stream_throttle.rs (L323-350)
```rust
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

**File:** streamer/src/nonblocking/swqos.rs (L152-172)
```rust
    let streams = match peer_type {
        ConnectionPeerType::Staked(peer_stake) => {
            // No checked math for f64 type. So let's explicitly check for 0 here
            if total_stake == 0 || peer_stake > total_stake {
                warn!(
                    "Invalid stake values: peer_stake: {peer_stake:?}, total_stake: \
                     {total_stake:?}"
                );

                QUIC_MIN_STAKED_CONCURRENT_STREAMS
            } else {
                let delta = (QUIC_TOTAL_STAKED_CONCURRENT_STREAMS
                    - QUIC_MIN_STAKED_CONCURRENT_STREAMS) as f64;

                (((peer_stake as f64 / total_stake as f64) * delta) as u32
                    + QUIC_MIN_STAKED_CONCURRENT_STREAMS)
                    .clamp(
                        QUIC_MIN_STAKED_CONCURRENT_STREAMS,
                        QUIC_MAX_STAKED_CONCURRENT_STREAMS,
                    )
            }
```
