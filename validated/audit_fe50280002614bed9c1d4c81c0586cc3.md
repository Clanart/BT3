Audit Report

## Title
Unbounded WebSocket Handshake Read in RPC PubSub Server Enables Slowloris-Style Resource Exhaustion - (rpc/src/rpc_pubsub_service.rs)

## Summary
`listen()` accepts every incoming TCP connection on the RPC PubSub port and spawns a task running `handle_connection()`, which calls `server.receive_request().await?` to perform the WebSocket handshake with no read deadline. [1](#0-0)  A remote client can open a TCP connection, withhold or trickle the handshake bytes, and keep the spawned task and its socket alive indefinitely, since nothing bounds this await.

## Finding Description
`listen()` binds the listener and on each accepted connection immediately spawns `handle_connection` with only a metrics `TokenCounter` attached — no per-connection read timeout or connection admission limit is applied. [2](#0-1)  Inside `handle_connection`, `Server::new(socket.compat())` followed by `server.receive_request().await?` reads the HTTP-style WebSocket upgrade request. [3](#0-2)  There is no `tokio::time::timeout`/`timeout_at` wrapping this call anywhere in the file, and no `max_connections`/`Semaphore`/rate-limiting logic exists in `rpc_pubsub_service.rs` to bound the number of half-open handshakes. This is the same bug class as a missing `ReadHeaderTimeout`: a client that connects but never completes the handshake ties up a tokio task and a socket file descriptor for as long as it holds the connection open. The codebase already has a working mitigation pattern for exactly this kind of exposure in `net-utils/src/ip_echo_server.rs`, which wraps handshake reads in `timeout_at(deadline, ...)` with a 5-second `IO_TIMEOUT` and caps `MAX_CONCURRENT_CONNECTIONS`, confirming this is an inconsistency/gap rather than a considered design choice.

## Impact Explanation
Any unauthenticated remote client that can reach the exposed RPC PubSub port can open many TCP connections and stall each handshake indefinitely, each occupying a spawned tokio task and OS socket resource with no reclaiming timeout. This is a single-client, low-rate resource exhaustion vector against the RPC/PubSub subsystem, matching the "single-client low-rate RPC crash/degradation" and "non-RPC remote exhaustion" categories: it can exhaust file descriptors or the PubSub tokio runtime's task/memory budget, degrading or crashing PubSub service for legitimate clients, without needing any validator/peer privilege or key material.

## Likelihood Explanation
Likelihood is high for any validator/RPC operator exposing the PubSub WebSocket endpoint publicly, which is a common supported configuration. The attack requires only the ability to open a TCP connection and withhold/slow-send a handful of bytes — trivially scriptable, unauthenticated, and repeatable with no rate limiting or timeout in the current code to counter it.

## Recommendation
Wrap `server.receive_request()` (and ideally the subsequent `send_response`) in `tokio::time::timeout`/`timeout_at` with a bounded duration, mirroring `IO_TIMEOUT` in `net-utils/src/ip_echo_server.rs`, dropping the connection on expiry. Additionally, consider capping concurrent pre-handshake/unauthenticated connections, similar to `MAX_CONCURRENT_CONNECTIONS` in `ip_echo_server.rs`.

## Proof of Concept
1. Start a validator with `rpc_pubsub_service::listen` bound and reachable (`rpc/src/rpc_pubsub_service.rs` lines 429-447).
2. From an attacker host, open N TCP connections to the pubsub listen address.
3. For each connection, send no bytes or only a partial WebSocket handshake request line, and hold the socket open.
4. Since `receive_request().await?` (line 356) has no timeout, each `tokio::spawn`ed task (lines 458-467) parks indefinitely, retaining its socket and task resources.
5. Repeat until the validator's file-descriptor limit or tokio task/memory ceiling for the PubSub runtime is exhausted, causing new legitimate PubSub connections to fail or the service to degrade — achievable from a single unprivileged client.

### Citations

**File:** rpc/src/rpc_pubsub_service.rs (L349-361)
```rust
async fn handle_connection(
    socket: TcpStream,
    subscription_control: SubscriptionControl,
    config: PubSubConfig,
    mut tripwire: Tripwire,
) -> Result<(), Error> {
    let mut server = Server::new(socket.compat());
    let request = server.receive_request().await?;
    let accept = server::Response::Accept {
        key: request.key(),
        protocol: None,
    };
    server.send_response(&accept).await?;
```

**File:** rpc/src/rpc_pubsub_service.rs (L429-467)
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
```
