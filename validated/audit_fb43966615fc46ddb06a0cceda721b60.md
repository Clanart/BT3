### Title
Aggregator running with `client_verification=false` installs `Interceptors::Noop`, making every `Internal*` RPC unauthenticated - ([File: core/src/servers.rs])

### Summary
`create_grpc_server` selects between `OnlyAggregatorAndSelf` and `Noop` interceptors solely based on `config.client_verification`, and `only_aggregator_and_self` is the only code path that ever inspects `is_internal`/peer certificates. When the aggregator is deployed with `client_verification=false` — a configuration the codebase itself does not flag as a mainnet misconfiguration for the `Aggregator` actor (unlike `Verifier`/`Operator`) — `Noop` is installed and every RPC, including any `Internal`-prefixed method exposed by the aggregator's service, is accepted unconditionally with no certificate check at all.

### Finding Description
The binding that is supposed to hold is:
`is_internal(req) == true` ⇒ `leaf_cert(req) == our_cert` (checked only inside `only_aggregator_and_self`, [1](#0-0) ).

That binding is only enforced when the installed interceptor is `Interceptors::OnlyAggregatorAndSelf`. `create_grpc_server` picks the interceptor purely from `config.client_verification`: [2](#0-1) 

When `client_verification` is `false`, `Noop` is installed, and `Interceptor::call` for `Noop` simply returns `Ok(req)` without ever evaluating `is_internal` or any certificate: [3](#0-2) 

`create_aggregator_grpc_server` only emits a `tracing::warn!` when verification is *enabled*, implying the "normal"/expected state for the aggregator is verification disabled, and it performs no enforcement of its own: [4](#0-3) 

Crucially, `BridgeConfig::check_mainnet_requirements` — the one guard that could catch a dangerous production misconfiguration — only rejects `client_verification=false` for `Actor::Verifier` and `Actor::Operator`, explicitly excluding `Actor::Aggregator`: [5](#0-4) 

So an aggregator deployed with `client_verification=false` (a state the repo's own mainnet-safety check does not forbid) will accept any gRPC request over its public socket, without TLS client-cert enforcement, and without the `is_internal`/leaf-cert binding ever being evaluated. Any caller that sets the `grpc-method` metadata (populated automatically by `AddMethodMiddlewareLayer`) to a value beginning with `Internal` reaches the same handler an authenticated self-call would reach, because the interceptor that would otherwise reject it (`OnlyAggregatorAndSelf`) is never in the chain.

### Impact Explanation
Depending on which RPCs on the aggregator's service are `Internal`-prefixed, an unauthenticated attacker could invoke state-changing or broadcasting calls that are meant to be restricted to the aggregator's own trusted internal caller (e.g. transaction-broadcast or emergency-stop related endpoints). This matches the rules' High-severity category: "an unauthenticated state-changing or broadcasting call." The blast radius is the entire aggregator process for as long as it runs with `client_verification=false`; the attack is trivially repeatable (every request), costs nothing beyond network access, and requires no privileged role, key, or certificate.

### Likelihood Explanation
The precondition is a deployment choice: the aggregator's `client_verification` set to `false`. This is not blocked by `check_mainnet_requirements` for the `Aggregator` actor even on mainnet, unlike `Verifier`/`Operator`, so it is a plausible and even implicitly-condoned deployment state per the current code, not merely a hypothetical misconfiguration. Given that, exploitation cost is zero (a bare `tonic::Channel`, no certs, one gRPC call with a crafted `grpc-method` header value).

### Recommendation
- Enforce `client_verification=true` for the `Aggregator` actor in `check_mainnet_requirements` (currently only checked for `Verifier`/`Operator`), or otherwise refuse to start the aggregator's gRPC server with `Noop` when any `Internal`-prefixed method is registered on its service.
- Alternatively, make `Internal`-prefixed method protection independent of `client_verification`: always install (or always additionally run) the `is_internal` check regardless of whether full mTLS peer verification is enabled, so `Noop` cannot silently remove authorization for internal-only endpoints.

### Proof of Concept
```rust
// core/src/test/rpc_auth.rs-style test, targeted at the aggregator instead of the operator.
#[tokio::test]
async fn test_aggregator_noop_allows_unauthenticated_internal_call() {
    // 1. Start aggregator with config.client_verification = false
    //    (mirrors create_aggregator_grpc_server / create_aggregator_unix_server).
    // 2. Connect with a bare tonic::Channel — no Identity, no CertificateDer.
    // 3. Call an Internal-prefixed RPC (e.g. InternalSendTx / InternalGetEmergencyStopTx).
    // ASSERT: call succeeds (Ok), proving is_internal/leaf_cert binding was never evaluated.

    // 4. Repeat with config.client_verification = true and no client cert presented.
    // ASSERT: call returns Status::unauthenticated
    //         ("Failed to verify peer certificate, is TLS enabled?"),
    //         proving only_aggregator_and_self is the sole enforcement path.
}
```

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

**File:** core/src/rpc/interceptors.rs (L62-69)
```rust
    if is_internal(&req) {
        if leaf_cert == our_cert {
            Ok(req)
        } else {
            Err(Status::unauthenticated(
                "Unauthorized call to internal method (not self)",
            ))
        }
```

**File:** core/src/servers.rs (L114-139)
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

**File:** core/src/config/mod.rs (L292-304)
```rust
    pub fn check_mainnet_requirements(&self, actor_type: cli::Actor) -> Result<(), BridgeError> {
        if self.protocol_paramset().network != Network::Bitcoin {
            return Ok(());
        }

        let mut misconfigs = Vec::new();

        if matches!(actor_type, cli::Actor::Verifier | cli::Actor::Operator)
            && !self.client_verification
        {
            misconfigs.push("CLIENT_VERIFICATION=false".to_string());
        }

```
