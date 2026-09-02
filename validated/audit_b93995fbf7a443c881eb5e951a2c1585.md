### Title
`client_verification=false` installs `Interceptors::Noop`, making all `ClementineAggregator` RPCs (including `Internal*`-restricted ones) callable with zero TLS certificate - ([File: core/src/servers.rs])

### Summary
`create_grpc_server` in `core/src/servers.rs` picks the connection-authentication interceptor solely based on `config.client_verification`: when true it installs `Interceptors::OnlyAggregatorAndSelf { aggregator_cert, our_cert }`, when false it installs `Interceptors::Noop`, which is a pass-through that performs no certificate check at all. Since `only_aggregator_and_self` (`core/src/rpc/interceptors.rs`) is the only code path that ever inspects `is_internal(&req)` or compares the peer's leaf certificate, disabling it via `client_verification=false` removes every distinction between externally-callable aggregator methods (`Setup`, `NewDeposit`, `Withdraw`, `OptimisticPayout`, `SendMoveToVaultTx`) and `Internal*`-only methods (`InternalSendTx`, `InternalGetEmergencyStopTx`).

### Finding Description
Binding claimed: a caller successfully invoking any `ClementineAggregator` method implies that caller holds `our_cert` (for `Internal*` methods) or `aggregator_cert`/`our_cert` (for public methods) — enforced exclusively by `only_aggregator_and_self` when `Interceptors::OnlyAggregatorAndSelf` is active [1](#0-0) .

Code path: `create_grpc_server` builds the TLS config and interceptor together, gated on the same `config.client_verification` boolean: [2](#0-1) 
When `client_verification` is `false`, the `ServerTlsConfig` omits `.client_ca_root(...)` (so the TLS handshake does not require or validate a client certificate) and the interceptor is `Noop`, which returns `Ok(req)` unconditionally for every request [3](#0-2) . This means `is_internal(&req)` is never consulted, so the `grpc-method` header added by `AddMethodMiddlewareLayer` and the certificate comparisons in `only_aggregator_and_self` are entirely bypassed — a request from a peer presenting no certificate whatsoever reaches the aggregator's service handlers directly.

`create_aggregator_grpc_server` merely logs a warning when `client_verification` is true but otherwise defers entirely to `create_grpc_server`'s binary choice between `OnlyAggregatorAndSelf` and `Noop` [4](#0-3) . There is no secondary authorization layer inside the aggregator's RPC handlers themselves that re-checks caller identity — `only_aggregator_and_self` is the sole gate. Once `client_verification=false`, an attacker who has no `aggregator_cert_path` and no `client_cert_path` can call any aggregator method, including state-mutating ones like `Withdraw`/`OptimisticPayout`/`SendMoveToVaultTx` and the `Internal*` methods meant only for the entity's own loopback calls.

### Impact Explanation
With this configuration, an unauthenticated caller can invoke aggregator RPCs that mutate protocol state and/or broadcast Bitcoin transactions (e.g. `SendMoveToVaultTx`, `Withdraw`, `OptimisticPayout`) as well as `Internal*` RPCs intended solely for the aggregator's own certificate (`InternalSendTx`, `InternalGetEmergencyStopTx`). This is an unauthenticated state-changing/broadcasting call, matching the High severity category defined in the rules. The blast radius is total for any deployment running with `client_verification=false`: every aggregator RPC, across all deposits and operators handled by that aggregator instance, is exposed with zero cryptographic gating.

### Likelihood Explanation
This is entirely dependent on deployment configuration: it only manifests when the operator/deployer sets `client_verification=false` in `BridgeConfig`. The reference test/dev config file (`core/src/test/data/bridge_config.toml`) and testnet4 docker config (`scripts/docker/configs/testnet4/bridge_config.toml`) both reference `client_verification`, indicating it's a real, supported field rather than a hypothetical toggle — but I could not fully confirm from available context whether the default/production deployment sets this to `true` or `false`, since I ran out of iterations before reading the full default value in `core/src/config/mod.rs`/`env.rs`. If any production deployment sets `client_verification=false` (e.g., for internal testing or misconfiguration), the attack requires no BTC cost and no privileged material — the attacker only needs network access to the aggregator's gRPC port.

### Recommendation
Remove or restrict the `Noop` interceptor option, especially for the aggregator server. At minimum, force `client_verification=true` (or an equivalent always-on identity check) unconditionally for `create_aggregator_grpc_server`, independent of the generic `client_verification` config flag, since the aggregator's `Internal*`/public method split has no other enforcement mechanism. If `Noop` must remain available for local/dev use, gate it behind a `debug_assertions`/explicit dev-only flag that cannot be set for TCP-exposed production servers, and add a defense-in-depth check inside aggregator RPC handlers themselves.

### Proof of Concept
Mirror `test_auth_interceptor` (`core/src/test/rpc_auth.rs`) but:
1. Build a `BridgeConfig` with `client_verification = false`.
2. Start the aggregator via `create_aggregator_grpc_server` (or `create_aggregator_unix_server` for the unix-socket variant).
3. Construct a gRPC client channel with **no** client identity configured (do not load `certs/client/client.pem` or `certs/server/server.pem`), only TLS transport trust of the server cert (or plaintext, if TLS is entirely skippable given no `client_ca_root`).
4. Assert: calling a non-internal method (e.g. `NewDeposit`) succeeds (`Ok`) — expected under the current binding it should fail with `Status::unauthenticated`.
5. Assert: calling an `Internal*` method (e.g. `InternalGetEmergencyStopTx`) also succeeds (`Ok`) — expected under the current binding it should fail with `"Unauthorized call to internal method (not self)"`.
6. Compare against the same test with `client_verification = true`, where step 4/5 correctly return `Status::unauthenticated`, proving the binding is enforced only when `OnlyAggregatorAndSelf` is active and collapses entirely under `Noop`.

### Citations

**File:** core/src/rpc/interceptors.rs (L22-33)
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
}
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

**File:** core/src/servers.rs (L293-317)
```rust
pub async fn create_aggregator_grpc_server(
    config: BridgeConfig,
) -> Result<(std::net::SocketAddr, oneshot::Sender<()>), BridgeError> {
    let addr: std::net::SocketAddr = format!("{}:{}", config.host, config.port)
        .parse()
        .wrap_err("Failed to parse address")?;
    let aggregator_server = AggregatorServer::new(config.clone()).await?;
    aggregator_server.start_background_tasks().await?;

    let svc = ClementineAggregatorServer::new(aggregator_server)
        .max_encoding_message_size(config.grpc.max_message_size)
        .max_decoding_message_size(config.grpc.max_message_size);

    if config.client_verification {
        tracing::warn!("Client verification is enabled on aggregator gRPC server",);
    }

    let (server_addr, shutdown_tx) =
        create_grpc_server(addr.into(), svc, "Aggregator", &config).await?;

    match server_addr {
        ServerAddr::Tcp(socket_addr) => Ok((socket_addr, shutdown_tx)),
        _ => Err(BridgeError::ConfigError("Expected TCP address".into())),
    }
}
```
