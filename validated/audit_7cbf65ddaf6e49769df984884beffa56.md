Audit Report

## Title
No Rate Limiting on JSON-RPC HTTP Endpoint Allows Single-Client Resource Exhaustion - (File: rpc/src/rpc_service.rs)

## Summary
`JsonRpcService::new` constructs the JSON-RPC HTTP server via `jsonrpc_http_server::ServerBuilder` with only CORS, a request middleware for snapshot/genesis serving, and a max body size cap — no per-IP or global request/connection rate limiter is configured anywhere in this path. [1](#0-0)  This is inconsistent with every other unprivileged ingress surface in the codebase (QUIC/TPU, gossip, repair), all of which implement explicit token-bucket/keyed rate limiting, leaving the RPC HTTP endpoint (default port 8899) open to unthrottled single-client request floods.

## Finding Description
The `RpcRequestMiddleware` struct and its `on_request`-related logic only handle snapshot/genesis file serving and health redirect logic; it contains no throttling construct. [2](#0-1)  The `ServerBuilder` chain configures `.event_loop_executor`, `.threads(1)`, `.cors`, `.cors_max_age`, `.request_middleware`, and `.max_request_body_size`, but no rate-limiting layer, and a search of the `rpc/src` tree for rate-limiter primitives (`TokenBucket`, `KeyedRateLimiter`, `429`, etc.) returns no matches. [3](#0-2) 

This contrasts with other unprivileged network paths in the codebase that all implement explicit per-IP and/or global rate limiting: QUIC/TPU's `ConnectionRateLimiter` backed by `TokenBucket`, gossip's `KeyedRateLimiter<IpAddr>` pull-request budget, and repair's `TokenBucket`-based data budget in `serve_repair`. The JSON-RPC HTTP path has no equivalent guard — `max_request_body_size` and CORS bound payload size and origin policy respectively, but neither bounds request rate or concurrent connections from a single source.

## Impact Explanation
Because the server has no per-IP or global throttling, an unauthenticated client can issue requests (including expensive calls such as `getProgramAccounts` which scan `bank_forks`/`Blockstore` state) at unbounded rate/concurrency. This falls within the accepted "single-client low-rate RPC crash/degradation" impact category, since sustained request volume against a JSON-RPC server configured with only a single event-loop thread pool (`.threads(1)`) can degrade responsiveness for other RPC clients sharing the same validator resources. [4](#0-3) 

## Likelihood Explanation
High likelihood: the JSON-RPC HTTP endpoint is, by design, an intentionally public-facing interface with no authentication gate before reaching `MetaIoHandler`/`RpcRequestMiddleware`, so an unprivileged remote client needs no special access to trigger unbounded request volume against it. [5](#0-4) 

## Recommendation
Add per-source-IP and/or global request/connection throttling to the JSON-RPC HTTP server, mirroring the `TokenBucket`/`KeyedRateLimiter` infrastructure already used in `streamer`, `gossip`, and `core::repair`. This can be implemented as a wrapping middleware around `RpcRequestMiddleware::on_request` (or an outer `hyper`/`tower` layer) that rejects/delays requests once a configurable per-IP or global budget is exhausted, returning `429 Too Many Requests` with `Retry-After`, and expose the limits via validator CLI flags analogous to existing QUIC/TPU per-IP connection-rate options.

## Proof of Concept
1. Start a validator with default `JsonRpcConfig` (RPC enabled on port 8899), using the `ServerBuilder` configuration shown at `rpc/src/rpc_service.rs:724-743`.
2. From a single unauthenticated client, issue a high volume of concurrent/rapid HTTP POST requests against the JSON-RPC endpoint (e.g., repeated `getProgramAccounts` calls).
3. Observe that no component in `RpcRequestMiddleware` (`rpc/src/rpc_service.rs:126-224`) or the `ServerBuilder` chain rejects or throttles the traffic — unlike QUIC/TPU's `ConnectionRateLimiter::is_allowed` or gossip's `try_consume_pull_request_scan_budget`, which would begin rejecting excess traffic from the same source.

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

**File:** rpc/src/rpc_service.rs (L703-743)
```rust
        let thread_hdl = Builder::new()
            .name("solJsonRpcSvc".to_string())
            .spawn(move || {
                renice_this_thread(rpc_niceness_adj).unwrap();

                let mut io = MetaIoHandler::default();

                io.extend_with(rpc_minimal::MinimalImpl.to_delegate());
                if full_api {
                    io.extend_with(rpc_bank::BankDataImpl.to_delegate());
                    io.extend_with(rpc_accounts::AccountsDataImpl.to_delegate());
                    io.extend_with(rpc_accounts_scan::AccountsScanImpl.to_delegate());
                    io.extend_with(rpc_full::FullImpl.to_delegate());
                }

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
