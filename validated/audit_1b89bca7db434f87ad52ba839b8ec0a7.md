### Title
Unauthenticated disclosure of the internal-only EmergencyStopTx via `InternalGetEmergencyStopTx` when `client_verification` is disabled - ([File: core/src/rpc/interceptors.rs])

### Summary
The `is_internal()` check that is supposed to restrict `Internal*`-prefixed RPCs (such as `internal_get_emergency_stop_tx`) to the aggregator's own client certificate only runs inside `only_aggregator_and_self`, which is only reached when the `OnlyAggregatorAndSelf` interceptor variant is active. When `config.client_verification == false`, `create_grpc_server` in `core/src/servers.rs` installs `Interceptors::Noop` instead, whose `call` implementation unconditionally returns `Ok(req)` with no certificate or method-prefix check at all. Any unauthenticated caller can therefore invoke `internal_get_emergency_stop_tx` and retrieve the emergency-stop transaction/metadata.

### Finding Description
The intended binding is: *the recipient of `InternalGetEmergencyStopTx` output == the aggregator's own client certificate holder (`our_cert` in `Interceptors::OnlyAggregatorAndSelf`)*. This is enforced only by `is_internal(&req)` inside `only_aggregator_and_self` at [1](#0-0) , which rejects any peer whose leaf cert is not `our_cert` for methods whose `grpc-method` metadata starts with `Internal`.

However, `Interceptors::call` dispatches based on the enum variant: [2](#0-1)  — for `Interceptors::Noop` it returns `Ok(req)` immediately, never calling `is_internal` or checking any certificate.

`create_grpc_server` in `core/src/servers.rs` selects `Noop` whenever `config.client_verification` is `false`: [3](#0-2) . In that configuration mode, the server's TLS config also omits `client_ca_root`, meaning no client certificate is required at the transport layer at all: [4](#0-3) .

Consequently, when an aggregator is deployed with `client_verification = false`, any party that can reach the aggregator's gRPC port — with no TLS client identity — can call `internal_get_emergency_stop_tx` and receive the internal-only response, since the sole gate protecting `Internal*` methods (`is_internal`) is never evaluated.

### Impact Explanation
The disclosed artifact is the encrypted emergency-stop transaction and its metadata, a protocol-internal object intended only for the aggregator's own subsequent internal use (assembling/broadcasting the emergency-stop flow), not for external actors. Premature disclosure of this protocol commitment to an unauthenticated party matches the "High" impact category (premature disclosure of a protocol commitment) defined in the rules. It does not by itself move funds, credit a reimbursement, or burn collateral, but it does leak internal-only bridge-control-plane material to an unauthorized party. This is repeatable for every deposit whose `move_txid` the attacker can learn (e.g., from `NewDeposit` responses or a chain scan), across the aggregator's whole deposit set, as long as the misconfiguration (`client_verification == false`) is present.

### Likelihood Explanation
The vulnerability requires the specific, non-default configuration `client_verification == false`, which the codebase explicitly warns about at startup (`tracing::warn!("Client verification is enabled...")` is the only warning emitted, notably only when verification is *enabled*, with no corresponding warning for the disabled/insecure state) [5](#0-4) . Given that precondition, exploitation costs nothing (no fees, no BTC) beyond knowing a valid `move_txid`, which is learnable from any prior deposit's public response or by chain scanning. The attack is trivially repeatable and requires no privileged role, matching the "unprivileged attacker" threat model.

### Recommendation
Do not gate the `Internal*` method authorization solely on the interceptor variant selected by `client_verification`. Either (a) always enforce the `is_internal` prefix check regardless of TLS/cert verification mode — i.e., have `Interceptors::Noop` (or a replacement) still reject requests whose `grpc-method` starts with `Internal` unless proven to originate from the aggregator's own loopback/self channel, or (b) refuse to serve `Internal*`-prefixed RPCs on any externally-reachable listener when `client_verification` is disabled, restricting such internal calls to a separate, non-network-exposed channel (e.g., in-process call or Unix socket bound to localhost only).

### Proof of Concept
```rust
// core/src/rpc/tests.rs (new #[tokio::test], for illustration of assertions only —
// actual test file location is out of scope per rules, but demonstrates the check)

#[tokio::test]
async fn internal_method_leaks_without_client_verification() {
    // 1. Build a BridgeConfig with client_verification = false.
    let mut config = create_test_config().await; // existing test helper
    config.client_verification = false;

    // 2. Start aggregator gRPC server (TCP) with create_aggregator_grpc_server(config.clone()).
    let (addr, _shutdown) = create_aggregator_grpc_server(config.clone()).await.unwrap();

    // 3. Perform a deposit through the normal flow to populate move_to_vault_txid in DB.
    let move_txid = perform_test_deposit(&config).await; // existing helper flow

    // 4. Connect as a bare, unauthenticated client (no TLS identity / no client cert).
    let mut client = ClementineAggregatorClient::connect(format!("http://{addr}"))
        .await
        .unwrap();

    // 5. Call InternalGetEmergencyStopTx directly.
    let resp = client
        .internal_get_emergency_stop_tx(InternalGetEmergencyStopTxRequest {
            txids: vec![move_txid.clone()],
        })
        .await;

    // Binding under test:
    //   LHS = recipient of InternalGetEmergencyStopTx output
    //   RHS = aggregator's own client-cert holder (our_cert)
    // Expected (secure) behavior: resp is Err(Status::unauthenticated(_)) because attacker != our_cert.
    // Actual (vulnerable) behavior: resp is Ok(GetEmergencyStopTxResponse { .. }) because
    // Interceptors::Noop short-circuits is_internal() entirely.
    assert!(
        resp.is_err(),
        "unauthenticated caller must not receive internal emergency-stop tx, but got: {:?}",
        resp
    );
}
```
This test starts the aggregator with the insecure-but-supported `client_verification = false` configuration, performs a deposit to obtain a valid `move_txid`, and calls `internal_get_emergency_stop_tx` as a plain, uncertified client. Under the current code, the call succeeds (violating the binding), whereas with `client_verification = true` and `Interceptors::OnlyAggregatorAndSelf`, the same call is rejected by `is_internal`/`only_aggregator_and_self`.

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
