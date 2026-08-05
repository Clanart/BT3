## Analog Identified: QUIC connection-table pruning uses a self-resettable `last_update` timestamp — analogous to the `stopTrade`/`updateTPAndSL` bypass

### Title
Unprivileged clients can indefinitely evade `ConnectionTable::prune_oldest` eviction by refreshing `last_update` via trivial streams, enabling QUIC/TPU connection-slot exhaustion - (`streamer/src/nonblocking/quic.rs`)

### Summary
The external report's core primitive is: a delay/staleness-based enforcement check (`ot.lastUpdateTime + minAcceptanceDelay <= block.timestamp`) is trivially reset by a cheap, legitimate-looking user action (`updateTPAndSL`), letting the actor perpetually avoid being selected for a punitive/corrective action (liquidation). Agave's QUIC/TPU streamer contains the same broken invariant: connection eviction under capacity pressure is driven purely by a `last_update` timestamp that any connected peer can refresh at will by finishing a trivial stream, letting it evade `prune_oldest` indefinitely and crowd out legitimate incoming connections.

### Finding Description
`ConnectionTable::prune_oldest` selects which connection(s) to evict when a table (staked or unstaked) is at capacity, choosing the group with the minimum `last_update` value: [1](#0-0) 

`last_update` is an `Arc<AtomicU64>` stored per `ConnectionEntry` and shared with the connection's QoS context. It is updated every time a stream on that connection finishes — i.e. on ordinary, cheap client activity: [2](#0-1) [3](#0-2) 

This is invoked from the per-stream read loop as soon as a stream reaches `StreamState::Finished`: [4](#0-3) 

`prune_oldest` is invoked when the unstaked (or staked) connection table is at/near capacity, to make room for new connections: [5](#0-4) [6](#0-5) 

The broken invariant is identical to the report's: the value used to decide "staleness" for a protective/corrective mechanism (eviction under table pressure) is refreshed by the very party the mechanism is supposed to constrain, via an action that has nothing to do with the property the timestamp is meant to represent (genuine connection age/inactivity). Just as the position owner in the report could indefinitely front-run liquidation with `updateTPAndSL`, any unstaked/low-stake peer holding a connection can indefinitely front-run `prune_oldest` by sending a single trivial byte on a unidirectional stream (closing it immediately) any time before the next pruning pass, resetting `last_update` and remaining "freshest" forever. No existing guard prevents this: `prune_oldest` has no separate notion of connection creation time, no monotonic minimum age, and no rate limit tied to how often `last_update` may be refreshed for pruning purposes (the connections-per-minute limiter only throttles new connection establishment, not stream activity on existing ones).

### Impact Explanation
An attacker who establishes a modest number of unstaked (or low-stake) connections — well within existing per-IP/per-pubkey connection limits — and periodically sends trivial streams on each of them can guarantee those connections are never the minimum-`last_update` group. When the unstaked connection table subsequently reaches capacity, `prune_oldest`/`prune_random` either fails to reclaim capacity from the attacker's connections or evicts other, genuinely idle legitimate clients instead, effectively occupying a persistent share of TPU/QUIC connection slots. This is a non-RPC remote resource-exhaustion vector against the TPU ingestion path (degrading legitimate clients'/validators' ability to submit transactions), falling within the "non-RPC remote exhaustion/crash" impact category — achievable by any unprivileged network peer with no stake or trust assumption.

### Likelihood Explanation
The primitive requires only standard, unprivileged QUIC client behavior (open connection, open+close a tiny unidirectional stream periodically) — no validator/leader/trusted-role assumption, no malicious peer collusion beyond the attacker's own connections, and no elevated stake (unstaked connections are explicitly supported and subject to this same pruning logic). The cost is a handful of long-lived connections plus low-rate stream traffic, which is cheap and easily sustained, making this a low-cost, high-likelihood degradation vector rather than a theoretical one.

### Recommendation
Do not use a self-refreshable "last activity" timestamp as the sole staleness signal for capacity-based eviction. Track connection *age* (creation time) independently of stream activity and factor it into `prune_oldest`/`prune_random` selection, or cap how much credit continued stream activity can give toward avoiding eviction (e.g., combine age-based and activity-based signals, similar to the report's recommendation to track a separate, activity-independent timestamp for enforcement decisions).

### Proof of Concept
1. Open N unstaked QUIC connections to a validator's TPU endpoint (N bounded by `DEFAULT_MAX_QUIC_CONNECTIONS_PER_UNSTAKED_PEER` per IP, more via multiple source IPs).
2. On each connection, periodically open a minimal unidirectional stream, send a few bytes, and close it — cheap and well below any stream-count throttling limits.
3. Each stream completion triggers `on_stream_finished`, storing `timing::timestamp()` into that connection's `last_update` (`simple_qos.rs:379-383` / `swqos.rs:490-494`).
4. As other legitimate clients connect and the unstaked table approaches `max_unstaked_connections`, `prune_unstaked_connection_table` invokes `ConnectionTable::prune_oldest`, which picks the group with the minimum `last_update` (`quic.rs:964-980`) — the attacker's continuously-refreshed connections are never selected, so genuinely idle/legitimate connections are evicted instead (or capacity simply cannot be reclaimed from the attacker), denying/degrading TPU connection slots for legitimate traffic.

### Citations

**File:** streamer/src/nonblocking/quic.rs (L689-693)
```rust
                // The stream is finished, break out of the loop and close the stream.
                Ok(StreamState::Finished) => {
                    qos.on_stream_finished(&context);
                    break;
                }
```

**File:** streamer/src/nonblocking/quic.rs (L943-980)
```rust
/// Prune the connection which has the oldest update
///
/// Return number pruned
impl<S: OpaqueStreamerCounter> ConnectionTable<S> {
    pub(crate) fn new(table_type: ConnectionTableType, cancel: CancellationToken) -> Self {
        Self {
            table: IndexMap::default(),
            total_size: 0,
            table_type,
            cancel,
        }
    }

    fn table_size(&self) -> usize {
        self.total_size
    }

    fn is_staked(&self) -> bool {
        matches!(self.table_type, ConnectionTableType::Staked)
    }

    pub(crate) fn prune_oldest(&mut self, max_size: usize) -> usize {
        let mut num_pruned = 0;
        let key = |(_, connections): &(_, &Vec<_>)| {
            connections.iter().map(ConnectionEntry::last_update).min()
        };
        while self.total_size.saturating_sub(num_pruned) > max_size {
            match self.table.values().enumerate().min_by_key(key) {
                None => break,
                Some((index, connections)) => {
                    num_pruned += connections.len();
                    self.table.swap_remove_index(index);
                }
            }
        }
        self.total_size = self.total_size.saturating_sub(num_pruned);
        num_pruned
    }
```

**File:** streamer/src/nonblocking/simple_qos.rs (L310-327)
```rust
            match conn_context.peer_type() {
                ConnectionPeerType::Staked(stake) => {
                    let mut connection_table_l = self.staked_connection_table.lock().await;

                    if connection_table_l.total_size >= self.config.max_staked_connections {
                        let num_pruned =
                            connection_table_l.prune_random(PRUNE_RANDOM_SAMPLE_SIZE, stake);

                        debug!(
                            "Pruned {} staked connections to make room for new staked connection \
                             from {}",
                            num_pruned, conn_context.remote_address,
                        );
                        self.stats
                            .num_evictions_staked
                            .fetch_add(num_pruned, Ordering::Relaxed);
                        update_open_connections_stat(&self.stats, &connection_table_l);
                    }
```

**File:** streamer/src/nonblocking/simple_qos.rs (L379-383)
```rust
    fn on_stream_finished(&self, context: &SimpleQosConnectionContext) {
        context
            .last_update
            .store(timing::timestamp(), Ordering::Relaxed);
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

**File:** streamer/src/nonblocking/swqos.rs (L490-494)
```rust
    fn on_stream_finished(&self, context: &SwQosConnectionContext) {
        context
            .last_update
            .store(timing::timestamp(), Ordering::Relaxed);
    }
```
