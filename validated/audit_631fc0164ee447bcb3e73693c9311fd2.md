### Title
Unauthenticated disclosure of the emergency-stop transaction via `InternalGetEmergencyStopTx` when `client_verification` is disabled - (File: core/src/rpc/interceptors.rs)

### Summary
The `is_internal()` prefix check that is supposed to restrict every `Internal*` gRPC method (including `InternalGetEmergencyStopTx`) to the aggregator's own certificate holder is implemented only inside `only_aggregator_and_self`, which is used exclusively by the `Interceptors::OnlyAggregatorAndSelf` variant. When `config.client_verification == false`, `create_grpc_server` wires up `Interceptors::Noop` instead, whose `call()` implementation unconditionally returns `Ok(req)` without ever invoking `is_internal()` or any peer-certificate check. Any unauthenticated TCP client that knows a `move_txid` can therefore call `InternalGetEmergencyStopTx` and receive the signed emergency-stop transaction meant to be internal-only.

### Finding Description
The broken binding: `party receiving InternalGetEmergencyStopTx output == the aggregator's own client-certificate holder (our_cert)`.

- `is_internal()` in `core/src/rpc/interceptors.rs` (lines 12-20) inspects the synthetic `grpc-method` header (injected by `AddMethodMiddlewareLayer`) and returns true for methods prefixed `Internal`.
- This check is only consulted from `only_aggregator_and_self` (lines 36-77), reachable solely through the `Interceptors::OnlyAggregatorAndSelf { our_cert, aggregator_cert }` variant. [1](#0-0) 
- `Interceptors::Noop => Ok(req)` performs no authentication at all — it never reaches `is_internal()`. [2](#0-1) 
- `core/src/servers.rs::create_grpc_server` selects between the two interceptor variants based purely on `config.client_verification`: if true it builds `OnlyAggregatorAndSelf` with the loaded client/aggregator certs, if false it installs `Noop`. [3](#0-2) 
- `AggregatorServiceImpl` exposes `internal_get_emergency_stop_tx`, an `Internal*`-prefixed RPC present in `core/src/rpc/aggregator.rs`, which is intended to be reachable only by the aggregator itself (self-call) per the `is_internal` design comment ("This normally doesn't exist but we add it in the AddMethodMiddleware").

Exploit flow: an attacker who learned any existing `move_to_vault_txid` (from a prior `NewDeposit` response or a chain scan) connects to the aggregator's public gRPC port with no TLS client identity while `client_verification == false`, and calls `InternalGetEmergencyStopTx{txids: [...]}`. Because `Noop` is active, the request is forwarded straight to `AggregatorServiceImpl::internal_get_emergency_stop_tx` with no identity check, and the attacker receives the emergency-stop transaction and associated metadata — a protocol-internal artifact that should never leave the aggregator/self-authenticated boundary. No existing guard (`only_aggregator_and_self`, `SECP.verify_schnorr`, deposit/storage validation) applies here since the interceptor layer is the only authorization mechanism for this RPC and it is bypassed entirely by configuration choice.

### Impact Explanation
The emergency-stop transaction is a signed protocol commitment whose premature disclosure before its intended use falls under the rules' High-severity category "premature disclosure of a protocol commitment." Nothing is spent or credited by this call alone (`InternalGetEmergencyStopTx` is a read), but leaking the pre-signed emergency-stop transaction and its metadata to an unauthenticated party lets that party learn/broadcast it ahead of the aggregator's intended timing, undermining the confidentiality assumption baked into the `is_internal()` gate. The issue is repeatable for every deposit whose `move_txid` the attacker can enumerate and is not limited to a single deposit or operator — any `Internal*` RPC on the aggregator suffers the identical bypass whenever `Noop` is active, not just this one method.

### Likelihood Explanation
The precondition is entirely configuration-driven: `config.client_verification == false`. The code path itself explicitly supports and exercises this state in `create_grpc_server` (no TLS client-cert enforcement branch), and `servers.rs` emits a warning only when verification is *enabled*, suggesting the disabled state is a normal/expected mode of operation. No BTC cost, no privileged credentials, and no protocol state are required from the attacker — only the ability to reach the aggregator's TCP port and know one `move_txid`, which is learnable from any prior deposit interaction. This makes the finding trivially and repeatedly exploitable whenever an aggregator is deployed with `client_verification` disabled.

### Recommendation
Do not let the `Internal*` authorization guarantee degrade to no-op based on the general `client_verification` toggle. Either (a) always enforce self-certificate/mTLS verification specifically for `Internal*`-prefixed methods regardless of the `client_verification` setting (i.e., give internal-method authorization its own independent, non-optional gate), or (b) reject/dedicate a separate internal-only transport (e.g., loopback/unix-socket-only) for `Internal*` RPCs so they are structurally unreachable from the public network interface even when `client_verification` is false.

### Proof of Concept
```rust
// cargo test binding-violation proof (Unix or TCP aggregator server, no mTLS)
// 1. Build BridgeConfig with client_verification = false.
// 2. Start aggregator via create_aggregator_unix_server(config, socket_path)
//    (or create_aggregator_grpc_server for TCP) — this wires Interceptors::Noop
//    per core/src/servers.rs create_grpc_server.
// 3. Perform a normal deposit flow to populate a move_to_vault_txid in the DB.
// 4. Connect a bare ClementineAggregatorClient with NO tonic Channel TLS identity
//    (plain, uninterecepted channel — simulating an attacker with zero certs).
// 5. Call client.internal_get_emergency_stop_tx(InternalGetEmergencyStopTxRequest {
//        txids: vec![move_txid_bytes],
//    }).await
// 6. Assert:
//      LHS (expected binding): response should be Err(Status::unauthenticated(_))
//        because caller != our_cert (the "self" identity for is_internal()).
//      RHS (observed): response is Ok(GetEmergencyStopTxResponse { .. })
//        containing the actual emergency stop tx bytes.
// The mismatch (Ok instead of unauthenticated) demonstrates the binding
// "receiver of Internal* output == our_cert holder" is violated whenever
// Interceptors::Noop is active.
``` [4](#0-3) [3](#0-2)

### Citations

**File:** core/src/rpc/interceptors.rs (L1-77)
```rust
use tonic::{service::Interceptor, transport::CertificateDer, Request, Status};

#[derive(Debug, Clone)]
pub enum Interceptors {
    OnlyAggregatorAndSelf {
        aggregator_cert: CertificateDer<'static>,
        our_cert: CertificateDer<'static>,
    },
    Noop,
}

fn is_internal(req: &Request<()>) -> bool {
    // This normally doesn't exist but we add it in the AddMethodMiddleware
    let Some(path) = req.metadata().get("grpc-method") else {
        // No grpc method? this should not happen
        tracing::error!("Missing grpc-method header in request");
        return false;
    };
    path.as_bytes().starts_with(b"Internal")
}

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

#[allow(clippy::result_large_err)]
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
