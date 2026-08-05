## Title
Stake-weighted QUIC/TPU privilege ("peer type"/stake) is captured once at connection establishment and never re-validated, letting fully-unstaked identities retain elevated throughput and eviction-immunity indefinitely - ([File: streamer/src/nonblocking/swqos.rs])

### Summary
The Salty report's broken invariant is: voting weight is computed from a *live, mutable* balance instead of a snapshot, so an actor can spend the same "stake" twice by moving it to a new identity while the stale-but-still-privileged first use remains in effect. The Agave analog is in the QUIC/TPU stake-weighted QoS layer: `ConnectionPeerType`/`total_stake` for a connection is derived once via `get_connection_stake()` when the connection is accepted, and is never re-derived for the lifetime of that connection, even though the authoritative stake table (`staked_nodes`) is refreshed independently and periodically.

### Finding Description
`StakedNodesUpdaterService` refreshes the shared `staked_nodes: Arc<RwLock<StakedNodes>>` from `root_bank.current_epoch_staked_nodes()` on a fixed 5-second cycle: [1](#0-0) 

`get_connection_stake()` is the only place a connection's stake is read from that table: [2](#0-1) 

Both QoS controllers (`SwQos` and `SimpleQos`) call this function exactly once, inside `build_connection_context()`, at connection setup, and bake the resulting `peer_type`/`total_stake` into an immutable-for-the-connection `ConnectionContext`: [3](#0-2) [4](#0-3) 

That captured `peer_type`/`total_stake` is then used for the entire life of the connection to (a) compute and permanently set the max concurrent uni-streams via `connection.set_max_concurrent_uni_streams()`: [5](#0-4) 
(b) size the per-interval stream throughput budget via `available_load_capacity_in_throttling_duration`, which is explicitly proportional to the captured stake: [6](#0-5) 
and (c) protect the connection from `prune_random` eviction, since eviction only happens against the *lowest-stake* connection sampled and is skipped entirely if all sampled stakes exceed `threshold_stake`: [7](#0-6) 

`handle_connection()` runs a stream-accept loop for the connection's whole lifetime and never re-checks `peer_type`/stake against the live `staked_nodes` table; the code comment even acknowledges staleness is tolerated for RTT, and no equivalent re-validation exists for stake/peer_type at all: [8](#0-7) 

Because deriving stake is decoupled from connection lifetime, and there is no mechanism that force-closes or downgrades a connection when the underlying stake account is deactivated/unstaked to zero, a validator can establish one or more QUIC TPU connections while staked, then fully unstake, and the connections keep their originally-granted elevated `max_uni_streams`, throttling budget, and (critically) **immunity from `prune_random` eviction** for as long as the QUIC idle timeout (30s) is not hit — i.e., indefinitely, as long as the attacker keeps sending traffic to reset the idle timer. This is the direct analog of Salty's "vote power computed from a value that can be moved away after being counted": the entitlement (elevated stake-weighted QoS) is never revoked even though its underlying justification (stake) is withdrawn.

### Impact Explanation
This breaks the core assumption of Agave's stake-weighted QoS design — that TPU/QUIC bandwidth and connection-table slots are proportional to *current* stake, protecting the network from unstaked/low-stake spam while guaranteeing capacity to legitimately staked validators. With this gap:
- An attacker who briefly stakes (even a modest amount, enough to clear `MIN_STAKE_FOR_GOSSIP`-style low-stake filtering thresholds or the `min_stake_ratio` gate in `build_connection_context`), opens `max_connections_per_staked_peer` connections, and unstakes, permanently occupies staked connection-table slots that cannot be pruned (since `prune_random` compares against the frozen high `stake` value, not the current, now-zero stake).
- Occupied slots reduce/starve capacity available to real, currently-staked validators once `max_staked_connections` is reached, degrading transaction ingestion for legitimate high-stake nodes.
- The attacker's connections also retain outsized `max_uni_streams` / throttling allowance computed from the stale stake, letting a now-zero-stake identity consume TPU stream bandwidth disproportionate to its actual (zero) stake.
This falls under "non-RPC remote exhaustion/crash" of a validator's TPU/QUIC ingestion path caused by an unprivileged actor (anyone who can transiently acquire and then remove stake), not requiring a malicious peer/validator/trusted assumption.

### Likelihood Explanation
Medium. It does not require any signature forgery, consensus bug, or privileged access — only the ability to (1) delegate stake temporarily, (2) open QUIC TPU connections while staked, and (3) unstake while keeping the connections alive with periodic traffic (well within reach of any staker, and stake deactivation/cooldown does not close existing sockets). The main mitigating factor is that acquiring stake at all has an economic cost and that connections still eventually close on idle timeout or restart, so the attack requires sustained activity rather than a single one-shot exploit; likelihood is thus real but bounded by the cost of holding transient stake and maintaining open connections.

### Recommendation
- Periodically re-evaluate each open connection's `peer_type`/stake against the live `staked_nodes` table (e.g., on the same cadence as `StakedNodesUpdaterService`), and downgrade/close connections whose backing stake has fallen below the threshold used at admission time.
- Make `prune_random`'s eviction threshold check use freshly looked-up stake rather than the value captured at connection creation.
- Alternatively, bound the lifetime of the elevated grant (e.g., re-derive `max_uni_streams`/throttling budget at fixed intervals per connection, similar to periodic RTT/EMA refresh) so that stale privilege cannot persist indefinitely.

### Proof of Concept
Conceptual (cannot be executed without a live cluster/test harness in this session):
1. Attacker delegates stake sufficient to pass the `min_stake_ratio` gate in `SwQos::build_connection_context` (streamer/src/nonblocking/swqos.rs:318-329) and wait for it to be reflected in `current_epoch_staked_nodes()`.
2. Attacker opens `max_connections_per_staked_peer` QUIC connections to a validator's TPU; `build_connection_context` captures `peer_type = Staked(stake)`/`total_stake`, and `cache_new_connection` sets an elevated `max_uni_streams` (swqos.rs:181-232) and inserts entries into the staked `ConnectionTable`.
3. Attacker deactivates/unstakes fully. After one epoch (or the `StakedNodesUpdaterService` 5s refresh for the underlying table), `staked_nodes.get_node_stake(&attacker_pubkey)` returns `None`/0 for any *new* lookup.
4. Attacker keeps the already-open connections alive by sending sparse traffic (below the 30s idle timeout in `quic.rs:36-38`). These connections retain their original `ConnectionEntry`/`peer_type`/`total_stake` and continue to (a) enjoy the originally granted `max_uni_streams` and throttling budget, and (b) be immune from `prune_random(sample_size, threshold_stake)` (quic.rs:982-1006) since the recorded stake used for comparison is still the old high value.
5. When other, currently-staked validators attempt to connect and `max_staked_connections` is reached, `prune_random` fails to evict the attacker's now-zero-stake connections, denying slots to legitimate stakers.

This cannot be fully validated end-to-end without running the streamer integration tests in a live/test harness (e.g., extending `streamer/src/nonblocking/simple_qos.rs`'s existing `test_try_add_connection_max_staked_connections_no_pruning_possible`-style tests to simulate a stake reduction after connection admission), which was outside the scope of the read-only code search performed here.

### Citations

**File:** core/src/staked_nodes_updater_service.rs (L16-39)
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

**File:** streamer/src/nonblocking/quic.rs (L583-639)
```rust
async fn handle_connection<Q, C>(
    packet_sender: Sender<PacketBatch>,
    remote_address: SocketAddr,
    connection: Connection,
    stats: Arc<StreamerStats>,
    wait_for_chunk_timeout: Duration,
    max_stream_data_bytes: u32,
    context: C,
    qos: Arc<Q>,
    cancel: CancellationToken,
) where
    Q: QosController<C> + Send + Sync + 'static,
    C: ConnectionContext + Send + Sync + 'static,
{
    let peer_type = context.peer_type();
    debug!(
        "quic new connection {} streams: {} connections: {}",
        remote_address,
        stats.active_streams.load(Ordering::Relaxed),
        stats.total_connections.load(Ordering::Relaxed),
    );
    stats.total_connections.fetch_add(1, Ordering::Relaxed);

    // cache the RTT to avoid grabbing lock for every stream.
    // we only use that for some stats here, so if it gets stale during connection lifetime
    // it is not the end of the world.
    let rtt = connection.rtt();
    'conn: loop {
        // Wait for new streams. If the peer is disconnected we get a cancellation signal and stop
        // the connection task.
        let mut stream = select! {
            stream = connection.accept_uni() => match stream {
                Ok(stream) => stream,
                Err(e) => {
                    debug!("stream error: {e:?}");
                    break;
                }
            },
            _ = cancel.cancelled() => break,
        };

        qos.on_new_stream(&context).await;
        qos.on_stream_accepted(&context);
        stats.active_streams.fetch_add(1, Ordering::Relaxed);
        stats.total_new_streams.fetch_add(1, Ordering::Relaxed);

        let mut meta = Meta::default();
        meta.set_socket_addr(&remote_address);
        meta.set_from_staked_node(matches!(peer_type, ConnectionPeerType::Staked(_)));
        if let Some(pubkey) = context.remote_pubkey() {
            meta.set_remote_pubkey(pubkey);
        }

        let mut accum = PacketAccumulator::new(meta);
        // Virtually all small transactions will fit in 1 chunk. Larger transactions will fit in 1
        // or 2 chunks if the first chunk starts towards the end of a datagram. A small number of
        // transaction will have other protocol frames inserted in the middle. Empirically it's been
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

**File:** streamer/src/nonblocking/swqos.rs (L181-232)
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
```

**File:** streamer/src/nonblocking/swqos.rs (L301-342)
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
    }
```

**File:** streamer/src/nonblocking/simple_qos.rs (L256-273)
```rust
impl QosController<SimpleQosConnectionContext> for SimpleQos {
    fn build_connection_context(&self, connection: &Connection) -> SimpleQosConnectionContext {
        let (peer_type, remote_pubkey, _total_stake) =
            get_connection_stake(connection, &self.staked_nodes).map_or(
                (ConnectionPeerType::Unstaked, None, 0),
                |(pubkey, stake, total_stake)| {
                    (ConnectionPeerType::Staked(stake), Some(pubkey), total_stake)
                },
            );

        SimpleQosConnectionContext {
            peer_type,
            remote_pubkey,
            remote_address: connection.remote_address(),
            last_update: Arc::new(AtomicU64::new(timing::timestamp())),
            stream_counter: None,
        }
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
