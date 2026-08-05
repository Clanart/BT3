## Answer

The finding is valid: `SimpleQos::try_add_connection` in `streamer/src/nonblocking/simple_qos.rs` prunes and updates eviction stats **unconditionally**, before it is known whether the new connection will actually be admitted.

### Finding Description

In `try_add_connection` for a `ConnectionPeerType::Staked(stake)` peer:

```rust
if connection_table_l.total_size >= self.config.max_staked_connections {
    let num_pruned = connection_table_l.prune_random(PRUNE_RANDOM_SAMPLE_SIZE, stake);
    ...
    self.stats.num_evictions_staked.fetch_add(num_pruned, Ordering::Relaxed);
    update_open_connections_stat(&self.stats, &connection_table_l);
}

if connection_table_l.total_size < self.config.max_staked_connections
    && let Ok((last_update, cancel_connection, stream_counter)) = self
        .cache_new_connection(client_connection_tracker, connection, connection_table_l, conn_context)
{
    ...
    return Some(cancel_connection);
}
None
``` [1](#0-0) 

`prune_random` evicts an entry chosen at random from a sample of `PRUNE_RANDOM_SAMPLE_SIZE` entries in the table — the lowest-stake entry among the sample, as long as its stake is below the attacker's own claimed `stake` — and this eviction is entirely independent of the attacker's own key: [2](#0-1) 

`num_pruned` is added to `stats.num_evictions_staked` immediately, before `cache_new_connection` is even attempted. Only *after* this side effect does the code check `total_size < max_staked_connections` and attempt `cache_new_connection`, which internally calls `ConnectionTable::try_add_connection`. That function independently checks the attacker's own per-key capacity:

```rust
let connection_entry = self.table.entry(key).or_default();
let has_connection_capacity = connection_entry.len().checked_add(1).map(|c| c <= max_connections_per_peer).unwrap_or(false);
if has_connection_capacity { ... } else { ... None }
``` [3](#0-2) 

`key` is derived from `remote_addr.ip()` + `conn_context.remote_pubkey`, i.e. the attacker's own identity — not the identity of the peer being pruned. Thus if the attacker's own key already has `max_connections_per_peer` entries in the staked table (a limit entirely under the attacker's control, since they can open that many concurrent QUIC connections themselves), `cache_new_connection` fails via `ConnectionHandlerError::ConnectionAddError`, and `try_add_connection` returns `None` — but the eviction of an unrelated victim connection (chosen by `prune_random`, which does not exclude the attacker's own entries or otherwise correlate with the entry that will actually be inserted) has already happened and already been counted in `num_evictions_staked`.

The existing unit test `test_try_add_connection_max_staked_connections_no_pruning_possible` only covers the case where pruning *itself* fails (all sampled stakes ≥ threshold); it does not cover the scenario where pruning **succeeds** but the subsequent `cache_new_connection` fails for a reason unrelated to pruning outcome (own per-peer connection limit reached) — the exact gap in coverage the question is asking about. [4](#0-3) [5](#0-4) 

### Impact Explanation

A staked attacker can repeatedly open new QUIC connections against the staked connection table once their own per-key slot count is saturated. Each such attempt causes the table's occupancy check (`total_size >= max_staked_connections`) to trigger, and `prune_random` evicts one connection belonging to an arbitrary other peer whose sampled stake is below the attacker's — even though the attacker's own connection will then be rejected for hitting its own `max_connections_per_peer` limit. This produces one-sided churn: legitimate staked peers' connections are torn down (forcing them to reconnect/re-authenticate, losing in-flight streams) while the attacker gains nothing and can repeat the attack indefinitely at low cost, since opening/closing QUIC connections is cheap relative to the disruption caused to victims.

### Likelihood Explanation

This requires only a staked (not necessarily highly staked) attacker able to establish `max_connections_per_peer` connections under one key (a self-imposed, attacker-controlled condition, e.g. `DEFAULT_MAX_QUIC_CONNECTIONS_PER_STAKED_PEER = 16` [6](#0-5) ) and then continue issuing new connection attempts once at that limit. No special privilege or race condition is needed; it's a straightforward sequencing bug in the eviction-then-insert flow of `try_add_connection`.

### Recommendation

Defer accounting `num_evictions_staked` (and the actual table pruning, if feasible) until after confirming `cache_new_connection` will succeed for the new peer — e.g., check the new peer's own `has_connection_capacity` before performing `prune_random`, or roll back/avoid the prune if the subsequent insert is known to fail regardless of table space (already-at-own-limit case).

### Proof of Concept

Extend `test_try_add_connection_max_staked_connections_with_pruning` (`streamer/src/nonblocking/simple_qos.rs:906-974`) so that after the first connection is added, configure `max_connections_per_peer: 1` for the `simple_qos`/table, and have the "attacker" (`server_keypair2`/`client_keypair2`) itself already occupy that 1 slot in the staked table via a prior successful `try_add_connection` call. Then attempt a further `try_add_connection` from the same attacker key: assert that `result.is_none()` (own `cache_new_connection` fails due to per-peer capacity) while `stats.num_evictions_staked.load(Ordering::Relaxed)` still increased and the victim connection (`server_keypair1`) was removed from `staked_connection_table`, demonstrating eviction occurs independent of the attacker's own insertion success.

### Citations

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

**File:** streamer/src/nonblocking/simple_qos.rs (L953-974)
```rust
        assert!(result1.is_some()); // First connection should succeed

        // Try to add second connection (should trigger pruning)
        let client_tracker2 = ClientConnectionTracker {
            stats: stats.clone(),
        };

        let (server_connection2, _client_endpoint2, _server_endpoint2) =
            create_connection_with_keypairs(&server_keypair2, &client_keypair2).await;

        let mut conn_context2 = simple_qos.build_connection_context(&server_connection2);

        let result2 = simple_qos
            .try_add_connection(client_tracker2, &server_connection2, &mut conn_context2)
            .await;

        // Verify second connection succeeded (after pruning)
        assert!(result2.is_some());

        // Verify pruning stats were updated
        assert!(stats.num_evictions_staked.load(Ordering::Relaxed) > 0);
    }
```

**File:** streamer/src/nonblocking/simple_qos.rs (L1035-1044)
```rust
        let result2 = simple_qos
            .try_add_connection(client_tracker2, &server_connection2, &mut conn_context2)
            .await;

        // Verify second connection failed (couldn't prune higher stake)
        assert!(result2.is_none());

        // Verify context was not updated
        assert!(conn_context2.stream_counter.is_none());
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

**File:** streamer/src/nonblocking/quic.rs (L1019-1050)
```rust
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

**File:** streamer/src/quic.rs (L43-44)
```rust
// allow multiple connections per ID for geo-distributed forwarders
pub const DEFAULT_MAX_QUIC_CONNECTIONS_PER_STAKED_PEER: usize = 16;
```
