## Finding [1](#0-0) 

The Agave analog to the "unlimited concurrent requests" bug is the `agave-validator`'s **RPC PubSub (WebSocket) listener**, `listen()` in `rpc/src/rpc_pubsub_service.rs`.

### Title
Unbounded concurrent WebSocket connections in RPC PubSub service enable remote memory-exhaustion DoS - (File: `rpc/src/rpc_pubsub_service.rs`)

### Summary
The Staking API report's bug class is "no limit on concurrent in-flight requests leads to unbounded memory growth and process death." The Agave `PubSubService` accept loop in `listen()` accepts every incoming TCP connection unconditionally and spawns a new tokio task (`handle_connection`) for each one, with no cap on the number of simultaneously open connections/tasks.

### Finding Description
`listen()` runs an infinite `select!` loop that calls `listener.accept()` and, for every successful accept, unconditionally spawns a task: [2](#0-1) 

```rust
let counter = TokenCounter::new("rpc_pubsub_connections");
loop {
    select! {
        result = listener.accept() => match result {
            Ok((socket, addr)) => {
                ...
                let counter_token = counter.create_token();
                tokio::spawn(async move {
                    let handle = handle_connection(socket, subscription_control, config, tripwire);
                    ...
                    drop(counter_token);
                });
            }
            ...
```

`TokenCounter`/`CounterToken` (defined in `metrics/src/lib.rs` lines 30-84) only emit a `datapoint_info!` metric of the current live-connection count via `Arc::strong_count` — they perform **no admission control** and never reject a connection. There is no `semaphore`, no `max_connections` check, and no back-pressure mechanism anywhere in this accept loop.

Each spawned `handle_connection` call allocates a soketto WebSocket handshake buffer, a `DashMap` for `current_subscriptions`, and a `jsonrpc_core::IoHandler` with the full `RpcSolPubSubImpl` delegate registered: [3](#0-2) 

The only quota that exists in the pubsub subsystem, `max_active_subscriptions` (default `1_000_000`, `DEFAULT_MAX_ACTIVE_SUBSCRIPTIONS` at line 33), bounds the number of *subscriptions*, not the number of *TCP connections/tasks*. An attacker's connections don't even need to complete the WebSocket handshake to have already consumed a socket, an accepted `TcpStream`, and a spawned tokio task on the pubsub runtime (`worker_threads` = `DEFAULT_WORKER_THREADS = 1` by default, per `PubSubConfig::default_for_tests`/`args`). This is the direct analog of the Chi/Go HTTP server accepting unlimited concurrent requests without a `Throttle` middleware.

The companion JSON RPC HTTP server in `rpc/src/rpc_service.rs` has the same gap: `ServerBuilder` is configured with `.max_request_body_size(...)` and `.threads(1)` plus a tokio executor, but there is no analog of `.max_connections()` or any per-IP/global concurrency cap: [4](#0-3) 

### Impact Explanation
An unprivileged remote attacker can open a very large number of TCP connections to the public RPC PubSub port (default enabled whenever `--full-rpc-api` websocket subscriptions are exposed) without sending any valid data. Each accepted connection consumes validator process memory/file descriptors and a spawned task on a runtime with only a handful of worker threads, degrading or crashing the RPC/PubSub service and, if the pubsub/rpc threads compete with validator resources, potentially affecting overall node health. This matches "single-client low-rate RPC crash/degradation" / "non-RPC remote exhaustion" impact categories: the operator-facing RPC/pubsub endpoint becomes unavailable to legitimate clients (e.g. staking/wallet dApps depending on `signatureSubscribe`/`accountSubscribe`), analogous to stakers being unable to interact with the Staking API in the original report.

### Likelihood Explanation
Likelihood is high: this requires only knowledge of a public RPC endpoint's PubSub port and the ability to open many concurrent sockets (e.g. via a simple script or `slowloris`-style tooling), with no authentication, staking, or special network position required.

### Recommendation
Add a bounded concurrency gate to the PubSub accept loop (e.g. a `tokio::sync::Semaphore` or a hard cap tracked alongside/instead of the purely-informational `TokenCounter`), rejecting/closing new connections once a configurable `max_connections` limit is reached, mirroring the existing `max_active_subscriptions` pattern but applied at the connection level. The same should be considered for the JSON-RPC HTTP server in `rpc_service.rs` (e.g. exposing a `max_connections` config knob), consistent with the original report's suggestion of using a `Throttle`-style middleware.

### Proof of Concept
1. Start a validator/test-validator exposing the RPC PubSub port.
2. From an attacker host, open thousands of raw TCP connections to the pubsub port in a tight loop without completing/continuing the WebSocket handshake (or completing it and then idling), e.g.:
```python
import socket
socks = []
for _ in range(50000):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect(("target-rpc-host", PUBSUB_PORT))
    socks.append(s)  # keep open, don't send valid data
```
3. Observe validator process memory growth and increasing task/connection counts (via the `rpc_pubsub_connections` metric or OS-level fd/mem inspection) with no rejection ever occurring in `listen()`, since no cap exists in the accept loop shown in `rpc/src/rpc_pubsub_service.rs` lines 448-468.

### Citations

**File:** rpc/src/rpc_pubsub_service.rs (L355-378)
```rust
    let mut server = Server::new(socket.compat());
    let request = server.receive_request().await?;
    let accept = server::Response::Accept {
        key: request.key(),
        protocol: None,
    };
    server.send_response(&accept).await?;
    let mut builder = server.into_builder();
    builder.set_max_message_size(4_096);
    builder.set_max_frame_size(4_096);
    let (mut sender, mut receiver) = builder.finish();

    let mut broadcast_receiver = subscription_control.broadcast_receiver();
    let mut data = Vec::new();
    let current_subscriptions = Arc::new(DashMap::new());

    let mut json_rpc_handler = IoHandler::new();
    let rpc_impl = RpcSolPubSubImpl::new(
        config,
        subscription_control,
        Arc::clone(&current_subscriptions),
    );
    json_rpc_handler.extend_with(rpc_impl.to_delegate());
    let broadcast_handler = BroadcastHandler::new(current_subscriptions);
```

**File:** rpc/src/rpc_pubsub_service.rs (L429-474)
```rust
async fn listen(
    listen_address: SocketAddr,
    config: PubSubConfig,
    subscription_control: SubscriptionControl,
    mut tripwire: Tripwire,
) -> io::Result<()> {
    let listener = match tokio::net::TcpListener::bind(&listen_address).await {
        Ok(listener) => {
            info!("rpc_pubsub listening on {listen_address:?}");
            listener
        }
        Err(e) => {
            error!(
                "failed to bind rpc_pubsub listener on {listen_address:?}: {e}. Hint: is the port \
                 already in use?"
            );
            return Err(e);
        }
    };
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
}
```

**File:** rpc/src/rpc_service.rs (L724-743)
```rust
                let server = ServerBuilder::with_meta_extractor(
                    io,
                    move |req: &hyper::Request<hyper::Body>| {
                        let xbigtable = req.headers().get("x-bigtable");
                        if xbigtable.is_some_and(|v| v == "disabled") {
                            request_processor.clone_without_bigtable()
                        } else {
                            request_processor.clone()
                        }
                    },
                )
                .event_loop_executor(runtime.handle().clone())
                .threads(1)
                .cors(DomainsValidation::AllowOnly(vec![
                    AccessControlAllowOrigin::Any,
                ]))
                .cors_max_age(86400)
                .request_middleware(request_middleware)
                .max_request_body_size(max_request_body_size)
                .start_http(&rpc_addr);
```
