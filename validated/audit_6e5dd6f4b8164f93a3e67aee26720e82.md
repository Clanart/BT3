Based on my research, I found a genuine analog: Agave implements two parallel, structurally similar QUIC connection-admission systems — `SimpleQos` (`streamer/src/nonblocking/simple_qos.rs`) and `SwQos` (`streamer/src/nonblocking/swqos.rs`) — that mirror the report's "two similar functions handled inconsistently" pattern. Only `SimpleQos` enforces a pubkey banlist check before admitting a connection; `SwQos` has no equivalent check at all.

### Title
Missing banlist enforcement in `SwQos` connection admission allows banned/invalid-vote senders to keep flooding the validator - ([File: streamer/src/nonblocking/swqos.rs])

### Summary
`SimpleQos::try_add_connection` explicitly checks `self.banlist.is_banned(&remote_pubkey)` and rejects/closes the connection before any admission logic runs [1](#0-0) . The BLS sigverifier bans senders of invalid certificates/votes for 48 hours using this same `SimpleQosBanlist` type [2](#0-1) . However, `SwQos::try_add_connection`, which implements the same `try_add_connection` admission contract, contains no banlist field, no `is_banned` check, and no equivalent enforcement point [3](#0-2) [4](#0-3) .

### Finding Description
This is a direct structural analog of the reported issue: two functions/modules implementing "similar functionality" (connection admission/quality-of-service control for QUIC) are handled inconsistently. In the Solidity report, `VaultBlocklist` exposes a proper `_checkBlocklist()` enforcement path while `VaultWhitelist` requires callers to check state directly — an inconsistency that risks a caller forgetting to enforce the check. Here, the analogous risk is realized: the ban-check enforcement that exists in one QoS backend (`SimpleQos`) is silently absent from the other backend (`SwQos`) that implements the identical `try_add_connection`/`ConnectionContext` interface [5](#0-4) . A pubkey banned via `SimpleQosBanlist::ban()` (e.g., for sending invalid BLS certificates/votes) is only rejected if the validator is running the `SimpleQos` admission path; if `SwQos` is the active QoS backend, the ban list is never consulted, so the banned peer's connections are re-admitted and streams processed normally.

### Impact Explanation
The broken invariant is "a banned pubkey must not be able to open new connections/streams to the TPU/vote QUIC endpoint." Because `SwQos` never queries the banlist, an attacker whose pubkey has already been banned for misbehavior (invalid BLS votes/certs) can continue opening staked connections and consuming the staked connection table / stream budget through the `SwQos` path, defeating the purpose of the ban and enabling continued remote resource exhaustion of a single validator's QUIC ingestion pipeline (non-RPC remote crash/degradation surface).

### Likelihood Explanation
Likelihood is high wherever `SwQos` is the configured/compiled admission backend, since no additional guard exists anywhere else in `SwQos::try_add_connection` (staked-connection pruning and unstaked handling are the only gates) [6](#0-5) . No malicious-peer trust assumption beyond an already-observed misbehaving pubkey is required — this is exactly the unprivileged scenario the ban mechanism is meant to stop.

### Recommendation
Give `SwQos` (and any other `QosController` implementation) the same `SimpleQosBanlist` field and enforce `is_banned()` at the top of `try_add_connection`, mirroring `SimpleQos`'s check [1](#0-0) , or centralize the ban check in a shared helper/trait method so both QoS backends cannot diverge in the future.

### Proof of Concept
1. Configure/compile the validator with the `SwQos` admission backend for QUIC (as selected via `QosController`/`qos_config` wiring in `core/src/tpu.rs`/`core/src/validator.rs`).
2. Have an attacker-controlled staked pubkey send an invalid BLS certificate or vote, triggering `bls-sigverify`'s 48-hour ban via `SimpleQosBanlist::ban()` [7](#0-6) .
3. From the same pubkey, reopen a QUIC connection to the validator's TPU/vote endpoint that is served by `SwQos::try_add_connection` [8](#0-7) .
4. Observe the connection is admitted into the staked connection table and streams are processed — no `is_banned` check exists in this path — unlike the equivalent test for `SimpleQos` (`test_simple_qos_banned_pubkey_rejected_across_source_ip`) which asserts rejection [9](#0-8) .

Note: I was unable to fully confirm at which point/config flag `SwQos` vs `SimpleQos` is selected as the live production QUIC backend (the search only surfaced references in `core/src/tpu.rs`, `core/src/validator.rs`, and `validator/src/commands/run/execute.rs`, which I could not inspect in this final iteration). If `SwQos` turns out to be dead code or gated behind a feature flag not reachable in production, the practical severity would be reduced accordingly — this should be verified before treating the finding as fully confirmed.

### Citations

**File:** streamer/src/nonblocking/simple_qos.rs (L294-308)
```rust
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

**File:** bls-sigverify/src/bls_sigverifier.rs (L34-59)
```rust
    solana_streamer::{nonblocking::simple_qos::SimpleQosBanlist, packet::PacketBatch},
    std::{
        cmp,
        collections::{HashMap, HashSet, hash_map::Entry},
        sync::{
            Arc,
            atomic::{AtomicBool, Ordering},
        },
        thread::{self, Builder},
        time::Duration,
    },
};

/// If a cert or vote is so many slots in the future relative to the root slot, it is considered
/// invalid and discarded.
///
/// This also sets an upper bound on how much storage the various structs in this module require.
///
/// At 200ms slot times, 30K slots is 100mins.  We do not expect a node to catch up if it has
/// fallen so far behind.
pub(super) const NUM_SLOTS_FOR_VERIFY: Slot = 30_000;

/// If we receive an invalid certificate or vote, we ban its attributed sender. For certificates
/// received from blockstore, that sender is the scheduled leader for the carrier slot. We ban the
/// sender for 2 days, which roughly corresponds to an epoch.
pub(super) const BAN_TIMEOUT: Duration = Duration::from_hours(48);
```

**File:** streamer/src/nonblocking/swqos.rs (L88-145)
```rust
pub struct SwQos {
    config: SwQosConfig,
    staked_stream_load_ema: Arc<StakedStreamLoadEMA>,
    stats: Arc<StreamerStats>,
    staked_nodes: Arc<RwLock<StakedNodes>>,
    unstaked_connection_table: Arc<Mutex<ConnectionTable<ConnectionStreamCounter>>>,
    staked_connection_table: Arc<Mutex<ConnectionTable<ConnectionStreamCounter>>>,
}

// QoS Params for Stake weighted QoS
#[derive(Clone)]
pub struct SwQosConnectionContext {
    peer_type: ConnectionPeerType,
    remote_pubkey: Option<solana_pubkey::Pubkey>,
    total_stake: u64,
    in_staked_table: bool,
    last_update: Arc<AtomicU64>,
    remote_address: std::net::SocketAddr,
    stream_counter: Option<Arc<ConnectionStreamCounter>>,
}

impl ConnectionContext for SwQosConnectionContext {
    fn peer_type(&self) -> ConnectionPeerType {
        self.peer_type
    }

    fn remote_pubkey(&self) -> Option<solana_pubkey::Pubkey> {
        self.remote_pubkey
    }
}

impl SwQos {
    pub fn new(
        config: SwQosConfig,
        stats: Arc<StreamerStats>,
        staked_nodes: Arc<RwLock<StakedNodes>>,
        cancel: CancellationToken,
    ) -> Self {
        Self {
            config: config.clone(),
            staked_stream_load_ema: Arc::new(StakedStreamLoadEMA::new(
                stats.clone(),
                config.max_unstaked_connections,
                config.max_streams_per_ms,
            )),
            stats,
            staked_nodes,
            unstaked_connection_table: Arc::new(Mutex::new(ConnectionTable::new(
                ConnectionTableType::Unstaked,
                cancel.clone(),
            ))),
            staked_connection_table: Arc::new(Mutex::new(ConnectionTable::new(
                ConnectionTableType::Staked,
                cancel,
            ))),
        }
    }
}
```

**File:** streamer/src/nonblocking/swqos.rs (L345-443)
```rust
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

**File:** streamer/src/quic.rs (L937-1035)
```rust
    #[test]
    fn test_simple_qos_banned_pubkey_rejected_across_source_ip() {
        agave_logger::setup();
        let client_keypair = Keypair::new();
        let staked_nodes = Arc::new(RwLock::new(StakedNodes::new(
            Arc::new(HashMap::from([(client_keypair.pubkey(), 1_000)])),
            HashMap::<Pubkey, u64>::default(),
        )));

        let server_params = SimpleQosQuicStreamerConfig {
            quic_streamer_config: QuicStreamerConfig::default_for_tests(),
            qos_config: SimpleQosConfig {
                max_connections_per_peer: 2,
                max_streams_per_second: 20,
                ..Default::default()
            },
        };
        let (t, receiver, server_address, cancel, banlist) =
            setup_simple_qos_quic_server(server_params, staked_nodes);

        let runtime = rt_for_test();
        runtime.block_on(async move {
            let wait_for_packet = || async {
                let start = Instant::now();
                while start.elapsed().as_secs() < 3 {
                    if let Ok(packet_batch) = receiver.try_recv() {
                        return Some(packet_batch);
                    }
                    sleep(Duration::from_millis(25)).await;
                }
                None
            };

            // Pre-ban: same pubkey is accepted from different source IP addresses.
            let connection1 = make_client_endpoint_with_bind_ip(
                &server_address,
                IpAddr::V4(Ipv4Addr::new(127, 0, 0, 1)),
                Some(&client_keypair),
            )
            .await
            .expect("connection should succeed for staked client");
            let mut stream = connection1.open_uni().await.unwrap();
            stream.write_all(&[9u8]).await.unwrap();
            stream.finish().unwrap();
            assert!(wait_for_packet().await.is_some());

            let connection2 = make_client_endpoint_with_bind_ip(
                &server_address,
                IpAddr::V4(Ipv4Addr::new(127, 0, 0, 2)),
                Some(&client_keypair),
            )
            .await
            .expect("connection should succeed for staked client");
            let mut stream = connection2.open_uni().await.unwrap();
            stream.write_all(&[9u8]).await.unwrap();
            stream.finish().unwrap();
            let packet_batch = wait_for_packet().await.unwrap();
            let remote_pubkey = packet_batch.get(0).unwrap().meta().remote_pubkey().unwrap();

            // Ban the pubkey and ensure new connections are rejected.
            banlist.ban(remote_pubkey, Duration::from_secs(30));

            // Existing connections from this pubkey should be actively evicted.
            let start = Instant::now();
            let mut existing_connection_closed = false;
            while start.elapsed().as_secs() < 3 {
                if connection1.close_reason().is_some() {
                    existing_connection_closed = true;
                    break;
                }
                sleep(Duration::from_millis(25)).await;
            }
            assert!(existing_connection_closed);

            let post_ban = make_client_endpoint_with_bind_ip(
                &server_address,
                IpAddr::V4(Ipv4Addr::new(127, 0, 0, 3)),
                Some(&client_keypair),
            )
            .await;

            // Rejection can happen at handshake or when opening streams.
            if let Ok(connection) = post_ban
                && let Ok(mut stream) = connection.open_uni().await
            {
                let _ = stream.write_all(&[7u8]).await;
                let _ = stream.finish();
            }

            // Ensure nothing from the post-ban attempt made it through.
            let start = Instant::now();
            while start.elapsed().as_secs() < 1 {
                assert!(receiver.try_recv().is_err());
                sleep(Duration::from_millis(25)).await;
            }
        });
        cancel.cancel();
        t.join().unwrap();
    }
```
