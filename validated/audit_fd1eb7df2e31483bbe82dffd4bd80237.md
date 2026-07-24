The code confirms the claim. Here is the analysis:

**TCP branch** wraps the service in `InterceptedService` with `OnlyAggregatorAndSelf` (when `client_verification = true`): [1](#0-0) 

**Unix branch** calls `.add_service(service)` directly — no `InterceptedService`, no interceptor: [2](#0-1) 

The `OnlyAggregatorAndSelf` interceptor enforces cert-pinned auth — it rejects requests whose TLS leaf cert does not match the aggregator's or the server's own cert: [3](#0-2) 

Because the Unix branch never wraps the service in `InterceptedService`, the interceptor's `call()` method is never invoked for any request arriving on the Unix socket. The `peer_certs` check at line 41 is never reached.

---

### Title
Unix-socket gRPC branch skips `InterceptedService` interceptor, bypassing `OnlyAggregatorAndSelf` cert-pinned auth — (`core/src/servers.rs`)

### Summary
`create_grpc_server` has two branches. The TCP branch wraps the service in `InterceptedService::new(service, OnlyAggregatorAndSelf { … })` when `client_verification = true`, enforcing that only the aggregator or the server itself may call privileged RPCs. The Unix-socket branch calls `.add_service(service)` directly, with no interceptor applied at all. Any local process that can reach the Unix socket can call any operator or verifier RPC without presenting a certificate.

### Finding Description
In `core/src/servers.rs`, the `ServerAddr::Unix` match arm builds a `tonic::transport::Server` and calls `.add_service(service)` on the raw, unwrapped service. [4](#0-3) 

The TCP arm, by contrast, first constructs `InterceptedService::new(service, OnlyAggregatorAndSelf { aggregator_cert, our_cert })` and only then passes the wrapped service to `.add_service(...)`. [5](#0-4) 

The `OnlyAggregatorAndSelf` interceptor's `call()` implementation rejects any request whose TLS peer leaf certificate does not match the pinned aggregator or self certificate, and in non-test mode rejects requests with no peer certs at all. [6](#0-5) 

Because the Unix branch never instantiates `InterceptedService`, `Interceptors::call()` is never invoked, so the cert check is entirely absent for Unix-socket-bound servers.

### Impact Explanation
An unprivileged local process (no TLS identity, no aggregator certificate) can connect to the Unix socket and call any operator or verifier RPC — including deposit signing sessions, challenge initiation, and collateral-touching flows — without authentication. This enables:
- Unauthorized deposit signature collection, allowing forged move-tx construction.
- Unauthorized challenge initiation, burning operator collateral.
- Any other privileged bridge action gated only by the `OnlyAggregatorAndSelf` invariant.

### Likelihood Explanation
The `ServerAddr::Unix` variant is present in production code (used in `core/src/rpc/aggregator.rs` and `core/src/servers.rs`) and is available on all Unix targets via `#[cfg(unix)]`. Any co-located process (e.g., a compromised sidecar, a malicious container sharing a volume, or any process running as the same OS user) can exploit this without any key material.

### Recommendation
Wrap the service in `InterceptedService` in the Unix branch exactly as the TCP branch does:

```rust
// Unix branch — apply the same interceptor
let service = InterceptedService::new(
    service,
    if config.client_verification {
        OnlyAggregatorAndSelf { aggregator_cert, our_cert }
    } else {
        Noop
    },
);
let server_builder = tonic::transport::Server::builder()
    ...
    .add_service(service);
```

Note that on a Unix socket there are no TLS peer certs, so `only_aggregator_and_self` will hit the `peer_certs` is `None` branch and return `Unauthenticated` in non-test mode — which is the correct, safe behavior. If Unix sockets are intended for trusted local IPC only, use `Noop` explicitly and document that assumption; do not silently omit the interceptor.

### Proof of Concept
1. Start an operator or verifier server via `create_grpc_server(ServerAddr::Unix(path), service, …)` with `client_verification = true`.
2. Connect a plain gRPC client over the Unix socket with no TLS identity.
3. Call any privileged RPC (e.g., `get_deposit_signatures`).
4. Observe `Ok` response instead of `Unauthenticated` — the interceptor was never invoked.

### Citations

**File:** core/src/servers.rs (L114-160)
```rust
            let service = InterceptedService::new(
                service,
                if config.client_verification {
                    let client_cert = CertificateDer::from_pem_file(&config.client_cert_path)
                        .wrap_err(format!(
                            "Failed to read client certificate from {}",
                            config.client_cert_path.display()
                        ))?
                        .to_owned();

                    let aggregator_cert =
                        CertificateDer::from_pem_file(&config.aggregator_cert_path)
                            .wrap_err(format!(
                                "Failed to read aggregator certificate from {}",
                                config.aggregator_cert_path.display()
                            ))?
                            .to_owned();

                    OnlyAggregatorAndSelf {
                        aggregator_cert,
                        our_cert: client_cert,
                    }
                } else {
                    Noop
                },
            );

            tracing::info!(
                "Starting {} gRPC server with TCP address: {}",
                server_name,
                socket_addr
            );

            let server_builder = tonic::transport::Server::builder()
                .layer(AddMethodMiddlewareLayer)
                .layer(BufferLayer::new(config.grpc.req_concurrency_limit))
                .layer(RateLimitLayer::new(
                    config.grpc.ratelimit_req_count as u64,
                    Duration::from_secs(config.grpc.ratelimit_req_interval_secs),
                ))
                .timeout(Duration::from_secs(config.grpc.timeout_secs))
                .tcp_keepalive(Some(Duration::from_secs(config.grpc.tcp_keepalive_secs)))
                .concurrency_limit_per_connection(config.grpc.req_concurrency_limit)
                .http2_adaptive_window(Some(true))
                .tls_config(tls_config)
                .wrap_err("Failed to configure TLS")?
                .add_service(service);
```

**File:** core/src/servers.rs (L179-189)
```rust
        ServerAddr::Unix(ref socket_path) => {
            let server_builder = tonic::transport::Server::builder()
                .layer(AddMethodMiddlewareLayer)
                .layer(BufferLayer::new(config.grpc.req_concurrency_limit))
                .layer(RateLimitLayer::new(
                    config.grpc.ratelimit_req_count as u64,
                    Duration::from_secs(config.grpc.ratelimit_req_interval_secs),
                ))
                .timeout(Duration::from_secs(config.grpc.timeout_secs))
                .concurrency_limit_per_connection(config.grpc.req_concurrency_limit)
                .add_service(service);
```

**File:** core/src/rpc/interceptors.rs (L36-76)
```rust
fn only_aggregator_and_self(
    req: Request<()>,
    our_cert: &CertificateDer<'static>,
    aggregator_cert: &CertificateDer<'static>,
) -> Result<Request<()>, Status> {
    let Some(peer_certs) = req.peer_certs() else {
        if cfg!(test) {
            // Test mode, we don't need to verify peer certificates
            return Ok(req);
        } else {
            // If we're not in test mode, we need to check peer certificates
            return Err(Status::unauthenticated(
                "Failed to verify peer certificate, is TLS enabled?",
            ));
        }
    };

    // IMPORTANT: Only check the leaf (end-entity) certificate, which is always the first
    // certificate in the chain. The leaf is the only certificate whose private key the peer
    // proved possession of during the TLS handshake. Checking anywhere else in the chain
    // would allow identity spoofing: an attacker could include a pinned cert as an
    // intermediate in their chain without possessing its private key.
    let Some(leaf_cert) = peer_certs.first() else {
        return Err(Status::unauthenticated("Peer certificate chain is empty"));
    };

    if is_internal(&req) {
        if leaf_cert == our_cert {
            Ok(req)
        } else {
            Err(Status::unauthenticated(
                "Unauthorized call to internal method (not self)",
            ))
        }
    } else if leaf_cert == aggregator_cert || leaf_cert == our_cert {
        Ok(req)
    } else {
        Err(Status::unauthenticated(
            "Unauthorized call to method (not aggregator or self)",
        ))
    }
```
