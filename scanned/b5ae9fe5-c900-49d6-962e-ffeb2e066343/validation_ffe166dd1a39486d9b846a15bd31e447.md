## Title
Staked connection eviction is committed before verifying the new connection can actually be added, allowing a staked peer to permanently drain the QUIC/TPU staked-connection table - ([File: streamer/src/nonblocking/swqos.rs])

## Summary
This is a structural analog of the reported `WhitelistRegistry.register()` bug: an "evict-the-minimum-then-insert" pattern where the eviction is performed unconditionally while the subsequent insertion can independently fail, so the removal is never rolled back. In Agave's QUIC connection admission logic (`SwQos::try_add_connection` / `SimpleQos::try_add_connection`), when the staked connection table is full, `ConnectionTable::prune_random` unconditionally evicts a lower-stake connection *before* the code checks whether the caller's own per-peer connection limit (`max_connections_per_peer`) will even allow the new connection to be inserted. Any staked peer that has already saturated its own per-peer connection slot can keep opening throwaway connections; each attempt still triggers a real eviction of some other (lower-stake) staked peer's connection via `prune_random`, even though the attacker's own connection is guaranteed to be rejected afterward by `try_add_connection`'s per-peer cap check. This permanently shrinks the effective population of the staked connection table without the attacker ever occupying the freed slot.

## Finding Description
`ConnectionTable::try_add_connection` in [1](#0-0)  enforces a per-key (`ConnectionTableKey`, i.e. IP or IP+pubkey) `max_connections_per_peer` cap and rejects the connection (returning `None`, closing the QUIC connection with `CONNECTION_CLOSE_CODE_TOO_MANY`) if that cap is already reached.

`ConnectionTable::prune_random` in [2](#0-1)  unconditionally samples random entries and evicts the lowest-stake one if it is below the caller-supplied `threshold_stake` (the connecting peer's own stake) — this removal is committed to `self.table` immediately, independent of anything about the caller's ability to actually add a new connection.

Both QoS controllers use this pattern the same way, e.g. `SwQos::try_add_connection`: [3](#0-2)  and `SimpleQos::try_add_connection`: [4](#0-3) . The control flow is:
1. If `connection_table_l.total_size >= max_staked_connections`, call `prune_random` — this evicts (removes) a lower-stake connection from `self.table` right away.
2. Only *after* that eviction has already happened does the code check `connection_table_l.total_size < max_staked_connections` and call `cache_new_connection` → `try_add_connection`, which independently enforces the unrelated per-peer connection cap on the *connecting key* (see `has_connection_capacity` check at [5](#0-4) ).

There is no check, before pruning, of whether the connecting key already has room under `max_connections_per_peer`, and no rollback of the eviction if `try_add_connection` subsequently fails. This exactly mirrors the whitelist bug's broken invariant: "remove-the-minimum" is performed speculatively and is not conditioned on, nor reverted by, the success of the compensating "add".

## Impact Explanation
A staked peer (any node holding stake — not a privileged/trusted role, and not requiring a leaked key or malicious-validator assumption; any staked keypair used to open a QUIC connection to the TPU/TPU-forward port qualifies) that first fills its own per-peer connection slots (`max_connections_per_peer` / `max_connections_per_staked_peer`) can then keep issuing additional QUIC connection attempts from the same key. Each such attempt:
- passes the outer `total_size >= max_staked_connections` check (since the table is at/over capacity from legitimate staked traffic),
- causes `prune_random` to evict some other lower-stake staked peer's connection (as long as attacker's stake exceeds the minimum of the 2-entry random sample, `PRUNE_RANDOM_SAMPLE_SIZE`),
- then fails at `try_add_connection` due to the attacker's own per-peer cap, closing the just-opened connection with `CONNECTION_CLOSE_CODE_TOO_MANY`.

Net effect per attempt: one legitimate lower-stake staked validator's TPU connection is evicted, and the attacker gains nothing (no new connection persists), but the table's effective staked population is reduced by one. Repeating this drains real, legitimate staked connections from the table, degrading TPU connectivity/availability for lower-stake validators — a non-RPC, unprivileged remote degradation/exhaustion vector against the QUIC/TPU ingestion path, without requiring the attacker to be a validator authority, admin, or trusted process.

## Likelihood Explanation
The attack requires only: (a) being a staked node able to open QUIC connections to the TPU/TPU-forward endpoint (a normal, unprivileged capability derivable from the bank's stake table), and (b) enough stake to exceed the minimum of a 2-sample random draw from the staked table — not necessarily high stake, since the sample size is small (`PRUNE_RANDOM_SAMPLE_SIZE = 2`). The attacker does not need elevated privileges, a leaked key, or cooperation from another validator; opening connections up to and beyond `max_connections_per_peer` is entirely within an unprivileged client's control. This makes the condition easy to trigger repeatedly and cheaply.

## Recommendation
Reorder the checks so that eviction is only committed if the new connection is actually guaranteed to be insertable, or make the two operations atomic/reversible:
- Before calling `prune_random`, first verify that the connecting `ConnectionTableKey` has spare capacity under `max_connections_per_peer` (mirroring the `has_connection_capacity` check inside `try_add_connection`).
- Alternatively, restructure `try_add_connection`/`cache_new_connection` so pruning and insertion happen as a single guarded operation, and roll back (or refuse) the pruning if the subsequent insert fails, analogous to checking the return value of `AddressSet.add()` in the original Solidity fix.

## Proof of Concept
1. Attacker controls a staked keypair `K` with stake `s`.
2. Attacker opens `max_connections_per_staked_peer` QUIC connections from key `K` to the validator's TPU port, filling `K`'s per-peer slot in `staked_connection_table`.
3. Attacker opens one more QUIC connection from `K`. Assuming `staked_connection_table.total_size >= max_staked_connections` (true whenever the table is near/at capacity from ordinary cluster traffic), `SwQos::try_add_connection` calls `connection_table_l.prune_random(2, s)` ( [6](#0-5) ), which evicts some other staked peer's connection whose stake is below `s`.
4. `cache_new_connection` → `ConnectionTable::try_add_connection` then rejects the new connection for `K` because `has_connection_capacity` is `false` (`K` is already at its per-peer cap) ( [5](#0-4) ); the connection is closed with `CONNECTION_CLOSE_CODE_TOO_MANY`.
5. Repeat step 3 indefinitely: each cycle removes one legitimate lower-stake staked connection from the table with no compensating addition, progressively degrading TPU availability for lower-stake staked validators.

### Citations

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

**File:** streamer/src/nonblocking/swqos.rs (L354-384)
```rust
            match conn_context.peer_type() {
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
```

**File:** streamer/src/nonblocking/simple_qos.rs (L310-345)
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
```
