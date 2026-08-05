### Title
Staked-connection pruning is triggered and committed unconditionally before the per-peer capacity check, allowing a maxed-out staked peer to repeatedly evict unrelated legitimate staked connections without ever occupying a slot itself - (File: `streamer/src/nonblocking/simple_qos.rs`)

### Summary
In `SimpleQos::try_add_connection`, the decision to evict an existing staked connection (`prune_random`) and the corresponding `num_evictions_staked` stat update happen based solely on the *global* staked-table size, completely independent of whether the connecting peer can actually be admitted afterward. Admission is gated by a separate, *per-peer* capacity check inside `cache_new_connection` → `ConnectionTable::try_add_connection`. Because these two checks are decoupled and there is no rollback of the eviction on admission failure, a peer whose own `(ip, pubkey)` slot bucket is already at `max_connections_per_peer` can keep sending fresh QUIC connection attempts that always fail admission, yet each attempt — while the global table is full — still evicts one random lower-staked victim connection elsewhere in the table.

### Finding Description
The vulnerable sequence in `try_add_connection`: [1](#0-0) 

1. `connection_table_l.total_size >= self.config.max_staked_connections` is checked using only the aggregate table size.
2. If true, `prune_random(PRUNE_RANDOM_SAMPLE_SIZE, stake)` is called and immediately commits an eviction of a randomly sampled connection whose stake is lower than the connecting peer's stake: [2](#0-1) 
   The result (`num_pruned`) is unconditionally added to `stats.num_evictions_staked`: [3](#0-2) 
3. Only *after* this eviction has already been committed does the code attempt to actually admit the new connection via `cache_new_connection`, which internally re-checks a completely different, per-key capacity limit: [4](#0-3) [5](#0-4) 

The per-key check (`ConnectionTableKey::new(remote_addr.ip(), conn_context.remote_pubkey)`) is keyed by the connecting peer's own IP/pubkey, and is independent of the global-size check that gated the eviction. If the connecting peer already holds `max_connections_per_peer` live connections under that key (which it fully controls — it can simply keep those connections open), every subsequent connection attempt from that same peer will:
- Pass the banlist check and be classified `ConnectionPeerType::Staked(stake)`.
- Trigger the global-size branch (as long as the table is at capacity, e.g. refilled by legitimate traffic), causing `prune_random` to evict one random lower-staked connection belonging to an unrelated legitimate peer, and incrementing `num_evictions_staked`.
- Then fail in `cache_new_connection`'s per-peer capacity check and return `None`, closing the new connection with `CONNECTION_CLOSE_CODE_TOO_MANY`.

There is no code path that reverts or skips the eviction when the subsequent admission fails; the two checks (`total_size >= max_staked_connections` and per-peer `has_connection_capacity`) are entirely uncoordinated. The identical flaw pattern exists in the "Staked" branch of `SwQos::try_add_connection` as well, though there the peer at least falls back to attempting the unstaked table on failure: [6](#0-5) 

### Impact Explanation
An attacker who holds any non-trivial stake (enough to exceed the stake of at least some other connected staked peers) can:
1. Open exactly `max_connections_per_peer` connections and keep them alive to saturate their own per-peer bucket.
2. Repeatedly open and immediately have rejected additional QUIC connections from the same identity/IP whenever the staked-connection table is at capacity.

Each such rejected attempt still evicts one legitimate, unrelated staked peer's connection (`connection_removed`/prune internally decrements `total_size` and disconnects the victim), forcing that peer to re-establish its QUIC/TPU connection. Because the attacker never occupies the freed slot, and legitimate traffic will tend to refill the table back to capacity, this produces continuous, gratuitous churn of arbitrary lower-staked validators' TPU connections — degrading transaction ingestion for those validators — at essentially zero cost to the attacker (repeated QUIC handshakes are cheap relative to the disruption caused). This is a remote, non-RPC (QUIC/TPU) availability degradation vector against other validators' connections, fitting within the "non-RPC remote exhaustion/DoS" impact category.

### Likelihood Explanation
Moderate-to-high. The attacker needs only:
- Some non-negligible stake (any amount greater than the stake of some legitimate low-stake connected peers, since `prune_random` only evicts victims with `stake < threshold_stake`).
- The ability to open `max_connections_per_peer + 1` QUIC connections from the same pubkey/IP, which is trivial for any staked validator operator.
No special privileges, timing races, or malicious validator collusion are required — an attacker only needs to be an "unprivileged" staked TPU client sending standard QUIC connection attempts, i.e., a valid public input to the QUIC/TPU listener.

### Recommendation
Reorder or unify the eviction and admission decisions so that pruning is only committed if it will actually make room for a connection the caller can accept:
- Perform (or simulate) the per-peer capacity check for the incoming connection *before* deciding to prune, or
- Make `prune_random` + `try_add_connection` atomic under a single decision (e.g., only call `prune_random` if `has_connection_capacity` for the new key would hold after pruning), or
- Roll back the eviction (or refrain from counting it in `num_evictions_staked` / re-inserting the evicted entry) if the subsequent `cache_new_connection` call fails.

### Proof of Concept
A focused unit test in `streamer/src/nonblocking/simple_qos.rs` (or `quic.rs`'s `ConnectionTable` test module) can demonstrate this directly against `SimpleQos::try_add_connection` / `ConnectionTable`:

1. Build a `ConnectionTable::new(ConnectionTableType::Staked, ...)` and fill it to `max_staked_connections` with distinct legitimate staked peers of varying (low) stakes, using `try_add_connection` as done in the existing `test_prune_table_random` test: [7](#0-6) 
2. Pre-fill one attacker `ConnectionTableKey` up to `max_connections_per_peer` (so its own bucket is full).
3. Call the `SimpleQos::try_add_connection` async method for the attacker's pubkey with a fresh QUIC connection, with the staked table already at `max_staked_connections`.
4. Assert:
   - `stats.num_evictions_staked` increased by the amount `prune_random` returned (i.e., pruning happened).
   - One of the pre-existing *legitimate* (non-attacker) staked connections was actually removed from the table (`total_size` decreased, or that specific key is gone).
   - The overall call returns `None` (attacker's connection was rejected) and `stats.connection_add_failed` was incremented — confirming the attacker gained nothing while another peer's connection was destroyed.

This reproduces, using only in-repo code paths and no modification of validation logic, the exact behavior described: pruning/eviction of unrelated staked peers occurs and is recorded even when the initiating connection's own admission subsequently fails.

### Citations

**File:** streamer/src/nonblocking/simple_qos.rs (L206-223)
```rust
        let key = ConnectionTableKey::new(remote_addr.ip(), conn_context.remote_pubkey);
        if let Some((last_update, cancel_connection, stream_counter)) = connection_table_l
            .try_add_connection(
                key,
                remote_addr.port(),
                client_connection_tracker,
                Some(connection.clone()),
                conn_context.peer_type(),
                conn_context.last_update.clone(),
                self.config.max_connections_per_peer,
                || {
                    Arc::new(TokenBucket::new(
                        self.config.max_streams_per_second,
                        self.config.max_streams_per_second,
                        self.config.max_streams_per_second as f64,
                    ))
                },
            )
```

**File:** streamer/src/nonblocking/simple_qos.rs (L310-346)
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

                    if connection_table_l.total_size < self.config.max_staked_connections
                        && let Ok((last_update, cancel_connection, stream_counter)) = self
                            .cache_new_connection(
                                client_connection_tracker,
                                connection,
                                connection_table_l,
                                conn_context,
                            )
                    {
                        self.stats
                            .connection_added_from_staked_peer
                            .fetch_add(1, Ordering::Relaxed);
                        conn_context.last_update = last_update;
                        conn_context.stream_counter = Some(stream_counter);
                        return Some(cancel_connection);
                    }
                    None
                }
```

**File:** streamer/src/nonblocking/quic.rs (L986-1006)
```rust
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

**File:** streamer/src/nonblocking/quic.rs (L1008-1050)
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
```

**File:** streamer/src/nonblocking/quic.rs (L1894-1937)
```rust
    #[test]
    fn test_prune_table_random() {
        use std::net::Ipv4Addr;
        agave_logger::setup();
        let cancel = CancellationToken::new();
        let mut table = ConnectionTable::new(ConnectionTableType::Unstaked, cancel);

        let num_entries = 5;
        let max_connections_per_peer = 10;
        let sockets: Vec<_> = (0..num_entries)
            .map(|i| SocketAddr::new(IpAddr::V4(Ipv4Addr::new(i, 0, 0, 0)), 0))
            .collect();
        let stats: Arc<StreamerStats> = Arc::new(StreamerStats::default());

        for (i, socket) in sockets.iter().enumerate() {
            table
                .try_add_connection(
                    ConnectionTableKey::IP(socket.ip()),
                    socket.port(),
                    ClientConnectionTracker::new(stats.clone(), 1000).unwrap(),
                    None,
                    ConnectionPeerType::Staked((i + 1) as u64),
                    Arc::new(AtomicU64::new(i as u64)),
                    max_connections_per_peer,
                    || Arc::new(NullStreamerCounter {}),
                )
                .unwrap();
        }

        // Try pruninng with threshold stake less than all the entries in the table
        // It should fail to prune (i.e. return 0 number of pruned entries)
        let pruned = table.prune_random(/*sample_size:*/ 2, /*threshold_stake:*/ 0);
        assert_eq!(pruned, 0);

        // Try pruninng with threshold stake higher than all the entries in the table
        // It should succeed to prune (i.e. return 1 number of pruned entries)
        let pruned = table.prune_random(
            2,                      // sample_size
            num_entries as u64 + 1, // threshold_stake
        );
        assert_eq!(pruned, 1);
        // We had 5 connections and pruned 1, we should have 4 left
        assert_eq!(stats.open_connections.load(Ordering::Relaxed), 4);
    }
```

**File:** streamer/src/nonblocking/swqos.rs (L355-413)
```rust
                ConnectionPeerType::Staked(stake) => {
                    let mut connection_table_l = self.staked_connection_table.lock().await;

                    if connection_table_l.total_size >= self.config.max_staked_connections {
                        let num_pruned =
                            connection_table_l.prune_random(PRUNE_RANDOM_SAMPLE_SIZE, stake);
                        self.stats
                            .num_evictions_staked
                            .fetch_add(num_pruned, Ordering::Relaxed);
                        update_open_connections_stat(&self.stats, &connection_table_l);
                    }

                    if connection_table_l.total_size < self.config.max_staked_connections {
                        if let Ok((last_update, cancel_connection, stream_counter)) = self
                            .cache_new_connection(
                                client_connection_tracker,
                                connection,
                                connection_table_l,
                                conn_context,
                            )
                        {
                            self.stats
                                .connection_added_from_staked_peer
                                .fetch_add(1, Ordering::Relaxed);
                            conn_context.in_staked_table = true;
                            conn_context.last_update = last_update;
                            conn_context.stream_counter = Some(stream_counter);
                            return Some(cancel_connection);
                        }
                    } else {
                        // If we couldn't prune a connection in the staked connection table, let's
                        // put this connection in the unstaked connection table. If needed, prune a
                        // connection from the unstaked connection table.
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
                                .connection_added_from_staked_peer
                                .fetch_add(1, Ordering::Relaxed);
                            conn_context.in_staked_table = false;
                            conn_context.last_update = last_update;
                            conn_context.stream_counter = Some(stream_counter);
                            return Some(cancel_connection);
                        } else {
                            self.stats
                                .connection_add_failed_on_pruning
                                .fetch_add(1, Ordering::Relaxed);
                            self.stats
                                .connection_add_failed_staked_node
                                .fetch_add(1, Ordering::Relaxed);
                        }
                    }
```
