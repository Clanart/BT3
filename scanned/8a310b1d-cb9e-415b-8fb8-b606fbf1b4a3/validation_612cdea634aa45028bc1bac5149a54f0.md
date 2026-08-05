### Title
Stale per-connection stake snapshot lets an unstaking validator retain privileged staked QUIC/TPU connection slots and QoS after being deprioritized - (File: `streamer/src/nonblocking/quic.rs`, `streamer/src/nonblocking/swqos.rs`, `core/src/staked_nodes_updater_service.rs`)

### Summary
The Aerodrome bug lets an attacker inflate a value (LP fees) used to justify a resource already granted (deposited collateral) after the exposure-limit check has passed, and that stale/manipulable value then protects the position from being reconsidered. The same "value frozen at admission time, never re-checked while the resource stays granted" pattern exists in Agave's QUIC/TPU connection admission (`swqos.rs`/`simple_qos.rs`/`quic.rs`): a peer's stake is fetched once when the connection is accepted and baked into an immutable `ConnectionPeerType`/`ConnectionEntry`, and is never refreshed for the lifetime of that connection.

### Finding Description
When a QUIC connection is accepted, `SwQos::build_connection_context` (and the equivalent in `simple_qos.rs`) calls `get_connection_stake` once and stores the result as a fixed `ConnectionPeerType::Staked(stake)` in `SwQosConnectionContext`. This value is copied into the persisted `ConnectionEntry` for the staked connection table: [1](#0-0) 

`ConnectionEntry::stake()` simply returns the `peer_type` captured at connection-creation time — it is never re-derived from the live `staked_nodes` map. All admission-control decisions for the lifetime of the connection rely on this frozen value:

- `try_add_connection`/`cache_new_connection` use it to size the connection's `max_uni_streams` via `compute_max_allowed_uni_streams_with_rtt` [2](#0-1) 
- `prune_random` uses the frozen per-entry stake to decide which connections are protected from eviction when the staked table is full: connections whose cached stake is above the sampled `threshold_stake` are never evicted [3](#0-2) 

Meanwhile, the authoritative stake map (`StakedNodes`) used to classify *new* connections is only refreshed from the root bank every 5 seconds by a background service, and that refresh has zero effect on already-admitted connections: [4](#0-3) 

So the invariant "a peer occupies the staked connection table / gets elevated stream quota only while it is actually meaningfully staked" is enforced solely **at admission time**. Exactly like the Aerodrome LPs whose fee-inflated value is accepted once at deposit and then never re-validated against the exposure limit, once a peer's connection is admitted into the staked table with a given stake snapshot, that snapshot is used indefinitely for pruning-immunity and stream-quota purposes even if the peer's real stake later drops to near zero (undelegate, stake account closed, moved to a different validator identity, etc.). The only way such a connection is removed is normal LRU pruning (`prune_oldest`, driven by activity recency) or connection closure/errors — not a stake re-check.

### Impact Explanation
An attacker who is briefly staked can open multiple/expensive-to-evict connections into the leader's staked connection table (`max_staked_connections`), lock in a favorable `max_uni_streams` allocation and pruning-immunity, and then withdraw/redelegate their stake elsewhere. The now-effectively-unstaked connection continues consuming staked-tier capacity and bandwidth allowance that the QoS design (`SwQos`, `SimpleQos`) reserves for currently-staked, legitimate validators. This is a resource-exhaustion / QoS-bypass vector against the TPU ingestion path (explicitly in scope: QUIC/TPU, non-RPC remote exhaustion/degradation), degrading transaction/vote propagation for genuinely staked peers competing for the same fixed-size staked connection table and stream budget.

### Likelihood Explanation
Moderate. It requires the attacker to actually hold stake briefly (not merely forge it), which has cost, but stake can be delegated/undelegated relatively cheaply and connections, once admitted, persist via `last_update` activity without any re-verification of current stake, similar to how the Aerodrome LP owner never had to re-prove non-manipulated fees after depositing. The 5-second `StakedNodes` refresh cadence combined with per-connection stake caching (`ConnectionEntry`/`SwQosConnectionContext`) means there is a real, code-confirmed gap between the resource-granting event and any re-validation of the underlying entitlement.

### Recommendation
Periodically re-validate (or re-derive) the stake associated with each live `ConnectionEntry` against the current `StakedNodes` snapshot (e.g., during the existing `STAKE_REFRESH_CYCLE` tick), and demote/evict connections whose backing stake has fallen below the staked threshold, mirroring how the Arcadia fix added a "safety guard" that stops the value used for a already-granted resource from being trusted indefinitely without re-validation.

### Proof of Concept
1. Attacker delegates enough stake to be classified `ConnectionPeerType::Staked(stake)` and passes `min_stake_ratio` in `SwQos::build_connection_context`. [5](#0-4) 
2. Attacker opens one or more QUIC connections to the leader's TPU; each is admitted into `staked_connection_table` with `ConnectionEntry` capturing `peer_type = Staked(stake)` and generous `max_uni_streams`. [6](#0-5) 
3. Attacker immediately deactivates/withdraws the stake (or moves it to another identity) on-chain — the connection remains open and its `ConnectionEntry::stake()` still returns the old snapshot. [1](#0-0) 
4. When the staked table fills up, `prune_random` samples entries and compares against the *current* `threshold_stake` of a genuinely-staked incoming peer, but the attacker's stale high-stake entry is treated as still highly staked and is skipped for eviction, denying the real staked peer a slot. [3](#0-2) 
5. Repeating this (cheaply cycling small amounts of stake across many identities/connections) lets the attacker permanently occupy a disproportionate share of `max_staked_connections` and stream quota reserved for real stakers, degrading TPU throughput for the network.

Note: I was not able to trace, within the indexed portion of the codebase, any additional periodic revalidation logic elsewhere (e.g., in `staked_nodes_updater_service.rs` callers or connection-table sweep tasks) that might re-check live connections against updated stake; if such logic exists outside the indexed files, it would mitigate this finding, so this should be verified against the full repository in a Devin session before treating it as fully confirmed.

### Citations

**File:** streamer/src/nonblocking/quic.rs (L896-901)
```rust
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

**File:** streamer/src/nonblocking/swqos.rs (L301-341)
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

                SwQosConnectionContext {
                    peer_type,
                    total_stake,
                    remote_pubkey: Some(pubkey),
                    in_staked_table: false,
                    remote_address,
                    last_update: Arc::new(AtomicU64::new(timing::timestamp())),
                    stream_counter: None,
                }
            },
        )
```

**File:** core/src/staked_nodes_updater_service.rs (L16-41)
```rust
const STAKE_REFRESH_CYCLE: Duration = Duration::from_secs(5);

pub struct StakedNodesUpdaterService {
    thread_hdl: JoinHandle<()>,
}

impl StakedNodesUpdaterService {
    pub fn new(
        exit: Arc<AtomicBool>,
        bank_forks: Arc<RwLock<BankForks>>,
        staked_nodes: Arc<RwLock<StakedNodes>>,
        staked_nodes_overrides: Arc<RwLock<HashMap<Pubkey, u64>>>,
    ) -> Self {
        let thread_hdl = Builder::new()
            .name("solStakedNodeUd".to_string())
            .spawn(move || {
                while !exit.load(Ordering::Relaxed) {
                    let stakes = {
                        let root_bank = bank_forks.read().unwrap().root_bank();
                        root_bank.current_epoch_staked_nodes()
                    };
                    let overrides = staked_nodes_overrides.read().unwrap().clone();
                    *staked_nodes.write().unwrap() = StakedNodes::new(stakes, overrides);
                    std::thread::sleep(STAKE_REFRESH_CYCLE);
                }
            })
```
