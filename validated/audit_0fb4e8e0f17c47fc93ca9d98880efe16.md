### Title
Unauthenticated access to Internal-only aggregator RPCs (including `InternalGetEmergencyStopTx`) when `client_verification=false` - (File: core/src/rpc/interceptors.rs)

### Summary
The `OnlyAggregatorAndSelf` interceptor, which enforces that `Internal`-prefixed gRPC methods may only be called by the aggregator's own client certificate, is only installed when `config.client_verification` is `true`. When it is `false`, `Interceptors::Noop` is installed instead, which unconditionally returns `Ok(req)` for every request regardless of TLS identity, exposing `Internal*` methods such as `InternalGetEmergencyStopTx` to any caller with network access to the gRPC port.

### Finding Description
The intended binding is: `caller_identity(request) == self_cert` for any request where `is_internal(request) == true`. This is implemented in `only_aggregator_and_self` [1](#0-0) , which is only reachable through `Interceptors::OnlyAggregatorAndSelf::call` [2](#0-1) .

In `create_grpc_server`, the choice of interceptor is gated solely on `config.client_verification`: if true, `OnlyAggregatorAndSelf` is built from `client_cert_path`/`aggregator_cert_path`; if false, `Interceptors::Noop` is used instead [3](#0-2) . `Noop::call` simply returns `Ok(req)` for any request with no certificate check at all [4](#0-3) .

`is_internal` classifies a method as internal purely by the presence of an `Internal` prefix on the `grpc-method` header set by `AddMethodMiddleware` [5](#0-4) ; this classification is meaningless unless the enforcing interceptor is actually installed. When `client_verification=false`, no interceptor evaluates `is_internal` at all, so any unauthenticated caller reaching the aggregator's public gRPC port can invoke `InternalGetEmergencyStopTx` (and any other `Internal*` RPC) exactly as if they were the aggregator itself.

### Impact Explanation
An attacker with mere network access to the aggregator's gRPC port (no TLS certificate, no key share) can call any `Internal`-prefixed RPC, including `InternalGetEmergencyStopTx`, retrieving the encrypted emergency-stop transaction for a `move_to_vault_txid` ahead of its intended reveal. This is a premature disclosure of a protocol commitment, matching the "High" impact category. The exposure is not limited to this one method — every `Internal*` RPC on the aggregator server is equally unauthenticated under this configuration, and the issue is repeatable for every deposit whose emergency-stop tx has been stored.

### Likelihood Explanation
The finding is entirely conditioned on `client_verification=false` being the deployed configuration — this is stated as a precondition in the question and is a legitimate, reachable configuration path in `create_grpc_server` (not test-only, not behind a feature flag limited to tests). Given that precondition, the attack requires no BTC, no fees, no privileged role — only a plain TCP connection to the aggregator's advertised gRPC port and a crafted `InternalGetEmergencyStopTx` request. This is fully feasible and repeatable across every deposit with a stored emergency-stop tx.

### Recommendation
Do not make `Internal`-only authorization conditional on `client_verification`. Enforce the self-only check for `is_internal` requests unconditionally (i.e., always require and validate a peer certificate for internal methods even when `client_verification` is disabled for external/aggregator-vs-verifier traffic), or reject/refuse to start the server if `client_verification=false` while `Internal` RPCs are exposed on the same listener.

### Proof of Concept
```rust
// core/src/servers.rs or a new integration test module (non-test_utils file)
#[tokio::test]
async fn internal_method_unauthenticated_when_client_verification_disabled() {
    let mut config = create_test_config(); // client_verification = false
    config.client_verification = false;

    // Preconditions: store an emergency-stop tx for some move_to_vault_txid
    // via insert_signed_emergency_stop_tx_if_not_exists(...)

    let (addr, _shutdown) = create_aggregator_grpc_server(config).await.unwrap();

    // Connect with a bare tonic Channel, no client Identity / no TLS cert at all
    let channel = tonic::transport::Channel::from_shared(format!("http://{}", addr))
        .unwrap()
        .connect()
        .await
        .unwrap();
    let mut client = ClementineAggregatorClient::new(channel);

    let resp = client
        .internal_get_emergency_stop_tx(InternalGetEmergencyStopTxRequest {
            move_to_vault_txid: known_txid.clone(),
        })
        .await;

    // Binding under test: caller_identity(request) == self_cert for is_internal(request)==true
    // BEFORE fix: resp is Ok(_) with the encrypted emergency-stop tx (binding violated)
    // AFTER fix: resp must be Err(Status::unauthenticated(_))
    assert!(resp.is_err());
    assert_eq!(resp.unwrap_err().code(), tonic::Code::Unauthenticated);
}
```

### Citations

**File:** core/src/rpc/interceptors.rs (L12-20)
```rust
fn is_internal(req: &Request<()>) -> bool {
    // This normally doesn't exist but we add it in the AddMethodMiddleware
    let Some(path) = req.metadata().get("grpc-method") else {
        // No grpc method? this should not happen
        tracing::error!("Missing grpc-method header in request");
        return false;
    };
    path.as_bytes().starts_with(b"Internal")
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

**File:** core/src/rpc/interceptors.rs (L62-76)
```rust
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
