## Title
Self-reported/observed QUIC RTT is trusted uncapped for max-concurrent-stream (BDP) scaling, letting unstaked/staked peers inflate their own admission budget - (File: `streamer/src/nonblocking/swqos.rs`)

## Summary
The Deriverse bug used an unvalidated, attacker-influenceable last-observed value (spot `last_px`) directly in a critical safety calculation (liquidation threshold) with no averaging/oracle protection. The Agave analog is `compute_max_allowed_uni_streams_with_rtt` in `streamer/src/nonblocking/swqos.rs`, which takes the QUIC connection's observed round-trip-time (`connection.rtt()`) — a value a remote, unprivileged peer can influence simply by delaying its own ACKs — and uses it, unaveraged and only loosely bounded, to directly scale the number of concurrent unidirectional streams the TPU QUIC server will admit for that connection.

## Finding Description
`cache_new_connection` reads the connection's RTT and feeds it straight into the stream-budget formula: [1](#0-0) 

```rust
let rtt_millis = connection.rtt().as_millis().min(MAX_RTT_MS as u128) as u32;
let max_uni_streams = VarInt::from_u32(compute_max_allowed_uni_streams_with_rtt(
    rtt_millis,
    conn_context.peer_type(),
    conn_context.total_stake,
));
```

`compute_max_allowed_uni_streams_with_rtt` multiplies the base stream allotment by `rtt_millis / REFERENCE_RTT_MS` (clamped to `[REFERENCE_RTT_MS, MAX_RTT_MS] = [50, 350]`), i.e. up to a **7x** amplification, and — critically — for the `Unstaked` branch this multiplier is applied with **no upper clamp on the resulting stream count** at all: [2](#0-1) 

The staked branch at least clamps to `QUIC_MAX_STAKED_CONCURRENT_STREAMS` (512), but the unstaked branch's base value (`QUIC_MAX_UNSTAKED_CONCURRENT_STREAMS = 128`) is scaled by the same RTT multiplier with no equivalent ceiling, so an unstaked, fully unprivileged remote peer can obtain up to ~896 concurrent unidirectional streams (128 × 350/50) simply by making its connection appear to have a high RTT — something it fully controls since it is the party generating the ACKs that quinn's RTT estimator measures. This is confirmed by the unit test itself documenting the intended behavior ("Max streams should scale with BDP in high-RTT connections"): [3](#0-2) 

The resulting `max_uni_streams` is applied directly to the QUIC transport via `connection.set_max_concurrent_uni_streams`, governing how many concurrent streams (and thus how much per-connection buffering/state in `handle_connection`/`PacketAccumulator`) the TPU QUIC listener will admit: [4](#0-3) [5](#0-4) 

The same unguarded pattern exists in `simple_qos.rs`, where RTT similarly scales `max_streams_in_flight`: [6](#0-5) 

This mirrors the Deriverse pattern exactly: a value nominally intended as a benign "network condition" signal (spot price / RTT) is trusted at face value and fed unmodified into a security/resource-control decision (liquidation threshold / stream admission quota), with no sanity-check against manipulation by the very party whose behavior it is meant to measure.

## Impact Explanation
Existing guards (per-IP/per-pubkey connection limits, `StakedStreamLoadEMA` throttling, `max_connections_per_peer`) operate on connection counts and per-ms *rate*, not on the *concurrent stream ceiling* set at connection admission time. None of them re-validate or bound the RTT-derived multiplier for the unstaked path. An attacker who inflates perceived RTT (trivially done by delaying local ACK generation) gets each of their permitted unstaked/staked connections admitted with a disproportionately large concurrent-stream budget, multiplying the amount of per-connection server-side state (open streams, `PacketAccumulator` buffers, stream-tracking data structures in `handle_connection`) that the TPU/QUIC ingestion path must sustain per connection — a resource-exhaustion amplification vector against the QUIC/TPU intake achievable by any unprivileged remote client, matching the "non-RPC remote exhaustion/crash" impact category.

## Likelihood Explanation
Likelihood is moderate-to-high: the attacker primitive (control your own ACK timing to inflate a peer-measured RTT) requires no special access, no stake, and no cooperation from any other party — only opening ordinary QUIC connections to the validator's TPU port, which is inherently open to permissionless traffic. The multiplier is a straightforward, deterministic function of self-controlled RTT with no oracle/averaging/outlier-rejection, so the "manipulation primitive" from the source report translates directly.

## Recommendation
Do not scale admission-critical resource budgets from a single, unaveraged, attacker-influenceable RTT sample. Consider: (1) applying the same hard ceiling (`QUIC_MAX_UNSTAKED_CONCURRENT_STREAMS`-equivalent clamp) to the unstaked branch's BDP-scaled result that the staked branch already has; (2) using a smoothed/validated RTT estimate (e.g., minimum-observed RTT over multiple samples, or ignore RTT increases attributable to the peer delaying ACKs) rather than a single instantaneous `connection.rtt()` read at connection-admission time; (3) bounding total aggregate streams across all connections of a peer type independent of per-connection RTT claims.

## Proof of Concept
1. Open a QUIC connection to the validator's TPU QUIC endpoint as an unstaked client.
2. Deliberately delay ACK transmission on the connection (or otherwise induce quinn to measure a high RTT, up to the `MAX_RTT_MS = 350ms` ceiling used in `cache_new_connection`).
3. On connection admission, `compute_max_allowed_uni_streams_with_rtt` computes `128 * (350/50) = 896` as the unstaked concurrent-uni-stream allowance instead of the intended 128 — a ~7x increase — applied via `connection.set_max_concurrent_uni_streams`.
4. Repeat across the allowed number of unstaked connections (`max_unstaked_connections`) to multiply the server-side per-connection stream/state footprint the TPU QUIC listener must service, well beyond the values the constants (`QUIC_MAX_UNSTAKED_CONCURRENT_STREAMS`, etc.) were empirically tuned for. [7](#0-6)

### Citations

**File:** streamer/src/nonblocking/swqos.rs (L147-202)
```rust
fn compute_max_allowed_uni_streams_with_rtt(
    rtt_millis: u32,
    peer_type: ConnectionPeerType,
    total_stake: u64,
) -> u32 {
    let streams = match peer_type {
        ConnectionPeerType::Staked(peer_stake) => {
            // No checked math for f64 type. So let's explicitly check for 0 here
            if total_stake == 0 || peer_stake > total_stake {
                warn!(
                    "Invalid stake values: peer_stake: {peer_stake:?}, total_stake: \
                     {total_stake:?}"
                );

                QUIC_MIN_STAKED_CONCURRENT_STREAMS
            } else {
                let delta = (QUIC_TOTAL_STAKED_CONCURRENT_STREAMS
                    - QUIC_MIN_STAKED_CONCURRENT_STREAMS) as f64;

                (((peer_stake as f64 / total_stake as f64) * delta) as u32
                    + QUIC_MIN_STAKED_CONCURRENT_STREAMS)
                    .clamp(
                        QUIC_MIN_STAKED_CONCURRENT_STREAMS,
                        QUIC_MAX_STAKED_CONCURRENT_STREAMS,
                    )
            }
        }
        ConnectionPeerType::Unstaked => QUIC_MAX_UNSTAKED_CONCURRENT_STREAMS,
    };
    // scale amount of streams based on RTT if RTT is larger than REFERENCE_RTT_MS
    // multiply first then divide to avoid rounding errors.
    (streams.saturating_mul(rtt_millis.clamp(REFERENCE_RTT_MS, MAX_RTT_MS))) / REFERENCE_RTT_MS
}

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
```

**File:** streamer/src/nonblocking/swqos.rs (L224-224)
```rust
            connection.set_max_concurrent_uni_streams(max_uni_streams);
```

**File:** streamer/src/nonblocking/swqos.rs (L560-580)
```rust
    #[test]
    fn test_max_allowed_uni_streams_with_rtt() {
        assert_eq!(
            compute_max_allowed_uni_streams_with_rtt(
                REFERENCE_RTT_MS / 2,
                ConnectionPeerType::Unstaked,
                10000
            ),
            QUIC_MAX_UNSTAKED_CONCURRENT_STREAMS,
            "Max streams should not be less than normal for low RTT"
        );
        assert_eq!(
            compute_max_allowed_uni_streams_with_rtt(
                REFERENCE_RTT_MS + REFERENCE_RTT_MS / 2,
                ConnectionPeerType::Unstaked,
                10000
            ),
            QUIC_MAX_UNSTAKED_CONCURRENT_STREAMS + QUIC_MAX_UNSTAKED_CONCURRENT_STREAMS / 2,
            "Max streams should scale with BDP in high-RTT connections"
        );
    }
```

**File:** streamer/src/nonblocking/quic.rs (L605-636)
```rust

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
```

**File:** streamer/src/nonblocking/simple_qos.rs (L190-199)
```rust

        // this will never overflow u32 for reasonable MAX_RTT
        let rtt = connection.rtt().clamp(MIN_RTT, MAX_RTT).as_millis() as u32;
        let max_streams_in_flight = (self.config.max_streams_per_second as u32).saturating_mul(rtt)
            / 1000
            * STREAMS_IN_FLIGHT_MARGIN;
        // for very low values of max_streams_per_second, prevent connections from having zero
        // streams in flight
        let max_streams_in_flight = max_streams_in_flight.max(STREAMS_IN_FLIGHT_MARGIN);
        connection.set_max_concurrent_uni_streams(VarInt::from_u32(max_streams_in_flight));
```
