### Title
Unstaked QUIC/TPU stream quota is granted per-connection instead of globally, letting a Sybil (multi-address, zero-stake) attacker multiply their guaranteed allocation — ([File: streamer/src/nonblocking/stream_throttle.rs])

### Summary
The Zap Protocol bug is a "guaranteed minimum allocation per address" that a Sybil attacker can multiply by using many addresses instead of one, bypassing the intended global cap without ever staking. Agave's QUIC/TPU stream admission control (`SwQos`) has the same structural flaw: unstaked (non-staked) connections each receive a fixed, non-shared stream quota (`max_unstaked_load_in_throttling_window`), and that quota is tracked per `ConnectionStreamCounter` keyed per connection/pubkey/IP rather than as a global unstaked budget, so opening many independent unstaked connections multiplies the attacker's effective throughput with zero stake required.

### Finding Description
`StakedStreamLoadEMA::available_load_capacity_in_throttling_duration` returns, for `ConnectionPeerType::Unstaked`, the constant `max_unstaked_load_in_throttling_window` [1](#0-0) . This constant is computed once from `MAX_UNSTAKED_TPS` (200 TPS) and is identical for every unstaked connection [2](#0-1) [3](#0-2) .

Crucially, the EMA load tracker that is supposed to represent aggregate system load only accumulates *staked* traffic: `increment_load` only adds to `load_in_recent_interval` when `peer_type.is_staked()` [4](#0-3) . Unstaked stream volume is never fed back into the EMA, so there is no dynamic, aggregate cap on how much total throughput all unstaked connections combined can consume — each connection simply gets its own independent per-connection allowance via its own `ConnectionStreamCounter` and `throttle_stream` call [5](#0-4) , exactly like the Zap `calculateMaxAllocation` returning a flat guaranteed floor per address regardless of an aggregate check.

Admission for unstaked connections is gated by `max_unstaked_connections` (a table-size based cap with LRU pruning down to 90% capacity) and `max_connections_per_unstaked_peer` (per-IP/pubkey cap), not by any accounting of aggregate consumed stream budget [6](#0-5) [7](#0-6) . Any client without stake is routed into this unstaked path via `get_connection_stake`/`build_connection_context`, which falls back to `ConnectionPeerType::Unstaked` when the peer pubkey has no stake entry [8](#0-7) [9](#0-8) .

Since the unstaked bucket can hold up to `max_unstaked_connections` concurrent connections (each keyed by distinct pubkey/IP), and each gets the same fixed `max_unstaked_load_in_throttling_window` quota independent of how many other unstaked connections exist, an attacker who spins up `N` distinct QUIC client identities/source addresses (no stake, no vote account, no cost) obtains roughly `N × MAX_UNSTAKED_TPS` aggregate throughput into the TPU, rather than the single shared 20%-of-capacity budget (`EXPECTED_UNSTAKED_STREAMS_RATIO`) the system was designed to reserve for all unstaked traffic combined [10](#0-9) [11](#0-10) .

### Impact Explanation
This is the Agave analog of "max allocations bypassed via multiple addresses without staking": the per-connection/per-identity guaranteed quota was meant to be a small, bounded slice of total leader stream capacity reserved for unstaked clients, but because the quota is granted per connection and never aggregated/throttled globally, a zero-stake attacker can scale their effective allocation linearly with the number of Sybil identities/connections they open, up to `max_unstaked_connections`. This allows an unprivileged, unstaked remote actor to consume TPU/QUIC stream capacity far beyond the intended fraction, degrading transaction ingestion for legitimate (staked) senders — a non-RPC remote exhaustion/degradation vector on the TPU path.

### Likelihood Explanation
No stake, no privileged position, and no leaked keys are required — only the ability to open many independent QUIC connections with different peer pubkeys/source ports, which is standard SwQoS admission behavior already accounted for as "unstaked" traffic. The only friction is the pre-existing `max_connections_per_unstaked_peer` (per-IP/pubkey) and `max_unstaked_connections` (global table size) caps, both of which can be trivially worked around by rotating source IPs/ports and generating fresh ephemeral keypairs, since `ConnectionTableKey` is keyed by IP or pubkey and ordinary IP rotation/multiple hosts defeats it [12](#0-11) .

### Recommendation
Track and throttle unstaked stream consumption in aggregate (not just per-connection), e.g., by feeding unstaked stream counts into the shared EMA/load tracker (removing the `is_staked()` guard in `increment_load`) and deriving each unstaked connection's allowed throttling-window quota from a shared/divided budget (similar to how staked quota is divided by `total_stake`), rather than handing out the same static `max_unstaked_load_in_throttling_window` to every unstaked connection independently.

### Proof of Concept
1. Deploy a validator with default `SwQosConfig` (`max_unstaked_connections`, `max_connections_per_unstaked_peer` at their defaults) and TPU QUIC enabled.
2. From an attacker host with no stake and no vote account, open `N` (up to `max_unstaked_connections`) separate QUIC connections to TPU, each with a distinct ephemeral keypair (or from distinct source IPs) so each lands in a separate `ConnectionTableKey` slot in `unstaked_connection_table` [7](#0-6) .
3. On each connection, continuously open uni-streams to send transactions; each connection is throttled only against its own `ConnectionStreamCounter` at `max_unstaked_load_in_throttling_window` (derived from `MAX_UNSTAKED_TPS = 200`) [10](#0-9) [5](#0-4) .
4. Observe via `StreamerStats` (`open_unstaked_connections`, `throttled_unstaked_streams`) that aggregate unstaked throughput scales roughly linearly with `N`, exceeding the `EXPECTED_UNSTAKED_STREAMS_RATIO` (20%) share the design intends, while `increment_load`/EMA (which governs staked throttling) never reflects this unstaked load, since only staked increments are counted [4](#0-3) .

### Citations

**File:** streamer/src/nonblocking/stream_throttle.rs (L16-24)
```rust
/// Max TPS allowed for unstaked connection
const MAX_UNSTAKED_TPS: u64 = 200;
/// Expected fraction of max TPS to be consumed by unstaked connections
const EXPECTED_UNSTAKED_STREAMS_RATIO: f64 = 0.20;

pub const STREAM_THROTTLING_INTERVAL_MS: u64 = 100;
pub const STREAM_THROTTLING_INTERVAL: Duration =
    Duration::from_millis(STREAM_THROTTLING_INTERVAL_MS);
const STREAM_LOAD_EMA_INTERVAL_MS: u64 = 5;
```

**File:** streamer/src/nonblocking/stream_throttle.rs (L52-58)
```rust
        let allow_unstaked_streams = max_unstaked_connections > 0;
        let max_staked_load_in_ms = if allow_unstaked_streams {
            max_streams_per_ms
                - ((EXPECTED_UNSTAKED_STREAMS_RATIO * (max_streams_per_ms as f64)) as u64)
        } else {
            max_streams_per_ms
        };
```

**File:** streamer/src/nonblocking/stream_throttle.rs (L64-68)
```rust
        let max_unstaked_load_in_throttling_window = if allow_unstaked_streams {
            MAX_UNSTAKED_TPS * STREAM_THROTTLING_INTERVAL_MS / 1000
        } else {
            0
        };
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

**File:** streamer/src/nonblocking/swqos.rs (L301-313)
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
```

**File:** streamer/src/nonblocking/swqos.rs (L415-437)
```rust
                ConnectionPeerType::Unstaked => {
                    if let Ok((last_update, cancel_connection, stream_counter)) = self
                        .prune_unstaked_connections_and_add_new_connection(
                            client_connection_tracker,
                            connection,
                            self.unstaked_connection_table.clone(),
                            self.config.max_unstaked_connections,
                            conn_context,
                        )
                        .await
                    {
                        self.stats
                            .connection_added_from_unstaked_peer
                            .fetch_add(1, Ordering::Relaxed);
                        conn_context.in_staked_table = false;
                        conn_context.last_update = last_update;
                        conn_context.stream_counter = Some(stream_counter);
                        return Some(cancel_connection);
                    } else {
                        self.stats
                            .connection_add_failed_unstaked_node
                            .fetch_add(1, Ordering::Relaxed);
                    }
```

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

**File:** streamer/src/nonblocking/quic.rs (L916-928)
```rust
#[derive(Copy, Clone, Debug, Eq, Hash, PartialEq)]
pub(crate) enum ConnectionTableKey {
    IP(IpAddr),
    Pubkey(Pubkey),
}

impl ConnectionTableKey {
    pub(crate) fn new(ip: IpAddr, maybe_pubkey: Option<Pubkey>) -> Self {
        maybe_pubkey.map_or(ConnectionTableKey::IP(ip), |pubkey| {
            ConnectionTableKey::Pubkey(pubkey)
        })
    }
}
```
