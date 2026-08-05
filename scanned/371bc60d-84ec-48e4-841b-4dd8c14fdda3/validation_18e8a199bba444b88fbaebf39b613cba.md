[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** streamer/src/nonblocking/simple_qos.rs (L69-87)
```rust
    pub fn ban(&self, pubkey: Pubkey, timeout: Duration) -> bool {
        let ret = self.banlist.ban(pubkey, timeout);
        match self.eviction_sender.try_send(pubkey) {
            Ok(()) => {}
            Err(TrySendError::Full(pubkey)) => {
                error!(
                    "Simple QoS banlist eviction queue full, dropping eviction request for \
                     {pubkey}"
                );
            }
            Err(TrySendError::Closed(pubkey)) => {
                info!(
                    "Simple QoS banlist eviction queue closed, dropping eviction request for \
                     {pubkey}"
                );
            }
        }
        ret
    }
```

**File:** streamer/src/nonblocking/simple_qos.rs (L100-121)
```rust
        let _eviction_task = tokio::spawn(async move {
            let mut prune_interval = interval(BANLIST_PRUNE_INTERVAL);
            prune_interval.set_missed_tick_behavior(MissedTickBehavior::Skip);
            prune_interval.tick().await;
            loop {
                tokio::select! {
                    maybe_pubkey = eviction_receiver.recv() => {
                        let Some(pubkey) = maybe_pubkey else {
                            break;
                        };
                        let mut connection_table = staked_connection_table.lock().await;
                        let removed_connection_count = connection_table
                            .remove_connections_by_key(ConnectionTableKey::Pubkey(pubkey));
                        if removed_connection_count > 0 {
                            update_open_connections_stat(&stats, &connection_table);
                            stats
                                .connection_removed
                                .fetch_add(removed_connection_count, Ordering::Relaxed);
                            stats
                                .connection_removed_banned
                                .fetch_add(removed_connection_count, Ordering::Relaxed);
                        }
```

**File:** streamer/src/nonblocking/simple_qos.rs (L869-904)
```rust
    #[tokio::test]
    async fn test_try_add_connection_banned_pubkey_rejected() {
        let cancel = CancellationToken::new();
        let stats = Arc::new(StreamerStats::default());
        let server_keypair = Keypair::new();
        let client_keypair = Keypair::new();
        let staked_nodes =
            create_staked_nodes_with_keypairs(&server_keypair, &client_keypair, 50_000_000);

        let simple_qos = SimpleQos::new(
            SimpleQosConfig::default(),
            stats.clone(),
            staked_nodes,
            cancel,
        );

        simple_qos
            .banlist
            .ban(client_keypair.pubkey(), Duration::from_secs(30));

        let client_tracker = ClientConnectionTracker {
            stats: stats.clone(),
        };
        let (server_connection, _client_endpoint, _server_endpoint) =
            create_connection_with_keypairs(&server_keypair, &client_keypair).await;
        let mut conn_context = simple_qos.build_connection_context(&server_connection);
        let result = simple_qos
            .try_add_connection(client_tracker, &server_connection, &mut conn_context)
            .await;

        assert!(result.is_none());
        assert_eq!(
            stats.connection_add_failed_banned.load(Ordering::Relaxed),
            1
        );
    }
```

**File:** streamer/src/nonblocking/quic.rs (L647-707)
```rust
        loop {
            // Read the next chunks, waiting up to `wait_for_chunk_timeout`. If we don't get chunks
            // before then, we assume the stream is dead. This can only happen if there's severe
            // packet loss or the peer stops sending for whatever reason.
            let n_chunks = match tokio::select! {
                chunk = tokio::time::timeout(
                    wait_for_chunk_timeout,
                    stream.read_chunks(&mut chunks)) => chunk,

                // If the peer gets disconnected stop the task right away.
                _ = cancel.cancelled() => break,
            } {
                // read_chunk returned success
                Ok(Ok(chunk)) => chunk.unwrap_or(0),
                // read_chunk returned error
                Ok(Err(e)) => {
                    debug!("Received stream error: {e:?}");
                    stats
                        .total_stream_read_errors
                        .fetch_add(1, Ordering::Relaxed);
                    break;
                }
                // timeout elapsed
                Err(_) => {
                    debug!("Timeout in receiving on stream");
                    stats
                        .total_stream_read_timeouts
                        .fetch_add(1, Ordering::Relaxed);
                    break;
                }
            };

            match handle_chunks(
                // Bytes::clone() is a cheap atomic inc
                chunks.iter().take(n_chunks).cloned(),
                &mut accum,
                rtt,
                &packet_sender,
                &stats,
                peer_type,
                max_stream_data_bytes,
            ) {
                // The stream is finished, break out of the loop and close the stream.
                Ok(StreamState::Finished) => {
                    qos.on_stream_finished(&context);
                    break;
                }
                // The stream is still active, continue reading.
                Ok(StreamState::Receiving) => {}
                Err(_) => {
                    // Disconnect peers that send invalid streams.
                    connection.close(
                        CONNECTION_CLOSE_CODE_INVALID_STREAM.into(),
                        CONNECTION_CLOSE_REASON_INVALID_STREAM,
                    );
                    stats.active_streams.fetch_sub(1, Ordering::Relaxed);
                    qos.on_stream_error(&context);
                    break 'conn;
                }
            }
        }
```

**File:** streamer/src/nonblocking/quic.rs (L814-836)
```rust
    let packet_batch = PacketBatch::Single(packet);

    if let Err(err) = packet_sender.try_send(packet_batch) {
        stats
            .total_handle_chunk_to_packet_send_err
            .fetch_add(1, Ordering::Relaxed);
        match err {
            TrySendError::Full(_) => {
                stats
                    .total_handle_chunk_to_packet_send_full_err
                    .fetch_add(1, Ordering::Relaxed);
            }
            TrySendError::Disconnected(_) => {
                stats
                    .total_handle_chunk_to_packet_send_disconnected_err
                    .fetch_add(1, Ordering::Relaxed);
            }
        }
        trace!("packet batch send error {err:?}");
    } else {
        stats
            .total_bytes_sent_to_consumer
            .fetch_add(packet_size, Ordering::Relaxed);
```

**File:** streamer/src/quic.rs (L996-1009)
```rust
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
```
