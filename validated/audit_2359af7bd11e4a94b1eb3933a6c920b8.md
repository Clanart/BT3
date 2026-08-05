## Title
Stale/overridden stake data lets `stake / total_stake` exceed 1.0 in QUIC stream-throttling, granting an attacker more stream capacity than the whole staked budget - (File: `streamer/src/nonblocking/stream_throttle.rs`)

### Summary
`BaseJumpRateModelV2.utilizationRate()` assumed `reserves <= cash`, and when that invariant was violated the ratio exceeded its intended [0,1] bound. The Agave analog is `StakedStreamLoadEMA::available_load_capacity_in_throttling_duration()`, which computes a per-connection share of the QUIC/TPU stream budget as `max_staked_load_in_throttling_window * stake / total_stake`, implicitly assuming `stake <= total_stake`. That invariant is not enforced at the point of use: `total_stake` and `stake` are read from `StakedNodes`, whose `total_stake` field can be made inconsistent with an individual entry via the `overrides` map, and `stake`/`total_stake` are captured at different times/levels of the connection-handling pipeline.

### Finding Description
`get_connection_stake()` in `streamer/src/nonblocking/quic.rs` returns `(pubkey, staked_nodes.get_node_stake(&pubkey), staked_nodes.total_stake())` [1](#0-0) . `StakedNodes::total_stake()` is computed once in `StakedNodes::new()`/`calculate_total_stake()` as the sum of `stakes` entries not present in `overrides`, plus the sum of `overrides` values [2](#0-1) , while `get_node_stake()` looks up `overrides` first, falling back to `stakes` [3](#0-2) .

This per-node value is then fed into `available_load_capacity_in_throttling_duration()`:
```
self.max_staked_load_in_throttling_window
    .saturating_mul(stake)
    .checked_div(total_stake)
    .unwrap_or(self.max_unstaked_load_in_throttling_window + 1)
    .max(self.max_unstaked_load_in_throttling_window + 1)
``` [4](#0-3) 

This is exactly the `borrows * BASE / (cash + borrows - reserves)` pattern from the report: a ratio computed as `numerator * constant / denominator` with the unchecked assumption `numerator <= denominator`. If, due to a stale `StakedNodes` snapshot construction (overrides applied to a subset of nodes, or overrides that individually exceed the aggregate computed from `stakes`), a single node's `stake` value used at lookup time is larger than the `total_stake` captured in the same `StakedNodes` instance, `stake / total_stake > 1`, and the peer is granted a stream quota **larger than `max_staked_load_in_throttling_window`** — i.e., larger than the entire budget the throttler reserves for *all* staked peers combined for that interval. There is no clamp of the result to `max_staked_load_in_throttling_window`; the code only guards against division-by-zero (`unwrap_or`) and enforces a *floor* via `.max(...)`, never a ceiling.

Unlike the two related, already-guarded call sites (`compute_max_allowed_uni_streams_with_rtt` in `swqos.rs`, which explicitly checks `peer_stake > total_stake` and falls back to a minimum [5](#0-4) , and `build_connection_context`, which computes `stake_ratio = stake / total_stake` only to classify a peer as staked/unstaked, not to size a hard resource budget [6](#0-5) ), `available_load_capacity_in_throttling_duration` has **no such guard**, and its output is used directly as `max_streams_per_throttling_interval` in `throttle_stream()` to decide how many streams a single connection may open before being throttled [7](#0-6) [8](#0-7) .

### Impact Explanation
This falls in the QUIC/TPU category of valid impact ("non-RPC remote exhaustion/crash"). If a single connection's granted quota can exceed the aggregate staked stream budget, one (or a small set of) staked peer(s) can consume disproportionately more of the leader's/validator's ingest stream capacity than their stake entitles them to, starving legitimate staked traffic and degrading transaction ingestion for the whole cluster during that leader's slot — a resource-exhaustion condition on the TPU path, not merely a display/metrics bug.

### Likelihood Explanation
Exploitability depends on whether `StakedNodes` can, in practice, be constructed/updated such that a node's effective `stake` exceeds the `total_stake` recorded in the same snapshot (e.g., via the `overrides` mechanism, or via a race between epoch-boundary stake updates and the still-live `StakedNodes` instance being read by many concurrent connection handlers). I was not able to fully trace every caller that constructs `overrides` for `StakedNodes::new()` within the indexed portion of the codebase, so I cannot confirm with certainty that an inconsistent `(stake, total_stake)` pair is reachable from unprivileged/attacker-controlled input today; this is the main open question. Given the existence of an identical class of bug already patched defensively at the sibling call site in `swqos.rs` (`compute_max_allowed_uni_streams_with_rtt` explicitly checks `peer_stake > total_stake`), the missing symmetric check in `stream_throttle.rs` looks like an overlooked instance of the same known invariant violation rather than a hypothetical one.

### Recommendation
Clamp the computed capacity to `self.max_staked_load_in_throttling_window` (an explicit ceiling, not just the `unwrap_or`/`.max()` floor), and/or validate `stake <= total_stake` before the division, mirroring the guard already present in `compute_max_allowed_uni_streams_with_rtt`:
```rust
self.max_staked_load_in_throttling_window
    .saturating_mul(stake.min(total_stake))
    .checked_div(total_stake)
    .unwrap_or(self.max_unstaked_load_in_throttling_window + 1)
    .clamp(
        self.max_unstaked_load_in_throttling_window + 1,
        self.max_staked_load_in_throttling_window,
    )
```

### Proof of Concept
Conceptual PoC based on local code (not independently executed):
1. Construct/obtain a `StakedNodes` instance where `overrides` contains an entry for pubkey `P` with `stake_P` larger than the `total_stake` computed by `calculate_total_stake` for the rest of the map (e.g., because `total_stake` was computed before `overrides` was updated, or because `overrides` intentionally sets a value not reconciled with the aggregate) [2](#0-1) .
2. Establish a QUIC connection identified as pubkey `P`; `get_connection_stake` returns `(P, stake_P, total_stake)` with `stake_P > total_stake` [1](#0-0) .
3. `available_load_capacity_in_throttling_duration(ConnectionPeerType::Staked(stake_P), total_stake)` computes `max_staked_load_in_throttling_window * stake_P / total_stake > max_staked_load_in_throttling_window` [4](#0-3) .
4. `throttle_stream` uses this inflated value as `max_streams_per_throttling_interval`, allowing connection `P` alone to open more streams per interval than the entire staked-peer budget permits [8](#0-7) .

I could not locate, within the indexed code, a concrete production code path that populates `overrides` with attacker-influenced values or otherwise guarantees `stake > total_stake` is reachable outside of test/override configuration — this would need to be verified in a full checkout (e.g., via `grep` for all `StakedNodes::new(` call sites and how `overrides` is populated in production, which may not be fully covered by the index) before treating this as a confirmed, exploitable vulnerability rather than a defense-in-depth gap analogous to the reported bug class.

### Citations

**File:** streamer/src/nonblocking/quic.rs (L416-428)
```rust
pub fn get_connection_stake(
    connection: &Connection,
    staked_nodes: &RwLock<StakedNodes>,
) -> Option<(Pubkey, u64, u64)> {
    let pubkey = get_remote_pubkey(connection)?;
    debug!("Peer public key is {pubkey:?}");
    let staked_nodes = staked_nodes.read().unwrap();
    Some((
        pubkey,
        staked_nodes.get_node_stake(&pubkey)?,
        staked_nodes.total_stake(),
    ))
}
```

**File:** streamer/src/streamer.rs (L408-428)
```rust
impl StakedNodes {
    fn calculate_total_stake(
        stakes: &HashMap<Pubkey, u64>,
        overrides: &HashMap<Pubkey, u64>,
    ) -> u64 {
        stakes
            .iter()
            .filter(|(pubkey, _)| !overrides.contains_key(pubkey))
            .map(|(_, &stake)| stake)
            .chain(overrides.values().copied())
            .sum()
    }

    pub fn new(stakes: Arc<HashMap<Pubkey, u64>>, overrides: HashMap<Pubkey, u64>) -> Self {
        let total_stake = Self::calculate_total_stake(&stakes, &overrides);
        Self {
            stakes,
            overrides,
            total_stake,
        }
    }
```

**File:** streamer/src/streamer.rs (L430-436)
```rust
    pub fn get_node_stake(&self, pubkey: &Pubkey) -> Option<u64> {
        self.overrides
            .get(pubkey)
            .or_else(|| self.stakes.get(pubkey))
            .filter(|&&stake| stake > 0)
            .copied()
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

**File:** streamer/src/nonblocking/swqos.rs (L152-163)
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
```

**File:** streamer/src/nonblocking/swqos.rs (L292-298)
```rust
    fn max_streams_per_throttling_interval(&self, conn_context: &SwQosConnectionContext) -> u64 {
        self.staked_stream_load_ema
            .available_load_capacity_in_throttling_duration(
                conn_context.peer_type,
                conn_context.total_stake,
            )
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
