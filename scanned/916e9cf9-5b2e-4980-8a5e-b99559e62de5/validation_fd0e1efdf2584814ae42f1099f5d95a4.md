### Title
Unauthenticated RPC-pubsub WebSocket connections have no handshake/idle read timeout, enabling remote resource exhaustion - (`rpc/src/rpc_pubsub_service.rs`)

### Summary
The Sherlock report flags `makeApiCall` for lacking any HTTP client timeout, letting a hanging remote endpoint hold a connection (and goroutine) open forever, exhausting resources. The Agave analog is `listen`/`handle_connection` in `rpc/src/rpc_pubsub_service.rs`, which accepts unauthenticated TCP connections on the JSON-RPC pubsub (WebSocket) port and neither bounds the WebSocket handshake nor imposes any idle-read timeout on established connections.

### Finding Description
`listen()` accepts every incoming TCP connection unconditionally and spawns a task per connection with only a connection counter for metrics, no cap and no timeout: [1](#0-0) 

Inside `handle_connection`, the very first step awaits the WebSocket handshake indefinitely: [2](#0-1) 

After the handshake, the main loop awaits `receiver.receive_data(&mut data)` with no timeout — the only other branches in the `select!` are the broadcast channel and the shutdown `tripwire`, neither of which fires due to client inactivity: [3](#0-2) 

A client that opens a TCP connection to the pubsub port and either never completes the WebSocket handshake, or completes it and then never sends another frame, causes the spawned `tokio::spawn(handle_connection(...))` task, its socket, and its `DashMap`/broadcast-receiver state to remain allocated indefinitely. There is no `TcpStream` read timeout, no handshake timeout, and no idle-connection timeout anywhere in this path, unlike the QUIC/TPU path (`streamer/src/nonblocking/quic.rs`) which explicitly implements connection-attempt timeouts, per-IP rate limiting, and a cap on concurrent connections: [4](#0-3) 
or the `ip_echo_server.rs` TCP listener, which enforces `MAX_CONCURRENT_CONNECTIONS` and per-IP connection limits plus an I/O deadline (`IO_TIMEOUT`) on every read/write: [5](#0-4) [6](#0-5) 

The `TokenCounter` used in `listen()` only emits a metric of the current connection count; it does not enforce any limit or trigger any eviction: [7](#0-6) 

`PubSubConfig` (`rpc/src/rpc_pubsub_service.rs:41-49`) has fields for subscription counts and queue capacities, but none for max concurrent connections or per-connection idle timeout: [8](#0-7) 

### Impact Explanation
This is a non-RPC-request remote resource exhaustion vector reachable by a single unprivileged, unauthenticated client (no special peer/validator trust assumed). Because there is no cap on the number of concurrent connections, no per-IP limit (unlike `ip_echo_server`), and no idle/handshake timeout, an attacker can open a very large number of TCP sockets to the pubsub port, either stalling on the WebSocket handshake or completing it and then going silent, to accumulate unbounded server-side tasks/memory/file descriptors on the validator's RPC pubsub service until the process degrades or crashes (matches "non-RPC remote exhaustion/crash" and "single-client low-rate RPC crash/degradation" categories).

### Likelihood Explanation
Likelihood is high for validators/RPC nodes exposing the pubsub WebSocket endpoint publicly: the exploit requires only opening TCP sockets and optionally leaving them idle (or intentionally stalling the handshake) — no valid subscription, authentication, or protocol knowledge is needed, and it can be scripted trivially with many concurrent low-rate connections, consistent with existing OS-level fd/socket-exhaustion attacks against long-lived servers that lack timeouts.

### Recommendation
Add explicit timeouts to the pubsub connection lifecycle: wrap the WebSocket handshake (`server.receive_request()` / `send_response()`) in a `tokio::time::timeout`, add an idle-read timeout branch to the main `select!` loop in `handle_connection` that closes the connection if no client message arrives within a bounded window, and enforce a maximum number of concurrent pubsub connections (globally and per-IP), similar to the protections already implemented in `streamer/src/nonblocking/quic.rs` and `net-utils/src/ip_echo_server.rs`.

### Proof of Concept
1. Start a validator/RPC node with the JSON-RPC pubsub service enabled (`PubSubService::new`, listening via `listen()` in `rpc/src/rpc_pubsub_service.rs`).
2. From an unprivileged client, open N raw TCP connections to the pubsub port (e.g., `nc host port` repeated, or a script using raw sockets) without completing the WebSocket handshake, or complete the handshake once and then send nothing further.
3. Because `handle_connection`'s handshake await (`rpc_pubsub_service.rs:356`) and its main-loop `receive_future` await (`rpc_pubsub_service.rs:384-398`) have no timeout, and `listen()` (`rpc_pubsub_service.rs:449-467`) spawns an unbounded number of such tasks, each connection persists indefinitely, consuming a tokio task, socket, and associated buffers.
4. Repeating this at scale from one or few source IPs accumulates unbounded resource usage on the pubsub runtime (`pubsub_config.worker_threads`), eventually degrading or exhausting the validator's RPC pubsub service.

### Citations

**File:** rpc/src/rpc_pubsub_service.rs (L40-49)
```rust
#[derive(Debug, Clone, PartialEq)]
pub struct PubSubConfig {
    pub enable_block_subscription: bool,
    pub enable_vote_subscription: bool,
    pub max_active_subscriptions: usize,
    pub queue_capacity_items: usize,
    pub queue_capacity_bytes: usize,
    pub worker_threads: usize,
    pub notification_threads: Option<NonZeroUsize>,
}
```

**File:** rpc/src/rpc_pubsub_service.rs (L354-361)
```rust
) -> Result<(), Error> {
    let mut server = Server::new(socket.compat());
    let request = server.receive_request().await?;
    let accept = server::Response::Accept {
        key: request.key(),
        protocol: None,
    };
    server.send_response(&accept).await?;
```

**File:** rpc/src/rpc_pubsub_service.rs (L379-413)
```rust
    loop {
        // Extra block for dropping `receive_future`.
        {
            // soketto is not cancel safe, so we have to introduce an inner loop to poll
            // `receive_data` to completion.
            let receive_future = receiver.receive_data(&mut data);
            pin!(receive_future);
            loop {
                select! {
                    biased; // See [prioritization] note below.

                    // [prioritization]
                    // This block must come FIRST in the `select!` macro. This prioritizes
                    // processing received messages over sending messages. This ensures the timely
                    // processing of new subscriptions and time-sensitive opcodes like `PING`.
                    result = &mut receive_future => match result {
                        Ok(_) => break,
                        Err(soketto::connection::Error::Closed) => return Ok(()),
                        Err(err) => return Err(err.into()),
                    },
                    result = broadcast_receiver.recv() => {

                        // In both possible error cases (closed or lagged) we disconnect the client.
                        if let Some(json) = broadcast_handler.handle(result?)? {
                            sender.send_text(&*json).await?;
                        }
                    },
                    _ = &mut tripwire => {
                        warn!("disconnecting websocket client: shutting down");
                        return Ok(())
                    },

                }
            }
        }
```

**File:** rpc/src/rpc_pubsub_service.rs (L448-473)
```rust
    let counter = TokenCounter::new("rpc_pubsub_connections");
    loop {
        select! {
            result = listener.accept() => match result {
                Ok((socket, addr)) => {
                    debug!("new client ({addr:?})");
                    let subscription_control = subscription_control.clone();
                    let config = config.clone();
                    let tripwire = tripwire.clone();
                    let counter_token = counter.create_token();
                    tokio::spawn(async move {
                        let handle = handle_connection(
                            socket, subscription_control, config, tripwire
                        );
                        match handle.await {
                            Ok(()) => debug!("connection closed ({addr:?})"),
                            Err(err) => warn!("connection handler error ({addr:?}): {err}"),
                        }
                        drop(counter_token); // Force moving token into the task.
                    });
                }
                Err(e) => error!("couldn't accept connection: {e:?}"),
            },
            _ = &mut tripwire => return Ok(()),
        }
    }
```

**File:** streamer/src/nonblocking/quic.rs (L331-379)
```rust
        if let Ok(Some(incoming)) = timeout_connection {
            // our connection/handshake abuse mitigation policy is one of shed
            // fast and bound resource consumption. attempting to be "smarter"
            // before a peer has asserted control over their ip address by
            // completing the retry challenge creates a scenario whereby peers
            // can attack one another via ip spoofing. employ the following
            // * limit duration of in-flight connection attempts with a timeout
            // * protect against connection attempt bursts with a global rate-limiter
            // * rate-limit abusive peers by (control-asserted) ip
            // * cap total connections per-peer/ip

            stats
                .total_incoming_connection_attempts
                .fetch_add(1, Ordering::Relaxed);

            // check overall connection request rate limiter
            if overall_connection_rate_limiter.current_tokens() == 0 {
                stats
                    .connection_rate_limited_across_all
                    .fetch_add(1, Ordering::Relaxed);
                debug!(
                    "Ignoring incoming connection from {} due to overall rate limit.",
                    incoming.remote_address()
                );
                incoming.ignore();
                continue;
            }
            // then perform per IpAddr rate limiting
            if !rate_limiter.is_allowed(&incoming.remote_address().ip()) {
                stats
                    .connection_rate_limited_per_ipaddr
                    .fetch_add(1, Ordering::Relaxed);
                debug!(
                    "Ignoring incoming connection from {} due to per-IP rate limiting.",
                    incoming.remote_address()
                );
                incoming.ignore();
                continue;
            }

            let Ok(client_connection_tracker) =
                ClientConnectionTracker::new(stats.clone(), qos.max_concurrent_connections())
            else {
                stats
                    .refused_connections_too_many_open_connections
                    .fetch_add(1, Ordering::Relaxed);
                incoming.refuse();
                continue;
            };
```

**File:** net-utils/src/ip_echo_server.rs (L92-102)
```rust
    let deadline = Instant::now()
        .checked_add(IO_TIMEOUT)
        .ok_or_else(|| io::Error::other("failed to compute request deadline"))?;

    let mut data = vec![0u8; ip_echo_server_request_length()];

    let mut writer = {
        let (mut reader, writer) = socket.split();
        let _ = timeout_at(deadline, reader.read_exact(&mut data)).await??;
        writer
    };
```

**File:** net-utils/src/ip_echo_server.rs (L189-225)
```rust
    loop {
        let connection = tcp_listener.accept().await;
        match connection {
            Ok((socket, peer_addr)) => {
                let tracked_ip = (!peer_addr.ip().is_loopback()).then_some(peer_addr.ip());
                if let Some(ip) = tracked_ip {
                    let mut active_ip_set = active_ips
                        .lock()
                        .expect("active_ips lock poisoned while admitting");
                    if active_ip_set.len() >= MAX_CONCURRENT_CONNECTIONS {
                        debug!(
                            "dropping connection from {peer_addr:?}: max concurrent connections \
                             ({MAX_CONCURRENT_CONNECTIONS}) reached",
                        );
                        continue;
                    }
                    if !active_ip_set.insert(ip) {
                        debug!(
                            "dropping connection from {peer_addr:?}: max concurrent connections \
                             per IP (1) reached"
                        );
                        continue;
                    }
                }
                let cleanup =
                    tracked_ip.map(|ip| ConnectionCleanup::new(Arc::clone(&active_ips), ip));
                runtime::Handle::current().spawn(async move {
                    let cleanup = cleanup;
                    if let Err(err) = process_connection(socket, peer_addr, shred_version).await {
                        info!("session failed: {err:?}");
                    }
                    drop(cleanup);
                });
            }
            Err(err) => warn!("listener accept failed: {err:?}"),
        }
    }
```

**File:** metrics/src/lib.rs (L30-49)
```rust
/// A helper that sends the count of created tokens as a datapoint.
#[allow(clippy::redundant_allocation)]
pub struct TokenCounter(Arc<&'static str>);

impl TokenCounter {
    /// Creates a new counter with the specified metrics `name`.
    pub fn new(name: &'static str) -> Self {
        Self(Arc::new(name))
    }

    /// Creates a new token for this counter. The metric's value will be equal
    /// to the number of `CounterToken`s.
    pub fn create_token(&self) -> CounterToken {
        // new_count = strong_count
        //    - 1 (in TokenCounter)
        //    + 1 (token that's being created)
        datapoint_info!(*self.0, ("count", Arc::strong_count(&self.0), i64));
        CounterToken(self.0.clone())
    }
}
```
