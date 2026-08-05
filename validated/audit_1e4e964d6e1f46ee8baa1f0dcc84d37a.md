## Title
No Rate Limiting on JSON-RPC HTTP Endpoint Allows Single-Client Resource Exhaustion - (File: rpc/src/rpc_service.rs)

## Summary
The JSON-RPC HTTP server (`JsonRpcService`) accepts and processes requests from any client without any per-IP or global rate limiting, unlike every other unprivileged-facing ingress path in Agave (QUIC/TPU, gossip, repair). This mirrors the external report's "Lack of Rate Limiting" finding almost exactly: a POST-based JSON-RPC endpoint that allows unlimited parallel/rapid requests, enabling a single low-rate/unprivileged client to degrade or exhaust validator RPC resources.

## Finding Description
`JsonRpcService::new` builds the JSON-RPC HTTP server using `jsonrpc_http_server::ServerBuilder`, configuring CORS, `request_middleware`, and `max_request_body_size`, but applying no connection or request-rate throttling of any kind: [1](#0-0) 

The server is started with a single event-loop thread (`.threads(1)`) and no per-IP token bucket, no concurrent-connection cap, and no request-per-second budget: [2](#0-1) 

Compare this to every other unprivileged network surface in the codebase, all of which explicitly implement rate limiting to defend against exactly this bug class:
- QUIC/TPU ingress uses a per-IP `ConnectionRateLimiter` (`TokenBucket`-backed) plus a global `overall_connection_rate_limiter`, connection caps per peer, and per-connection stream throttling: [3](#0-2) [4](#0-3) 
- Gossip pull requests are metered via a `KeyedRateLimiter<IpAddr>` scan budget: [5](#0-4) 
- The repair `serve_repair` listener enforces a `TokenBucket`-based bytes-per-second data budget and a max-requests-per-iteration cap: [6](#0-5) 

The JSON-RPC HTTP path has no equivalent construct. `RpcRequestMiddleware` only handles snapshot/genesis file serving and health redirects — it performs no throttling: [7](#0-6) 

The corrupted/missing value is the absence of any per-source-IP or global request/connection budget analogous to `solana_net_utils::token_bucket::TokenBucket`/`KeyedRateLimiter` used elsewhere in the codebase — existing guards (`max_request_body_size`, CORS policy) bound payload size and origin, but do nothing to bound request *rate* or concurrent connection count from a single client.

## Impact Explanation
An unauthenticated remote client can open many concurrent HTTP connections or issue requests at a high rate to the JSON-RPC endpoint (default port 8899) with no server-side throttling to reject or delay them. Because the JSON-RPC server runs its event loop on a single thread (`.threads(1)`) shared across all RPC methods, and expensive calls (e.g., account scans, `getProgramAccounts`, block/transaction lookups) execute against shared `BankForks`/`Blockstore` state, a flood of requests can saturate CPU, memory, and I/O, degrading RPC responsiveness for all other clients or crashing the process — a "single-client low-rate RPC crash/degradation" scenario explicitly within the accepted impact scope for this analysis.

## Likelihood Explanation
High likelihood: no authentication, no allow-list, and no rate limiter are required to reach the vulnerable path — the JSON-RPC HTTP endpoint is intentionally publicly reachable by design (that's its purpose), and the reporter's own PoC targeting a live JSON-RPC endpoint (`/v1/gno` style POST) demonstrates the trivial reproduction of this bug class in practice.

## Recommendation
Introduce request/connection throttling on the JSON-RPC HTTP server analogous to the existing `solana_net_utils::token_bucket::{TokenBucket, KeyedRateLimiter}` infrastructure already used in `streamer`, `gossip`, and `core::repair`:
- Add a per-source-IP `KeyedRateLimiter` (or an equivalent `tower`/hyper middleware) inside `RpcRequestMiddleware::on_request` or as a wrapping `ServerBuilder` layer, rejecting/delaying requests once a configurable per-IP budget is exhausted.
- Add a global token bucket bounding total request throughput to protect shared resources (bank/blockstore locks) regardless of source diversity.
- Return `429 Too Many Requests` with a `Retry-After` header (the RPC/pubsub *clients* already handle this response per `rpc-client/src/http_sender.rs` and `pubsub-client/src/pubsub_client.rs`, but the server never sends it), so existing client retry logic can be exercised.
- Make the limits configurable via validator CLI flags, consistent with `--tpu-max-connections-per-ipaddr-per-minute` style options already present for QUIC/TPU.

## Proof of Concept
1. Start a validator with default `JsonRpcConfig` (RPC enabled on port 8899).
2. From a single unauthenticated client, open a large number of concurrent HTTP connections (or issue requests in a tight loop) against the JSON-RPC endpoint calling an expensive method (e.g., `getProgramAccounts` or repeated cheap methods at high volume) — no rate limiting rejects any of them, unlike the QUIC/TPU path where `ConnectionRateLimiter::is_allowed` (streamer/src/nonblocking/connection_rate_limiter.rs:34) or the gossip `pull_request_budget` (gossip/src/cluster_info.rs:1705) would begin rejecting excess traffic.
3. Because the server event loop runs on a single thread (`rpc/src/rpc_service.rs:736`, `.threads(1)`) and no throttling gate exists in `RpcRequestMiddleware` (`rpc/src/rpc_service.rs:126-156`) or `ServerBuilder` configuration (`rpc/src/rpc_service.rs:718-743`), sustained/bursty single-client traffic degrades or exhausts RPC service responsiveness for all clients.

*Note: I was unable to fully verify whether any additional rate-limiting middleware might be layered in front of the RPC server at deployment time (e.g., via reverse proxy) since that is outside the indexed codebase; this finding is scoped strictly to what `rpc_service.rs` itself enforces.*

### Citations

**File:** rpc/src/rpc_service.rs (L126-156)
```rust
struct RpcRequestMiddleware {
    ledger_path: PathBuf,
    full_snapshot_archive_path_regex: Regex,
    incremental_snapshot_archive_path_regex: Regex,
    snapshot_config: Option<SnapshotConfig>,
    bank_forks: Arc<RwLock<BankForks>>,
    health: Arc<RpcHealth>,
}

impl RpcRequestMiddleware {
    pub fn new(
        ledger_path: PathBuf,
        snapshot_config: Option<SnapshotConfig>,
        bank_forks: Arc<RwLock<BankForks>>,
        health: Arc<RpcHealth>,
    ) -> Self {
        Self {
            ledger_path,
            full_snapshot_archive_path_regex: Regex::new(
                snapshot_paths::FULL_SNAPSHOT_ARCHIVE_FILENAME_REGEX,
            )
            .unwrap(),
            incremental_snapshot_archive_path_regex: Regex::new(
                snapshot_paths::INCREMENTAL_SNAPSHOT_ARCHIVE_FILENAME_REGEX,
            )
            .unwrap(),
            snapshot_config,
            bank_forks,
            health,
        }
    }
```

**File:** rpc/src/rpc_service.rs (L718-743)
```rust
                let request_middleware = RpcRequestMiddleware::new(
                    ledger_path,
                    snapshot_config,
                    bank_forks,
                    health.clone(),
                );
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

**File:** streamer/src/nonblocking/connection_rate_limiter.rs (L6-29)
```rust
/// Limits the rate of connections per IP address.
pub struct ConnectionRateLimiter {
    limiter: KeyedRateLimiter<IpAddr>,
}

/// The threshold of the size of the connection rate limiter map. When
/// the map size is above this, we will trigger a cleanup of older
/// entries used by past requests.
const CONNECTION_RATE_LIMITER_CLEANUP_SIZE_THRESHOLD: usize = 100_000;

impl ConnectionRateLimiter {
    /// Create a new rate limiter per IpAddr. The rate is specified as the count per minute to allow for
    /// less frequent connections. Higher limit also allows higher bursts.
    /// num_shards controls how many shards are used in the underlying dashmap,
    /// should be set >= number of contending threads.
    pub fn new(limit_per_minute: u64, max_burst: u64, num_shards: usize) -> Self {
        Self {
            limiter: KeyedRateLimiter::new(
                CONNECTION_RATE_LIMITER_CLEANUP_SIZE_THRESHOLD,
                TokenBucket::new(limit_per_minute, max_burst, limit_per_minute as f64 / 60.0),
                num_shards,
            ),
        }
    }
```

**File:** streamer/src/nonblocking/quic.rs (L346-369)
```rust
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
```

**File:** gossip/src/cluster_info.rs (L1705-1721)
```rust
    fn try_consume_pull_request_scan_budget(
        &self,
        request: &PullRequest,
        scan_entries: usize,
    ) -> bool {
        let cost = pull_request_scan_cost(scan_entries, request.filter.bloom_hash_count());
        if self
            .pull_request_budget
            .consume_tokens(request.addr.ip(), cost)
            .is_ok()
        {
            true
        } else {
            self.stats.pull_request_scan_budget_exhausted.add_relaxed(1);
            false
        }
    }
```

**File:** core/src/repair/serve_repair.rs (L1384-1411)
```rust
        const MAX_BYTES_PER_SECOND: u64 = 12_000_000;

        let mut ping_cache = PingCache::new(
            REPAIR_PING_CACHE_TTL,
            REPAIR_PING_CACHE_OUTSTANDING_PING_TIMEOUT_MS,
            REPAIR_PING_CACHE_CAPACITY,
        );

        let recycler = PacketBatchRecycler::default();
        Builder::new()
            .name("solRepairListen".to_string())
            .spawn(move || {
                let mut last_print = Instant::now();
                let mut stats = ServeRepairStats::default();
                let data_budget = TokenBucket::new(
                    MAX_BYTES_PER_SECOND,
                    MAX_BYTES_PER_SECOND,
                    MAX_BYTES_PER_SECOND as f64,
                );
                while !exit.load(Ordering::Relaxed) {
                    let result = self.run_listen(
                        &mut ping_cache,
                        &recycler,
                        &requests_receiver,
                        &response_sender,
                        &mut stats,
                        &data_budget,
                    );
```
