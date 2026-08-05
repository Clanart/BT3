### Title
Staked QUIC/TPU connection privileges are snapshotted at connect-time and never re-evaluated or revoked for the connection's lifetime - (File: streamer/src/nonblocking/swqos.rs)

### Summary
The Rubicon bug is a role (`strategist`) that can be *granted* but has no mechanism to be *revoked*, letting a stale/compromised privileged actor keep acting on the system forever. The same broken invariant — "grant with no corresponding revoke path for the life of the object" — exists in Agave's QUIC/TPU connection admission logic: a peer's `ConnectionPeerType::Staked(stake)` classification (and the elevated resource entitlements that come with it) is computed once from a stake snapshot when the connection is accepted, and is never re-derived or downgraded for as long as that connection stays open, even though the authoritative stake table (`StakedNodes`) is refreshed independently in the background.

### Finding Description
When a QUIC connection is accepted, `SwQos::build_connection_context` (and the analogous `SimpleQos::build_connection_context`) looks up the peer's current stake via `get_connection_stake`/`StakedNodes::get_node_stake` and bakes the result into an immutable `ConnectionPeerType` stored in the connection context: [1](#0-0) 

That single snapshot is used to:
- pick the `staked_connection_table` vs `unstaked_connection_table` slot and per-peer connection cap,
- compute the maximum concurrent uni-streams (`compute_max_allowed_uni_streams_with_rtt`),
- decide throttling capacity (`available_load_capacity_in_throttling_duration`) for the entire connection lifetime. [2](#0-1) [3](#0-2) 

`ConnectionEntry` itself stores `peer_type` once at creation and exposes it only via `stake()`, with no setter or refresh path: [4](#0-3) 

Meanwhile, the authoritative `StakedNodes` table (used to classify *new* connections) is refreshed independently by a background service (`core/src/staked_nodes_updater_service.rs`), but nothing in the connection-handling code path re-checks a live connection's actual current stake against this table and downgrades it. `staked_connection_table` entries are explicitly protected from the "unstaked" eviction path (`prune_unstaked_connection_table` only prunes `unstaked_connection_table`) and are only evicted by `prune_random`, which is stake-weighted and biased to spare the highest-stake entries — the exact entries most likely to have been staked-then-unstaked, since they were admitted at a high stake value: [5](#0-4) [6](#0-5) 

Existing guards do not stop this path:
- The per-IP/per-pubkey connection and rate limiters bound *new* connection attempts, not the resource entitlement of an already-admitted connection.
- `SimpleQosBanlist`/ban mechanisms address explicit malicious behavior, not stake decay — a peer that behaves well but simply unstakes is never banned or reclassified.
- QUIC idle timeouts close genuinely idle connections but do nothing for an active connection kept alive with legitimate-looking traffic.

### Impact Explanation
A peer can obtain a `Staked` classification while briefly holding stake, open one or more QUIC/TPU connections that occupy a reserved slot in the bounded `staked_connection_table` (capacity `max_staked_connections`) with an inflated `max_uni_streams` and elevated per-interval throughput quota, then reduce/remove that stake. The connection retains its staked-tier entitlements — occupying capacity that should be reserved for genuinely staked validators, and consuming a disproportionate share of ingress bandwidth/streams relative to current (near-zero) stake — degrading TPU ingest for legitimately staked validators. This is a non-RPC remote resource-exhaustion/degradation vector on the TPU/QUIC surface, consistent with the "cannot be revoked once granted" bug class in the seed report, translated to a resource-privilege-revocation gap rather than a fund-theft-role gap.

### Likelihood Explanation
Likelihood is moderate: it requires the attacker to hold some non-zero stake at connection time (the classification threshold in `SwQos` even explicitly downgrades very-low stake ratios to `Unstaked`, so a meaningful minimum stake is needed to cross into the `Staked` tier), and then to keep connections alive after unstaking. This is more feasible than a full validator compromise, but it is not a fully "zero cost, no prerequisite" unprivileged path, which tempers confidence in this being squarely in-scope versus requiring at least transient stake ownership.

### Recommendation
Periodically re-evaluate the stake of already-admitted connections (e.g., on each throttling/EMA tick or on a fixed interval) against the current `StakedNodes` snapshot, and downgrade/evict connections whose backing stake has dropped below the classification threshold, mirroring how `add_authorized_voter`/`remove-all` at least provides an explicit revoke path — an equivalent explicit "downgrade/evict stale-staked connection" path should be added here.

### Proof of Concept
1. Attacker acquires enough stake to cross the `SwQos` staked-classification threshold (`stake_ratio >= min_stake_ratio` in `build_connection_context`). [7](#0-6) 
2. Attacker opens QUIC/TPU connections; they are admitted into `staked_connection_table` with high `max_uni_streams` and throttling capacity computed from that stake snapshot. [8](#0-7) 
3. Attacker withdraws/redelegates stake so their current stake in `StakedNodes` is now ~0 (new connections would classify as `Unstaked`), but keeps the already-open connections alive with periodic legitimate traffic.
4. Because `ConnectionEntry`/`SwQosConnectionContext` never re-derive `peer_type` from the live `StakedNodes` table, the connections keep their staked-tier stream quota and their protected slot in `staked_connection_table`, immune to `prune_unstaked_connection_table` and biased away from `prune_random` eviction due to the (stale) recorded high stake. [9](#0-8) [5](#0-4)

### Citations

**File:** streamer/src/nonblocking/swqos.rs (L147-239)
```rust
fn compute_max_allowed_uni_streams_with_rtt(
    rtt_millis: u32,
    peer_type: ConnectionPeerType,
    total_stake: u64,
) -> u32 {
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
        }
        ConnectionPeerType::Unstaked => QUIC_MAX_UNSTAKED_CONCURRENT_STREAMS,
    };
    // scale amount of streams based on RTT if RTT is larger than REFERENCE_RTT_MS
    // multiply first then divide to avoid rounding errors.
    (streams.saturating_mul(rtt_millis.clamp(REFERENCE_RTT_MS, MAX_RTT_MS))) / REFERENCE_RTT_MS
}

impl SwQos {
    fn cache_new_connection(
        &self,
        client_connection_tracker: ClientConnectionTracker,
        connection: &Connection,
        mut connection_table_l: MutexGuard<ConnectionTable<ConnectionStreamCounter>>,
        conn_context: &SwQosConnectionContext,
    ) -> Result<
        (
            Arc<AtomicU64>,
            CancellationToken,
            Arc<ConnectionStreamCounter>,
        ),
        ConnectionHandlerError,
    > {
        // get current RTT and limit it to MAX_RTT_MS right away
        let rtt_millis = connection.rtt().as_millis().min(MAX_RTT_MS as u128) as u32;
        let max_uni_streams = VarInt::from_u32(compute_max_allowed_uni_streams_with_rtt(
            rtt_millis,
            conn_context.peer_type(),
            conn_context.total_stake,
        ));
        let remote_addr = conn_context.remote_address;

        let max_connections_per_peer = match conn_context.peer_type() {
            ConnectionPeerType::Unstaked => self.config.max_connections_per_unstaked_peer,
            ConnectionPeerType::Staked(_) => self.config.max_connections_per_staked_peer,
        };
        if let Some((last_update, cancel_connection, stream_counter)) = connection_table_l
            .try_add_connection(
                ConnectionTableKey::new(remote_addr.ip(), conn_context.remote_pubkey),
                remote_addr.port(),
                client_connection_tracker,
                Some(connection.clone()),
                conn_context.peer_type(),
                conn_context.last_update.clone(),
                max_connections_per_peer,
                || Arc::new(ConnectionStreamCounter::new()),
            )
        {
            update_open_connections_stat(&self.stats, &connection_table_l);
            drop(connection_table_l);

            connection.set_max_concurrent_uni_streams(max_uni_streams);
            debug!(
                "Peer type {:?}, total stake {}, max streams {} from peer {}",
                conn_context.peer_type(),
                conn_context.total_stake,
                max_uni_streams.into_inner(),
                remote_addr,
            );
            Ok((last_update, cancel_connection, stream_counter))
        } else {
            self.stats
                .connection_add_failed
                .fetch_add(1, Ordering::Relaxed);
            Err(ConnectionHandlerError::ConnectionAddError)
        }
    }
```

**File:** streamer/src/nonblocking/swqos.rs (L241-256)
```rust
    fn prune_unstaked_connection_table(
        &self,
        unstaked_connection_table: &mut ConnectionTable<ConnectionStreamCounter>,
        max_unstaked_connections: usize,
        stats: Arc<StreamerStats>,
    ) {
        if unstaked_connection_table.total_size >= max_unstaked_connections {
            // Prune the connection table down to 90% capacity
            const PRUNE_TABLE_RATIO: f64 = 0.90;
            let max_connections = (PRUNE_TABLE_RATIO * (max_unstaked_connections as f64)) as usize;
            let num_pruned = unstaked_connection_table.prune_oldest(max_connections);
            stats
                .num_evictions_unstaked
                .fetch_add(num_pruned, Ordering::Relaxed);
        }
    }
```

**File:** streamer/src/nonblocking/swqos.rs (L301-329)
```rust
impl QosController<SwQosConnectionContext> for SwQos {
    fn build_connection_context(&self, connection: &Connection) -> SwQosConnectionContext {
        let remote_address = connection.remote_address();
        get_connection_stake(connection, &self.staked_nodes).map_or(
            SwQosConnectionContext {
                peer_type: ConnectionPeerType::Unstaked,
                total_stake: 0,
                remote_pubkey: None,
                in_staked_table: false,
                remote_address,
                stream_counter: None,
                last_update: Arc::new(AtomicU64::new(timing::timestamp())),
            },
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

**File:** streamer/src/nonblocking/quic.rs (L860-901)
```rust
struct ConnectionEntry<S: OpaqueStreamerCounter> {
    cancel: CancellationToken,
    peer_type: ConnectionPeerType,
    last_update: Arc<AtomicU64>,
    port: u16,
    // We do not explicitly use it, but its drop is triggered when ConnectionEntry is dropped.
    _client_connection_tracker: ClientConnectionTracker,
    connection: Option<Connection>,
    stream_counter: Arc<S>,
}

impl<S: OpaqueStreamerCounter> ConnectionEntry<S> {
    fn new(
        cancel: CancellationToken,
        peer_type: ConnectionPeerType,
        last_update: Arc<AtomicU64>,
        port: u16,
        client_connection_tracker: ClientConnectionTracker,
        connection: Option<Connection>,
        stream_counter: Arc<S>,
    ) -> Self {
        Self {
            cancel,
            peer_type,
            last_update,
            port,
            _client_connection_tracker: client_connection_tracker,
            connection,
            stream_counter,
        }
    }

    fn last_update(&self) -> u64 {
        self.last_update.load(Ordering::Relaxed)
    }

    fn stake(&self) -> u64 {
        match self.peer_type {
            ConnectionPeerType::Unstaked => 0,
            ConnectionPeerType::Staked(stake) => stake,
        }
    }
```

**File:** streamer/src/nonblocking/quic.rs (L982-1006)
```rust
    // Randomly selects sample_size many connections, evicts the one with the
    // lowest stake, and returns the number of pruned connections.
    // If the stakes of all the sampled connections are higher than the
    // threshold_stake, rejects the pruning attempt, and returns 0.
    pub(crate) fn prune_random(&mut self, sample_size: usize, threshold_stake: u64) -> usize {
        let num_pruned = std::iter::once(self.table.len())
            .filter(|&size| size > 0)
            .flat_map(|size| {
                let mut rng = rng();
                repeat_with(move || rng.random_range(0..size))
            })
            .map(|index| {
                let connection = self.table[index].first();
                let stake = connection.map(|connection: &ConnectionEntry<S>| connection.stake());
                (index, stake)
            })
            .take(sample_size)
            .min_by_key(|&(_, stake)| stake)
            .filter(|&(_, stake)| stake < Some(threshold_stake))
            .and_then(|(index, _)| self.table.swap_remove_index(index))
            .map(|(_, connections)| connections.len())
            .unwrap_or_default();
        self.total_size = self.total_size.saturating_sub(num_pruned);
        num_pruned
    }
```
