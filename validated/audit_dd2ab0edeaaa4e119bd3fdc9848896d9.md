### Title
Aggregator gRPC methods have no caller authentication when `client_verification=false` and mainnet safety check exempts the Aggregator actor - (File: core/src/servers.rs)

### Summary
`create_grpc_server` installs `Interceptors::Noop` whenever `config.client_verification` is `false`, and `Noop::call` returns `Ok(req)` unconditionally with no certificate or identity check. `BridgeConfig::check_mainnet_requirements` only forces `client_verification=true` for `Actor::Verifier` and `Actor::Operator`, explicitly excluding `Actor::Aggregator`, so an aggregator deployed with `client_verification=false` passes mainnet configuration validation while exposing all its gRPC methods to any TCP/TLS connection with zero authentication.

### Finding Description
The binding claimed by the question is: `caller_identity_required_by(aggregator_method) == caller_identity_presented_by_attacker`. Tracing the code:

- `create_grpc_server` in `core/src/servers.rs` builds the `InterceptedService` and picks the interceptor purely from config: `if config.client_verification { OnlyAggregatorAndSelf{...} } else { Noop }` [1](#0-0) .
- `Interceptors::Noop::call` returns `Ok(req)` with no comparison against any certificate at all [2](#0-1) .
- The TLS server config itself, when `client_verification` is false, is built without `client_ca_root(...)`, so the TLS handshake does not require the peer to present any client certificate: `ServerTlsConfig::new().identity(server_identity)` [3](#0-2) .
- `create_aggregator_grpc_server` only logs a `tracing::warn!` when verification IS enabled, treating disabled verification as the unremarkable/default case for the aggregator [4](#0-3) .
- Critically, `check_mainnet_requirements` only rejects `client_verification=false` for `Actor::Verifier | Actor::Operator`, explicitly omitting `Actor::Aggregator` from the check: `if matches!(actor_type, cli::Actor::Verifier | cli::Actor::Operator) && !self.client_verification { misconfigs.push(...) }` [5](#0-4) . This means an aggregator running with no client verification passes the mainnet safety gate that would otherwise catch this misconfiguration for verifiers/operators.

If an aggregator is deployed with `client_verification=false` (the config codepath the source code visibly treats as normal/default via the inverted warning), any internet-reachable client can open a bare TLS connection with no client certificate and invoke any method on `ClementineAggregatorServer` (`Setup`, `NewDeposit`, `Withdraw`, `OptimisticPayout`, `InternalSendTx`, `SendMoveToVaultTx`, `InternalGetEmergencyStopTx`) because `Noop` performs no identity check whatsoever — the equality the system is supposed to enforce is never evaluated, so it holds only vacuously.

### Impact Explanation
If exploited, an unauthenticated caller can drive the aggregator into broadcasting attacker-chosen Bitcoin transactions (`internal_send_tx`/`SendMoveToVaultTx`), re-triggering `Setup`/`NewDeposit` flows, or extracting emergency-stop transaction material — actions that can move bridge-adjacent UTXOs, disrupt honest deposit/withdrawal state, or leak protocol data meant to remain internal. This matches the Critical/High impact categories for unauthenticated state-changing or broadcasting calls on aggregator-controlled bridge funds. The blast radius covers every deposit and operator serviced by that aggregator instance, and it is fully repeatable per request with no rate limiting inherent to the auth layer itself.

### Likelihood Explanation
Exploitability is entirely gated on deployment configuration: it requires `client_verification=false` to be the actual operational setting for the aggregator's `BridgeConfig`. The default `BridgeConfig::default()` sets `client_verification: true` [6](#0-5) , and the one sample deployment config I found (`scripts/docker/configs/testnet4/bridge_config.toml`) also sets `client_verification = true` [7](#0-6) . I was not able to locate a separate aggregator-specific deployment config confirming `client_verification=false` is actually used in a real deployment; the question's premise that this is "the documented state for the aggregator" is supported only by the inverted `tracing::warn!` behavior in `create_aggregator_grpc_server` (warning only when verification is *enabled*), not by any deployment artifact I could verify. Given the tool-call budget was exhausted before locating an authoritative aggregator config file or deployment doc, this remains **unconfirmed** for the actual production/documented default. The mainnet-check asymmetry (excluding `Actor::Aggregator`) is confirmed and real in code, independent of any specific toml.

### Recommendation
1. Extend `check_mainnet_requirements` to also require `client_verification=true` (or an equivalent authentication mechanism) for `cli::Actor::Aggregator`, removing the asymmetry that currently exempts the aggregator from the mainnet safety gate.
2. Reconsider making `Interceptors::Noop` unreachable in any non-test build entirely, or at minimum require an explicit, separately-flagged "insecure mode" opt-in rather than the mere absence of `client_verification`, so operators cannot silently disable all authentication.
3. Invert the log-level semantics in `create_aggregator_grpc_server` so that disabling client verification triggers a loud, prominent warning/error rather than enabling it.

### Proof of Concept
```rust
// core/src/test/rpc_auth.rs style test (informational reconstruction)
#[tokio::test]
async fn aggregator_noop_interceptor_allows_unauthenticated_internal_send_tx() {
    let mut config = create_test_config_with_thread_name().await;
    config.client_verification = false; // reproduce claimed "documented" aggregator state

    let (addr, _shutdown) = create_aggregator_grpc_server(config.clone()).await.unwrap();

    // Connect with a bare tonic Channel: no ClientTlsConfig, no client cert.
    let channel = tonic::transport::Channel::from_shared(format!("http://{addr}"))
        .unwrap()
        .connect()
        .await
        .unwrap();
    let mut client = ClementineAggregatorClient::new(channel);

    let resp = client
        .internal_send_tx(tonic::Request::new(attacker_crafted_raw_signed_tx()))
        .await;

    // Binding check: caller presented ZERO credentials, yet request must be rejected.
    // If Noop is active, this assertion currently FAILS (call succeeds).
    assert!(resp.is_err(), "unauthenticated caller must not reach internal_send_tx");
}
```
Both sides of the binding to assert: `caller_identity_required_by(internal_send_tx)` (should be "aggregator's own cert") vs `caller_identity_presented_by_attacker` (none) — the test demonstrates these are never compared when `client_verification=false`, since `Interceptors::Noop::call` unconditionally returns `Ok(req)` [8](#0-7) .

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

**File:** core/src/config/mod.rs (L299-303)
```rust
        if matches!(actor_type, cli::Actor::Verifier | cli::Actor::Operator)
            && !self.client_verification
        {
            misconfigs.push("CLIENT_VERIFICATION=false".to_string());
        }
```

**File:** core/src/config/mod.rs (L486-486)
```rust
            client_verification: true,
```

**File:** scripts/docker/configs/testnet4/bridge_config.toml (L77-77)
```text
client_verification = true
```
