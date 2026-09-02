### Title
Unauthenticated `Setup` RPC allows any caller to force protocol-state-mutating verifier/operator key re-distribution - (File: core/src/rpc/aggregator.rs, core/src/rpc/interceptors.rs)

### Summary
When an aggregator deployment runs with `client_verification=false`, the gRPC interceptor selected for it is `Interceptors::Noop`, which passes every request through unchecked. Because `AggregatorServer::setup` (the `Setup` RPC) is not otherwise gated by any caller identity check, any unprivileged network peer with access to the aggregator's public gRPC port can invoke `Setup` and force the aggregator to re-collect verifier keys and re-push operator configs to every verifier at will.

### Finding Description
The claimed binding is: CALLER_AUTHORITY(invoker of `Setup`) == the deployment operator meant to run setup exactly once. Tracing the gRPC entry path: `create_grpc_server` in `core/src/servers.rs` builds the `InterceptedService` with either `OnlyAggregatorAndSelf` (mutual-TLS cert pinning) or `Noop`, chosen strictly by the `config.client_verification` boolean [1](#0-0) . `Interceptors::Noop` unconditionally returns `Ok(req)` for any request with no identity check whatsoever [2](#0-1) . Contrast this with `OnlyAggregatorAndSelf`, which pins the leaf TLS certificate to either the aggregator's own cert or the self cert and rejects everyone else [3](#0-2) . Thus the equality CALLER_AUTHORITY(invoker) == operator only holds when `client_verification=true`; when it is `false` (an explicitly supported and, per the aggregator server warning log, expected/reachable deployment configuration [4](#0-3) ), any internet caller passes the interceptor and reaches `AggregatorServer::setup`, which internally fans out `get_operator_params_chunked`/`set_operator_params` calls to every verifier concurrently, re-driving key distribution regardless of caller identity. No idempotency token, nonce, or session guard is visible in the `Setup(Empty{})` request/handler to prevent this from being replayed arbitrarily by an unauthenticated party.

### Impact Explanation
This matches the rules' explicit High-severity category "an unauthenticated state-changing or broadcasting call": the `Setup` RPC mutates aggregator/verifier protocol state (operator parameter distribution) and is reachable by any caller under `Noop`, with no per-call authorization independent of TLS interceptor choice. Repeated invocation is unbounded per attacker session (subject only to whatever rate limiting exists at the transport layer, which is explicitly out of scope for this assessment) and applies uniformly across all verifiers/operators configured on that aggregator, since `setup` iterates over every verifier client.

### Likelihood Explanation
The precondition `client_verification=false` is a real, code-supported deployment mode (not hypothetical) — the code path exists specifically to select `Noop` in that case and the server even logs a warning distinguishing the enabled case, implying disabled is an accepted operational choice. No BTC cost, no key material, and no privileged role are required — only network access to the aggregator's gRPC port, which is explicitly within the unprivileged attacker's granted capabilities. The call is a trivial `Empty{}` request, fully repeatable.

### Recommendation
Do not allow `Noop` (no-authentication) as a valid interceptor for state-mutating RPCs like `Setup` on production/public-facing aggregator deployments; require `client_verification=true` (or an equivalent authenticated caller check) for any RPC that mutates verifier/operator protocol state. Additionally, add explicit idempotency/authorization at the `AggregatorServer::setup` handler level (e.g., a one-time setup flag or admin-only method marker akin to the `Internal` method-prefix check already used by `only_aggregator_and_self`) so that `Setup` cannot be replayed by any successfully-authenticated-but-non-operator caller either.

### Proof of Concept
```rust
// cargo test in core/src/rpc/aggregator.rs test harness (excluding out-of-scope test files,
// this describes the reproduction structure only)
// 1. Start verifiers + aggregator with client_verification = false (Noop interceptor active).
// 2. Client A (legitimate) begins `new_deposit` against the aggregator.
// 3. Concurrently, Client B (a plain, uncertified TCP client with no cert/key material)
//    connects to the aggregator's public port and calls `Setup(Empty{})` in a tight loop.
// 4. Assert: Client B's `Setup` calls succeed (return Ok) despite holding no aggregator/self
//    certificate identity — i.e., CALLER_AUTHORITY(B) != operator, yet the call is accepted.
// 5. Assert: verifier operator params state (as read back via get_operator_params) changes/
//    resets concurrently with the in-flight new_deposit call, OR the deposit call fails in a
//    way inconsistent with a single-setup-per-lifecycle invariant, demonstrating desync.
```

### Citations

**File:** core/src/servers.rs (L106-139)
```rust
            let tls_config = if config.client_verification {
                ServerTlsConfig::new()
                    .identity(server_identity)
                    .client_ca_root(client_ca)
            } else {
                ServerTlsConfig::new().identity(server_identity)
            };

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
```

**File:** core/src/servers.rs (L306-308)
```rust
    if config.client_verification {
        tracing::warn!("Client verification is enabled on aggregator gRPC server",);
    }
```

**File:** core/src/rpc/interceptors.rs (L22-32)
```rust
impl Interceptor for Interceptors {
    #[allow(clippy::result_large_err)]
    fn call(&mut self, req: Request<()>) -> Result<Request<()>, Status> {
        match self {
            Interceptors::OnlyAggregatorAndSelf {
                our_cert,
                aggregator_cert,
            } => only_aggregator_and_self(req, our_cert, aggregator_cert),
            Interceptors::Noop => Ok(req),
        }
    }
```

**File:** core/src/rpc/interceptors.rs (L36-77)
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
}
```
