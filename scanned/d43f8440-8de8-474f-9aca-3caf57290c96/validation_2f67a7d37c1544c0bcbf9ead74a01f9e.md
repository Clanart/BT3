## Analysis

The Slowloris report is about an HTTP server that never times out while waiting for a client to finish sending its request headers, letting a single slow/partial connection occupy server resources indefinitely. The closest Agave analog is the JSON-RPC PubSub (WebSocket) server in `rpc/src/rpc_pubsub_service.rs`.

### Title
Unbounded WebSocket Handshake Read in RPC PubSub Server Enables Slowloris-Style Resource Exhaustion - (rpc/src/rpc_pubsub_service.rs)

### Summary
`listen()` accepts every incoming TCP connection and spawns a task running `handle_connection()`, which immediately calls `server.receive_request().await?` to perform the WebSocket handshake with no deadline attached. [1](#0-0)  Unlike the sibling `net-utils/src/ip_echo_server.rs`, which wraps every read/write in a `timeout_at(deadline, ...)` bounded by a 5-second `IO_TIMEOUT`, [2](#0-1) [3](#0-2)  the PubSub handler has no equivalent guard around the initial handshake read.

### Finding Description
`listen()` binds the pubsub TCP listener and, on each accepted connection, spawns `handle_connection` without imposing any per-connection admission or read-timeout policy beyond a metrics `TokenCounter`. [4](#0-3)  Inside `handle_connection`, `Server::new(socket.compat())` followed by `server.receive_request().await?` reads the HTTP-style WebSocket upgrade request from the socket. [5](#0-4)  This await has no surrounding `tokio::time::timeout`/`timeout_at`, so a client that opens the TCP connection and then sends the handshake bytes at a trickle (or not at all) keeps the spawned tokio task, its socket file descriptor, and any associated buffers alive indefinitely.

This mirrors exactly the "missing `ReadHeaderTimeout`" bug class from the report: absence of a deadline on reading request headers/handshake data allows an attacker to hold connections open cheaply. The existing mitigation pattern that Agave already applies elsewhere in the codebase (`IO_TIMEOUT` in `ip_echo_server.rs`) is not applied here, showing the gap is not a deliberate design choice but an inconsistency.

### Impact Explanation
An unprivileged remote client can open many TCP connections to the RPC PubSub port and stall each handshake indefinitely. Because each connection spawns an unbounded async task and holds an OS socket/file descriptor with no timeout to reclaim it, an attacker with a low-rate, single-client connection flood can exhaust the validator's file-descriptor limit or tokio task/memory budget dedicated to the PubSub runtime, degrading or crashing the RPC subsystem. This matches the "single-client low-rate RPC crash/degradation" and "non-RPC remote exhaustion" categories under valid impact, without requiring a malicious peer/validator role or leaked keys—any TCP client reaching the exposed PubSub port suffices.

### Likelihood Explanation
Likelihood is high for any validator that exposes the RPC PubSub endpoint publicly (a common and supported configuration). The attack requires no authentication, no valid Solana keys, and no protocol-level trust—only the ability to open a TCP connection to the PubSub listener and withhold/slowly send the handshake bytes, which is trivial to script.

### Recommendation
Wrap `server.receive_request()` (and ideally the whole handshake/response send sequence) in `tokio::time::timeout` with a bounded duration, matching the pattern already used in `net-utils/src/ip_echo_server.rs`'s `IO_TIMEOUT`/`timeout_at` mechanism. [2](#0-1)  On timeout, drop the connection. Additionally, consider bounding total concurrent unauthenticated/pre-handshake connections similar to `MAX_CONCURRENT_CONNECTIONS` in `ip_echo_server.rs`. [6](#0-5) 

### Proof of Concept
1. Start a validator with the RPC PubSub service enabled and reachable.
2. From an attacker host, open N TCP connections to the pubsub port (`SocketAddr` bound in `listen()`). [7](#0-6) 
3. For each connection, send only a few bytes of the WebSocket handshake request (or none at all), then hold the socket open without completing the request line/headers that `server.receive_request()` expects to parse.
4. Because `receive_request().await?` at line 356 has no timeout, each spawned task in `tokio::spawn(async move { handle_connection(...) })` (around line 458) remains parked indefinitely, retaining its socket and task resources.
5. Repeat until the validator's file-descriptor limit or tokio task/memory ceiling for the PubSub runtime is reached, causing new legitimate PubSub connections to fail or the service to degrade/crash—achieved from a single low-privileged client.

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

**File:** rpc/src/rpc_pubsub_service.rs (L429-461)
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
```

**File:** net-utils/src/ip_echo_server.rs (L31-34)
```rust
const IO_TIMEOUT: Duration = Duration::from_secs(5);
// Non-loopback peers are limited to one active connection each; loopback is exempt.
const MAX_CONCURRENT_CONNECTIONS: usize = 2048;

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
