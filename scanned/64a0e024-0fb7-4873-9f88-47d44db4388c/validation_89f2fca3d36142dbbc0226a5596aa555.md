## Finding: Unbounded, timeout-free connection accept loop in Solana Faucet enables remote resource exhaustion

### Title
Faucet `run_faucet`/`process` accept unbounded, timeout-free TCP connections leading to remote resource (task/fd) exhaustion - (`faucet/src/faucet.rs`)

### Summary
`run_faucet` accepts every incoming TCP connection and spawns an unbounded Tokio task per connection with no read timeout, no per-IP connection limit, and no cap on concurrent connections, unlike the analogous `ip_echo_server` in the same codebase which enforces `MAX_CONCURRENT_CONNECTIONS` and an `IO_TIMEOUT` deadline on all I/O.

### Finding Description
`run_faucet` binds a `TcpListener` and loops on `listener.accept().await`, spawning a new Tokio task for every accepted connection with no bound: [1](#0-0) 

Each spawned task runs `process`, which calls `stream.read_exact(&mut request)` in a loop with **no timeout** and no bound on how long the connection can remain open before sending the expected number of bytes: [2](#0-1) 

There is no per-IP connection cap, no global concurrent-connection cap, and no `tokio::time::timeout(...)` wrapping the `read_exact` call. An unprivileged remote client can open many TCP connections to the faucet port and either send data extremely slowly or never send the full request buffer at all. Each such connection pins a Tokio task and a socket file descriptor indefinitely, since `read_exact` will simply await forever on a connection that trickles bytes or stalls.

This is directly analogous to the reported bug class (HTTP client `makeApiCall` with no `Timeout`/`DialContext` timeouts, allowing connections to "hang indefinitely, leading to goroutine leaks... crash or become unresponsive"). Here the corrupted invariant is symmetric: the *server* accept/read path has no timeout guard, so the "connection lifetime" is unbounded and attacker-controlled.

The codebase demonstrates the fix pattern already exists elsewhere: `net-utils/src/ip_echo_server.rs`'s `process_connection` computes a `deadline` via `IO_TIMEOUT` and wraps every read/write in `timeout_at(deadline, ...)`, and `run_echo_server` additionally caps `MAX_CONCURRENT_CONNECTIONS` and limits one connection per IP: [3](#0-2) [4](#0-3) 

`run_faucet`/`process` implements none of these guards.

### Impact Explanation
Any unprivileged network client that can reach the faucet TCP port (default `FAUCET_PORT = 9900`, typically bound to devnet/testnet validator hosts or faucet services) can open many slow/stalled connections. Each connection consumes a Tokio task plus a kernel socket file descriptor with no expiry. Sustained low-rate connection opening (well below any rate limiter, since none exists here) exhausts file descriptors / task memory on the host running the faucet, degrading or crashing the faucet process and potentially other colocated services sharing the same process/host resource limits. This matches the "non-RPC remote exhaustion/crash" impact category — it is not an RPC endpoint, requires no privileged access, and does not assume a malicious validator/peer, only a network client capable of opening TCP connections.

### Likelihood Explanation
Likelihood is high for any deployment exposing the faucet port to the public network (a common devnet/testnet configuration), since exploitation requires only opening TCP connections and withholding/trickling data — no authentication, no special protocol knowledge beyond a raw TCP connect, and no cryptographic or consensus prerequisites. It is a straightforward Slowloris-style attack against a service that already ships in this repository.

### Recommendation
Apply the same mitigations already used in `net-utils/src/ip_echo_server.rs`:
1. Wrap the `stream.read_exact` (and `write_all`) calls in `process` with `tokio::time::timeout`/`timeout_at` against a fixed per-request deadline, closing the connection on timeout.
2. Cap the number of concurrent connections (globally and/or per source IP), similar to `MAX_CONCURRENT_CONNECTIONS` and the per-IP `active_ips` tracking used in `ip_echo_server.rs`, before spawning a task for an accepted connection in `run_faucet`.
3. Consider bounding the total number of `tokio::spawn`ed faucet-processing tasks to avoid unbounded task growth even for well-formed but numerous connections.

### Proof of Concept
1. Start the faucet: `solana-faucet` binds `run_faucet` to `FAUCET_PORT` (9900) via `TcpListener::bind`.
2. From an attacker-controlled host (no special privileges required), open N TCP connections to the faucet port and, for each, either send 0 bytes or send 1 byte every few seconds (never completing the `serialized_size(&FaucetRequest::GetAirdrop{..})`-sized request buffer read in `process`).
3. Each connection causes `run_faucet`'s accept loop to `tokio::spawn` a task that blocks forever inside `stream.read_exact(&mut request).await` (`faucet/src/faucet.rs:459`), since there is no timeout on the read and no cap on connections.
4. Repeating this at a low, sustained rate accumulates open sockets/tasks without bound, eventually exhausting file descriptors or memory on the faucet host, causing the faucet service (and potentially colocated processes) to become unresponsive or crash — with no elevated privileges or validator/peer trust required by the attacker.

### Citations

**File:** faucet/src/faucet.rs (L431-443)
```rust
    loop {
        let faucet = faucet.clone();
        match listener.accept().await {
            Ok((stream, _)) => {
                tokio::spawn(async move {
                    if let Err(e) = process(stream, faucet).await {
                        info!("failed to process request; error = {e:?}");
                    }
                });
            }
            Err(e) => debug!("failed to accept socket; error = {e:?}"),
        }
    }
```

**File:** faucet/src/faucet.rs (L446-486)
```rust
async fn process(
    mut stream: TokioTcpStream,
    faucet: Arc<Mutex<Faucet>>,
) -> Result<(), Box<dyn std::error::Error>> {
    let mut request = vec![
        0u8;
        serialized_size(&FaucetRequest::GetAirdrop {
            lamports: u64::default(),
            to: Pubkey::default(),
            blockhash: Hash::default(),
        })
        .unwrap() as usize
    ];
    while stream.read_exact(&mut request).await.is_ok() {
        trace!("{request:?}");

        let response = {
            match stream.peer_addr() {
                Err(e) => {
                    info!("{:?}", e.into_inner());
                    ERROR_RESPONSE.to_vec()
                }
                Ok(peer_addr) => {
                    let ip = peer_addr.ip();
                    info!("Request IP: {ip:?}");

                    match faucet.lock().unwrap().process_faucet_request(&request, ip) {
                        Ok(response_bytes) => {
                            trace!("Airdrop response_bytes: {response_bytes:?}");
                            response_bytes
                        }
                        Err(e) => {
                            info!("Error in request: {e}");
                            ERROR_RESPONSE.to_vec()
                        }
                    }
                }
            }
        };
        stream.write_all(&response).await?;
    }
```

**File:** net-utils/src/ip_echo_server.rs (L86-101)
```rust
async fn process_connection(
    mut socket: TcpStream,
    peer_addr: SocketAddr,
    shred_version: Option<u16>,
) -> io::Result<()> {
    info!("connection from {peer_addr:?}");
    let deadline = Instant::now()
        .checked_add(IO_TIMEOUT)
        .ok_or_else(|| io::Error::other("failed to compute request deadline"))?;

    let mut data = vec![0u8; ip_echo_server_request_length()];

    let mut writer = {
        let (mut reader, writer) = socket.split();
        let _ = timeout_at(deadline, reader.read_exact(&mut data)).await??;
        writer
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
