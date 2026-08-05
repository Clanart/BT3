## Title
Unbounded WebSocket connection acceptance in `PubSubService::listen` allows unauthenticated remote resource exhaustion - (File: rpc/src/rpc_pubsub_service.rs)

### Summary
The public pubsub TCP/WebSocket listener in `listen()` accepts every incoming connection unconditionally and spawns a Tokio task per connection. The only "tracking" mechanism is `TokenCounter::create_token()`, which is purely a metrics/telemetry counter (it emits a `datapoint_info!` on token creation/drop) and performs no capacity check, no rejection, and no backpressure.

### Finding Description
`listen()` binds the pubsub TCP listener and loops on `listener.accept()`. For every accepted socket it clones the config/subscription control, creates a `counter_token = counter.create_token()`, and unconditionally spawns `handle_connection` as a new tokio task: [1](#0-0) 

`TokenCounter::create_token` only records a strong-count-based metric via `datapoint_info!`; it never returns an error or blocks and cannot refuse issuance: [2](#0-1) 

There is no cap on the number of concurrent websocket connections anywhere in `PubSubConfig` — the config only exposes `max_active_subscriptions`, queue capacity, worker thread count, and notification thread count, with no `max_connections` or per-IP connection cap field: [3](#0-2) 

By contrast, subscription creation *is* actually capped via `SubscriptionControl::subscribe`, which reserves a slot through `SubscriberCountGuard::try_reserve` and returns `Error::TooManySubscriptions` when `max_active_subscriptions` is exceeded: [4](#0-3) 

This shows the codebase does implement real enforcement for subscription counts, but the analogous protection is absent at the raw TCP/WebSocket-connection-acceptance layer in `listen()`. Each accepted connection performs a full WebSocket handshake (`Server::new`, `receive_request`, `send_response`), allocates buffers (4096-byte max frame/message size), a `broadcast_receiver`, a `DashMap` for `current_subscriptions`, and an `IoHandler`, all before any subscription-count limiting logic is ever reached: [5](#0-4) 

An unprivileged remote attacker can therefore open an effectively unbounded number of TCP connections against the pubsub port, each consuming a Tokio task, socket file descriptor, and per-connection memory, without ever needing to issue a subscription (which is the only place a limit exists).

### Impact Explanation
This is a resource-exhaustion / denial-of-service vector against the validator's public pubsub RPC endpoint: an attacker can exhaust file descriptors, task-scheduler capacity, and memory on the validator process by opening many concurrent WebSocket connections, degrading or crashing the RPC/pubsub service for legitimate clients on that node. It does not directly enable fund theft, false execution, or consensus impact, and it targets a single RPC-facing service rather than the core validator/gossip/TPU/consensus paths.

### Likelihood Explanation
High from a mechanical standpoint (no auth, no rate limiting, no connection cap — any TCP client can trigger it), but the resulting impact is bounded by OS-level limits (file descriptor ulimits, ephemeral port exhaustion on the attacker side, and the fact that pubsub is typically deployed behind operator-controlled network configuration/load balancers). This pattern (single endpoint accepting unlimited raw TCP connections with only a metrics counter) is a common, expected characteristic of RPC-style services and is typically mitigated operationally (firewalling, OS ulimits, reverse proxies) rather than in-protocol, and the affected component is the RPC/pubsub port rather than validator consensus, gossip, or TPU/QUIC paths that have dedicated connection/rate limiting per the program's threat model.

### Recommendation
Add an explicit, configurable maximum concurrent-connection limit (and ideally a per-IP limit) to `PubSubConfig`, and reject/close new connections in `listen()` once the cap is reached, mirroring the pattern already used by `SubscriberCountGuard::try_reserve` for subscriptions. Convert `TokenCounter` usage at the connection layer from a passive metrics-only counter into an active gate (e.g., an `AtomicUsize` counter checked before spawning `handle_connection`, or a `tokio::sync::Semaphore` sized to the configured connection cap).

### Proof of Concept
1. Start a validator with the public pubsub websocket listener enabled on `pubsub_addr`.
2. From an unprivileged client, open N (e.g., tens of thousands) of raw TCP connections to the pubsub address, completing only the WebSocket handshake (or even partially) without sending any subscription requests.
3. Observe that `listener.accept()` in `listen()` accepts every connection and spawns a task for each — no request is ever rejected due to connection count, since `TokenCounter::create_token()` performs no admission control (see `metrics/src/lib.rs` lines 34-48 and `rpc/src/rpc_pubsub_service.rs` lines 448-468).
4. Continue opening connections until the validator process exhausts file descriptors or memory, degrading pubsub availability for legitimate subscribers.

Note: I was unable to fully verify whether any external layer (e.g., OS-level `ulimit`, a reverse proxy, or firewall configuration typically recommended in Agave's validator deployment docs) is assumed to mitigate this in production deployments, since that configuration lives outside the indexed source and would require operator-side verification.

### Citations

**File:** rpc/src/rpc_pubsub_service.rs (L33-49)
```rust
pub const DEFAULT_MAX_ACTIVE_SUBSCRIPTIONS: usize = 1_000_000;
pub const DEFAULT_QUEUE_CAPACITY_ITEMS: usize = 10_000_000;
pub const DEFAULT_TEST_QUEUE_CAPACITY_ITEMS: usize = 1000;
pub const DEFAULT_QUEUE_CAPACITY_BYTES: usize = 256 * 1024 * 1024;
pub const DEFAULT_TEST_QUEUE_CAPACITY_BYTES: usize = 16 * 1024 * 1024;
pub const DEFAULT_WORKER_THREADS: usize = 1;

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

**File:** rpc/src/rpc_pubsub_service.rs (L349-378)
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

**File:** rpc/src/rpc_pubsub_service.rs (L448-468)
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
```

**File:** metrics/src/lib.rs (L34-48)
```rust
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
```

**File:** rpc/src/rpc_subscription_tracker.rs (L219-230)
```rust
    pub fn subscribe(&self, params: SubscriptionParams) -> Result<SubscriptionToken, Error> {
        debug!(
            "Total existing subscriptions: {}",
            self.0.subscriptions.len()
        );
        // Reserve a subscriber slot up front. The cap is per live token holder,
        // not per deduplicated upstream stream, so duplicate subscribes to the
        // same params each consume their own slot.
        let subscriber_guard = SubscriberCountGuard::try_reserve(&self.0).ok_or_else(|| {
            inc_new_counter_info!("rpc-subscription-refused-limit-reached", 1);
            Error::TooManySubscriptions
        })?;
```
