Audit Report

## Title
`SwQos`, the stake-weighted QUIC QoS controller wired to the TPU/TPU-forward sockets, never enforces the connection `Banlist`, letting banned peers keep consuming TPU connection/stream capacity - ([File: streamer/src/nonblocking/swqos.rs])

## Summary
`solana_net_utils::banlist::Banlist` is meant to exclude a flagged pubkey from further service, and `SimpleQos::try_add_connection` correctly checks `is_banned` and rejects banned peers with `CONNECTION_CLOSE_CODE_DISALLOWED`. `SwQos`, the controller actually spawned for the TPU and TPU-forward QUIC listeners via `spawn_stake_weighted_qos_server` in `Tpu::new`, has no `Banlist` field and its `build_connection_context`/`try_add_connection` never perform any ban check, so a banned pubkey can keep opening staked/unstaked connections and consuming stream/connection quota on the primary transaction-ingestion path.

## Finding Description
`SimpleQos` holds an `Arc<Banlist<Pubkey>>` (`SimpleQosBanlist`) and, in `try_add_connection`, checks `self.banlist.is_banned(&remote_pubkey)` before admitting a connection, closing it with `CONNECTION_CLOSE_CODE_DISALLOWED` if banned. [1](#0-0) [2](#0-1) 

`SwQos`'s struct contains only `config`, `staked_stream_load_ema`, `stats`, `staked_nodes`, `unstaked_connection_table`, and `staked_connection_table` — no banlist reference at all. [3](#0-2) 

Its `try_add_connection` implementation classifies the peer via `peer_type()` and goes straight into staked/unstaked connection-table admission (pruning, adding, or falling back to the unstaked table), with no `is_banned` call or equivalent exclusion check anywhere in the flow. [4](#0-3) 

Both `SimpleQos` and `SwQos` are constructed in `streamer/src/quic.rs`, confirming they are two independent, non-overlapping `QosController` implementations rather than one being layered on top of the other. [5](#0-4) 

`SwQos` (via `spawn_stake_weighted_qos_server`) is the concrete controller spawned for both the primary TPU QUIC listener and the TPU-forward QUIC listener in `Tpu::new`. [6](#0-5) 

Since `SwQos` performs no banlist lookup at any point in its connection-admission path, a ban issued for a pubkey has no effect on new connections accepted through the TPU/TPU-forward sockets, which is exactly the code path handling the bulk of live transaction traffic.

## Impact Explanation
This falls within the valid QUIC/TPU non-RPC remote exhaustion/degradation category. A peer that has been banned (e.g., due to detected protocol abuse) is expected to lose further TPU access, but because the actually-deployed `SwQos` controller performs no ban check, that peer can keep opening staked/unstaked QUIC connections and occupying `staked_connection_table`/`unstaked_connection_table` slots and stream throughput indefinitely. This degrades QoS/availability for legitimate, higher-priority stake-weighted senders on the transaction-ingestion path, since capacity meant to be freed by banning is never actually freed.

## Likelihood Explanation
No special privileges are needed beyond being a QUIC peer of the validator, which is the normal, unprivileged mode of interacting with the TPU. Since `SwQos` is the default controller wired into `Tpu::new` for both TPU and TPU-forward endpoints, any ban issued while that controller is active is silently ineffective for new connection attempts — an attacker only needs to reconnect after being banned to keep consuming capacity, making the exploit trivially repeatable.

## Recommendation
Add a banlist check (mirroring `SimpleQos`'s `is_banned` check) into `SwQos::try_add_connection` and/or `build_connection_context`, ideally by sharing a single `Banlist<Pubkey>` instance across both `QosController` implementations so a ban has uniform effect regardless of which QoS controller/socket is active. Add integration tests spawning `SwQos`-backed servers that assert a banned pubkey is rejected, matching the existing `SimpleQos` ban coverage.

## Proof of Concept
1. Run a validator with the default TPU/TPU-forward wiring (`Tpu::new` → `spawn_stake_weighted_qos_server`, using `SwQos`).
2. Insert an attacker pubkey into the shared `Banlist` via whatever mechanism triggers `Banlist::ban()` (the same condition `SimpleQos`'s eviction task reacts to).
3. From the banned keypair, open a new QUIC connection to the TPU or TPU-forward port.
4. Observe `SwQos::build_connection_context`/`try_add_connection` admit the connection to the staked or unstaked connection table without any `is_banned` check, unlike `SimpleQos::try_add_connection`, which would reject the same pubkey immediately.

### Citations

**File:** streamer/src/nonblocking/simple_qos.rs (L49-64)
```rust
pub struct SimpleQosBanlist {
    banlist: Arc<Banlist<Pubkey>>,
    eviction_sender: Sender<Pubkey>,
}

impl SimpleQosBanlist {
    pub fn new() -> (Self, Receiver<Pubkey>) {
        let (eviction_sender, eviction_receiver) = channel(MAX_IN_FLIGHT_EVICTIONS);
        (
            Self {
                banlist: Arc::new(Banlist::default()),
                eviction_sender,
            },
            eviction_receiver,
        )
    }
```

**File:** streamer/src/nonblocking/simple_qos.rs (L287-308)
```rust
    #[allow(clippy::manual_async_fn)]
    fn try_add_connection(
        &self,
        client_connection_tracker: ClientConnectionTracker,
        connection: &quinn::Connection,
        conn_context: &mut SimpleQosConnectionContext,
    ) -> impl Future<Output = Option<CancellationToken>> + Send {
        async move {
            const PRUNE_RANDOM_SAMPLE_SIZE: usize = 2;
            let remote_pubkey = conn_context.remote_pubkey()?;
            if self.banlist.is_banned(&remote_pubkey) {
                let remote_address = conn_context.remote_address;
                info!("Rejecting banned pubkey {remote_pubkey} from {remote_address:?}");
                self.stats
                    .connection_add_failed_banned
                    .fetch_add(1, Ordering::Relaxed);
                connection.close(
                    CONNECTION_CLOSE_CODE_DISALLOWED.into(),
                    CONNECTION_CLOSE_REASON_DISALLOWED,
                );
                return None;
            }
```

**File:** streamer/src/nonblocking/swqos.rs (L88-95)
```rust
pub struct SwQos {
    config: SwQosConfig,
    staked_stream_load_ema: Arc<StakedStreamLoadEMA>,
    stats: Arc<StreamerStats>,
    staked_nodes: Arc<RwLock<StakedNodes>>,
    unstaked_connection_table: Arc<Mutex<ConnectionTable<ConnectionStreamCounter>>>,
    staked_connection_table: Arc<Mutex<ConnectionTable<ConnectionStreamCounter>>>,
}
```

**File:** streamer/src/nonblocking/swqos.rs (L301-443)
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

    #[allow(clippy::manual_async_fn)]
    fn try_add_connection(
        &self,
        client_connection_tracker: ClientConnectionTracker,
        connection: &quinn::Connection,
        conn_context: &mut SwQosConnectionContext,
    ) -> impl Future<Output = Option<CancellationToken>> + Send {
        async move {
            const PRUNE_RANDOM_SAMPLE_SIZE: usize = 2;

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
                }
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
                }
            }

            None
        }
    }
```

**File:** streamer/src/quic.rs (L1-8)
```rust
use {
    crate::{
        nonblocking::{
            qos::{ConnectionContext, QosController},
            quic::{ALPN_TPU_PROTOCOL_ID, DEFAULT_WAIT_FOR_CHUNK_TIMEOUT},
            simple_qos::{SimpleQos, SimpleQosBanlist, SimpleQosConfig},
            swqos::{SwQos, SwQosConfig},
        },
```

**File:** core/src/tpu.rs (L236-274)
```rust
        let transactions_quic_sockets =
            into_quic_sockets(transactions_quic_sockets, quic_xdp_sender.clone());
        let SpawnServerResult {
            endpoints: _,
            thread: tpu_quic_t,
            key_updater,
        } = spawn_stake_weighted_qos_server(
            "solQuicTpu",
            "quic_streamer_tpu",
            transactions_quic_sockets,
            keypair,
            packet_sender,
            staked_nodes.clone(),
            tpu_quic_server_config.quic_streamer_config,
            tpu_quic_server_config.qos_config,
            cancel.clone(),
        )
        .unwrap();

        // Streamer for TPU forward
        let transactions_forwards_quic_sockets =
            into_quic_sockets(transactions_forwards_quic_sockets, quic_xdp_sender);
        let SpawnServerResult {
            endpoints: _,
            thread: tpu_forwards_quic_t,
            key_updater: forwards_key_updater,
        } = spawn_stake_weighted_qos_server(
            "solQuicTpuFwd",
            "quic_streamer_tpu_forwards",
            transactions_forwards_quic_sockets,
            keypair,
            forwarded_packet_sender,
            staked_nodes.clone(),
            tpu_fwd_quic_server_config.quic_streamer_config,
            tpu_fwd_quic_server_config.qos_config,
            cancel,
        )
        .unwrap();

```
