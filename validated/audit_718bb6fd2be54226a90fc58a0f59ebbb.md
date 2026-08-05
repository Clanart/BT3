### Title
QUIC uni-stream allocation is bypassed via self-reported RTT inflation at connection setup, defeating the documented per-peer stream caps - ([File: streamer/src/nonblocking/swqos.rs])

### Summary
`SwQos::cache_new_connection` grants each QUIC connection a concurrent-uni-stream budget computed once, at connection-accept time, from the connection's currently measured RTT. Because QUIC RTT is derived in part from the ACK-delay value the *client itself* reports, an unprivileged peer can inflate its own measured RTT to make the server believe the link is slow, which multiplies the stream budget by up to 7x — bypassing the `QUIC_MAX_STAKED_CONCURRENT_STREAMS` / `QUIC_MAX_UNSTAKED_CONCURRENT_STREAMS` caps that the code otherwise enforces. The inflated grant is applied via `connection.set_max_concurrent_uni_streams(...)` once and is never re-evaluated for the lifetime of the connection, so the effect persists exactly like the RAACMinter emission-rate manipulation persists until the next update interval.

### Finding Description
`compute_max_allowed_uni_streams_with_rtt` first computes a `streams` value for the peer based on its stake share and clamps it to `[QUIC_MIN_STAKED_CONCURRENT_STREAMS, QUIC_MAX_STAKED_CONCURRENT_STREAMS]` (or the fixed `QUIC_MAX_UNSTAKED_CONCURRENT_STREAMS` for unstaked peers): [1](#0-0) 

It then scales that already-clamped value by the connection's observed RTT, clamped only to `[REFERENCE_RTT_MS(50ms), MAX_RTT_MS(350ms)]`, and the clamp on RTT is applied *after* the stream-count clamp, so the multiplication can push the final result up to 7x above the nominal per-type maximum: [2](#0-1) 

This function is invoked in `cache_new_connection`, which reads `connection.rtt()` a single time when the connection is added to the connection table, and immediately applies the resulting stream limit to the live QUIC connection: [3](#0-2) 

The broken invariant is: the constants `QUIC_MIN_STAKED_CONCURRENT_STREAMS`, `QUIC_MAX_STAKED_CONCURRENT_STREAMS`, and `QUIC_MAX_UNSTAKED_CONCURRENT_STREAMS` are documented as hard per-connection maximums for concurrent uni-directional streams: [4](#0-3) 
but the RTT-based BDP scaling step is not similarly bounded relative to the *final* stream count — it multiplies an already-capped value, so the true effective ceiling becomes `cap * (MAX_RTT_MS / REFERENCE_RTT_MS)` = 7x the documented cap.

`rtt_millis` comes from `connection.rtt()` (quinn's live RTT estimator), which is influenced by the ACK Delay field the remote peer reports in its own ACK frames. A client can therefore deliberately delay/space out its ACKs (or otherwise manipulate the RTT samples observed during connection setup) to drive its apparent RTT toward `MAX_RTT_MS` before the server calls `cache_new_connection`. Because the resulting `set_max_concurrent_uni_streams` call happens exactly once — there is no periodic re-check found anywhere else in `swqos.rs`/`quic.rs` that lowers the limit again after RTT normalizes — the inflated allocation persists for the entire connection lifetime, mirroring how the RAACMinter emission rate stays inflated until the next `updateEmissionRate()` interval even after the attacker's manipulated input reverts to normal.

No existing guard stops this path: the stake-based clamp only bounds the pre-RTT-scaling `streams` term, and there is no server-side validation that the reported/measured RTT is plausible for the peer's actual network path.

### Impact Explanation
This is an unprivileged, remote, non-RPC vector against the TPU/QUIC ingestion path. By inflating apparent RTT, any connecting peer (staked or unstaked) can obtain a materially larger per-connection concurrent-stream allocation than the protocol intends (up to 7x `QUIC_MAX_STAKED_CONCURRENT_STREAMS`/`QUIC_MAX_UNSTAKED_CONCURRENT_STREAMS`). Combined across many connections, this expands the per-connection resource footprint (stream buffers/state, receive-window consumption) the validator must service, amplifying memory/CPU pressure on the TPU QUIC listener beyond the sizing assumptions baked into the fixed constants — a remote resource-exhaustion/degradation vector against a single validator's transaction-ingestion path, without requiring any trusted role, stake concentration, or malicious-validator assumption.

### Likelihood Explanation
Likelihood is moderate-to-high for a determined unprivileged client: manipulating perceived RTT via delayed ACKs is a standard, low-effort technique against RTT-based estimators, requires no stake and no validator privileges, and only needs to be done once at connection setup (not sustained), since the resulting stream cap is fixed for the connection's whole lifetime. It does require crafting a QUIC client (or using an existing QUIC library with knobs for ACK delay/pacing) that talks to the TPU port, which is a normal capability for any Solana network participant.

### Recommendation
- Apply the RTT-based BDP scaling before the final clamp, not after, so the enforced stream-count ceiling (`QUIC_MAX_STAKED_CONCURRENT_STREAMS` / `QUIC_MAX_UNSTAKED_CONCURRENT_STREAMS`) is truly an upper bound regardless of RTT.
- Treat the RTT sample used for BDP scaling with skepticism: use a smoothed/validated RTT (e.g., minimum RTT over multiple samples, or a value bounded by plausible network conditions) rather than a single instantaneous `connection.rtt()` read at accept time.
- Periodically re-evaluate and, if necessary, reduce `max_concurrent_uni_streams` for long-lived connections rather than fixing it permanently at accept time.

### Proof of Concept
1. Implement (or configure) a QUIC client that connects to a validator's TPU QUIC port and, during the handshake/initial RTT-probing exchange, artificially delays its ACK responses or reports an inflated ACK Delay field so that quinn's RTT estimator on the server side converges near `MAX_RTT_MS` (350 ms) rather than the true network RTT.
2. The server's `cache_new_connection` reads this inflated RTT via `connection.rtt()` and calls `compute_max_allowed_uni_streams_with_rtt`, which multiplies the stake/type-based, already-clamped stream count by `350/50 = 7`. [5](#0-4) 
3. `connection.set_max_concurrent_uni_streams(max_uni_streams)` is applied once with this inflated value and never re-evaluated afterward for the connection's lifetime.
4. The attacker then behaves as a normal (or low-latency) client for the remainder of the connection, retaining the 7x inflated concurrent-stream allocation for as long as the connection stays open, allowing it to open substantially more concurrent streams than the documented per-connection maximum permits — amplifying resource usage per connection beyond the intended sizing.

### Citations

**File:** streamer/src/nonblocking/swqos.rs (L147-175)
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
```

**File:** streamer/src/nonblocking/swqos.rs (L176-179)
```rust
    // scale amount of streams based on RTT if RTT is larger than REFERENCE_RTT_MS
    // multiply first then divide to avoid rounding errors.
    (streams.saturating_mul(rtt_millis.clamp(REFERENCE_RTT_MS, MAX_RTT_MS))) / REFERENCE_RTT_MS
}
```

**File:** streamer/src/nonblocking/swqos.rs (L181-224)
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
```

**File:** streamer/src/quic.rs (L36-48)
```rust
/// QUIC connection idle timeout. The connection will be closed if there are no activities on it
/// within the timeout window. The chosen value is default for quinn.
pub const QUIC_MAX_TIMEOUT: Duration = Duration::from_secs(30);

// allow multiple connections for NAT and any open/close overlap
pub const DEFAULT_MAX_QUIC_CONNECTIONS_PER_UNSTAKED_PEER: usize = 8;

// allow multiple connections per ID for geo-distributed forwarders
pub const DEFAULT_MAX_QUIC_CONNECTIONS_PER_STAKED_PEER: usize = 16;

pub const DEFAULT_MAX_STAKED_CONNECTIONS: usize = 2000;

pub const DEFAULT_MAX_UNSTAKED_CONNECTIONS: usize = 2000;
```
