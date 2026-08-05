### Title
Per-connection stream throttle counter reset by reconnect cycling bypasses stake-weighted rate limiting - (File: `streamer/src/nonblocking/quic.rs`, `streamer/src/nonblocking/swqos.rs`)

### Summary
The prePO bug is a case where a per-account rate-limit counter is bound to a resettable identity (a fresh account) instead of a persistent one (the user's total holdings), letting the user reset the counter by moving to a new identity. The same pattern exists in Agave's QUIC stream throttling: the `ConnectionStreamCounter`/`TokenBucket` that enforces `max_streams_per_throttling_interval` (stake-weighted quota) is created and stored inside the `ConnectionEntry` in the per-key `ConnectionTable`, and is discarded whenever the last connection for that key is removed. A peer can force removal of its table entry (by disconnecting) and immediately reconnect with the same pubkey/stake, receiving a brand-new, zeroed counter.

### Finding Description
`ConnectionTable::try_add_connection` fetches the shared `stream_counter` from the first existing `ConnectionEntry` for the key, or creates a fresh one via `stream_counter_factory` if none exists: [1](#0-0) 

`ConnectionTable::remove_connection` removes the `ConnectionEntry` for a (key, port, stable_id); when the vector becomes empty the whole table entry (and therefore the shared counter Arc, once no stream is mid-flight holding a reference) is dropped: [2](#0-1) 

The counter itself, `ConnectionStreamCounter`, only resets by wall-clock interval (`STREAM_THROTTLING_INTERVAL`) or when a new counter object is created — there is no persistence of the elapsed-usage state that survives connection teardown, and no check for how recently the pubkey/IP last disconnected before allowing a new connection with a fresh counter: [3](#0-2) 

`SwQos::cache_new_connection`/`try_add_connection` computes `max_streams_per_throttling_interval` proportional to the peer's stake and applies it via `throttle_stream` against `stream_counter.stream_count`: [4](#0-3) 

Because the throttle state lives entirely in the connection-scoped counter rather than in state keyed persistently on the pubkey across the whole `STREAM_THROTTLING_INTERVAL` window, a staked client that exhausts its interval quota can simply close its QUIC connection (client-initiated `close()`/idle timeout) and open a new one. `try_add_connection` (and its analog in `simple_qos.rs`, backed by a `TokenBucket`) has no independent minimum-reconnect-interval check tied to the pubkey; it only checks `max_connections_per_peer`/`max_staked_connections` concurrency limits, not historical usage. The reconnect therefore instantiates a fresh `ConnectionStreamCounter`/`TokenBucket` with a full quota, exactly mirroring the prePO pattern of resetting a per-identity limit by moving to a fresh identity — here the "identity" is the ephemeral connection object rather than the persistent pubkey.

### Impact Explanation
`max_streams_per_throttling_interval` and the stake-weighted uni-stream limits (`compute_max_allowed_uni_streams_with_rtt`) exist specifically to bound how much TPU/QUIC ingest bandwidth a single peer (weighted by stake) can consume, protecting the leader/validator from being overwhelmed by a single high-throughput sender and preserving fair, stake-proportional access to transaction ingestion. Bypassing this by reconnect-cycling allows a staked (or even unstaked, via `SimpleQos`'s `TokenBucket`-per-connection design) peer to sustain a stream/transaction submission rate far above its entitled share, causing local resource exhaustion (CPU/thread work in `handle_connection`, memory for stream buffers) and unfair QoS degradation for other peers — a non-RPC remote resource-exhaustion vector against the leader's ingest path.

### Likelihood Explanation
Exploitation requires only a QUIC client capable of opening/closing connections rapidly and does not require any privileged or trusted role — a staked or unstaked validator client behaving within protocol (no malicious peer/validator assumption beyond normal client behavior) can trigger this. The only friction is the concurrent-connection limits (`max_connections_per_peer`, `max_staked_connections`/`max_unstaked_connections`) and connection setup/handshake cost, which throttle the rate of reconnection but do not prevent it, and QUIC connection establishment is comparatively cheap relative to the bandwidth gained by resetting the stream quota every interval.

### Recommendation
Track cumulative stream usage per persistent identity (pubkey, or IP for unstaked) independent of connection lifetime — e.g., store the `ConnectionStreamCounter`/`TokenBucket` in a keyed map that survives `remove_connection` and is only evicted after quota-interval inactivity (similar to `KeyedRateLimiter` in `net-utils/src/token_bucket.rs`) rather than inside the per-connection `ConnectionEntry`. Alternatively, enforce a minimum reconnect interval per pubkey/IP before a fresh counter can be granted.

### Proof of Concept
1. Connect as a staked client to the leader's QUIC TPU port such that `SwQosConnectionContext` classifies the peer with `ConnectionPeerType::Staked(stake)`; `cache_new_connection` creates one `ConnectionStreamCounter` for the key `(ip, pubkey)`.
2. Open uni-streams up to `max_streams_per_throttling_interval` within the current `STREAM_THROTTLING_INTERVAL`; further streams get throttled/slept per `throttle_stream`.
3. Instead of waiting out the interval, close the QUIC connection (or let it idle-timeout), which triggers `remove_connection`, deleting the sole `ConnectionEntry` and its counter (`table.entry(key)` becomes empty and is `swap_remove_entry`'d).
4. Immediately reconnect with the same keypair/stake; `try_add_connection` re-enters the `Staked` branch, finds no existing entry for the key, and `cache_new_connection` creates a brand-new `ConnectionStreamCounter` with `stream_count = 0`.
5. Repeat steps 2–4 to sustain a stream rate many multiples of `max_streams_per_throttling_interval`, exceeding the stake-proportional quota indefinitely. [5](#0-4) [6](#0-5)

### Citations

**File:** streamer/src/nonblocking/quic.rs (L1008-1051)
```rust
    pub(crate) fn try_add_connection<F: FnOnce() -> Arc<S>>(
        &mut self,
        key: ConnectionTableKey,
        port: u16,
        client_connection_tracker: ClientConnectionTracker,
        connection: Option<Connection>,
        peer_type: ConnectionPeerType,
        last_update: Arc<AtomicU64>,
        max_connections_per_peer: usize,
        stream_counter_factory: F,
    ) -> Option<(Arc<AtomicU64>, CancellationToken, Arc<S>)> {
        let connection_entry = self.table.entry(key).or_default();
        let has_connection_capacity = connection_entry
            .len()
            .checked_add(1)
            .map(|c| c <= max_connections_per_peer)
            .unwrap_or(false);
        if has_connection_capacity {
            let cancel = self.cancel.child_token();
            let stream_counter = connection_entry
                .first()
                .map(|entry| entry.stream_counter.clone())
                .unwrap_or_else(stream_counter_factory);
            connection_entry.push(ConnectionEntry::new(
                cancel.clone(),
                peer_type,
                last_update.clone(),
                port,
                client_connection_tracker,
                connection,
                stream_counter.clone(),
            ));
            self.total_size += 1;
            Some((last_update, cancel, stream_counter))
        } else {
            if let Some(connection) = connection {
                connection.close(
                    CONNECTION_CLOSE_CODE_TOO_MANY.into(),
                    CONNECTION_CLOSE_REASON_TOO_MANY,
                );
            }
            None
        }
    }
```

**File:** streamer/src/nonblocking/quic.rs (L1054-1087)
```rust
    pub(crate) fn remove_connection(
        &mut self,
        key: ConnectionTableKey,
        port: u16,
        stable_id: usize,
    ) -> usize {
        if let Entry::Occupied(mut e) = self.table.entry(key) {
            let e_ref = e.get_mut();
            let old_size = e_ref.len();

            e_ref.retain(|connection_entry| {
                // Retain the connection entry if the port is different, or if the connection's
                // stable_id doesn't match the provided stable_id.
                // (Some unit tests do not fill in a valid connection in the table. To support that,
                // if the connection is none, the stable_id check is ignored. i.e. if the port matches,
                // the connection gets removed)
                connection_entry.port != port
                    || connection_entry
                        .connection
                        .as_ref()
                        .and_then(|connection| (connection.stable_id() != stable_id).then_some(0))
                        .is_some()
            });
            let new_size = e_ref.len();
            if e_ref.is_empty() {
                e.swap_remove_entry();
            }
            let connections_removed = old_size.saturating_sub(new_size);
            self.total_size = self.total_size.saturating_sub(connections_removed);
            connections_removed
        } else {
            0
        }
    }
```

**File:** streamer/src/nonblocking/stream_throttle.rs (L195-230)
```rust
#[derive(Debug)]
pub struct ConnectionStreamCounter {
    pub(crate) stream_count: AtomicU64,
    last_throttling_instant: RwLock<tokio::time::Instant>,
}

impl OpaqueStreamerCounter for ConnectionStreamCounter {}

impl ConnectionStreamCounter {
    pub fn new() -> Self {
        Self {
            stream_count: AtomicU64::default(),
            last_throttling_instant: RwLock::new(tokio::time::Instant::now()),
        }
    }

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
```

**File:** streamer/src/nonblocking/swqos.rs (L181-239)
```rust
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

**File:** streamer/src/nonblocking/swqos.rs (L490-516)
```rust
    fn on_stream_finished(&self, context: &SwQosConnectionContext) {
        context
            .last_update
            .store(timing::timestamp(), Ordering::Relaxed);
    }

    #[allow(clippy::manual_async_fn)]
    fn on_new_stream(&self, context: &SwQosConnectionContext) -> impl Future<Output = ()> + Send {
        async move {
            let peer_type = context.peer_type();
            let remote_addr = context.remote_address;
            let stream_counter: &Arc<ConnectionStreamCounter> =
                context.stream_counter.as_ref().unwrap();

            let max_streams_per_throttling_interval =
                self.max_streams_per_throttling_interval(context);

            throttle_stream(
                &self.stats,
                peer_type,
                remote_addr,
                stream_counter,
                max_streams_per_throttling_interval,
            )
            .await;
        }
    }
```
