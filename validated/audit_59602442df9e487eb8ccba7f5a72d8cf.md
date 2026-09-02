### Title
Unauthenticated `Internal*` gRPC calls (including `internal_send_tx`) bypass all authorization when `client_verification=false` - ([File: core/src/rpc/interceptors.rs])

### Summary
When an operator/verifier/aggregator is deployed with `client_verification=false`, `create_grpc_server` installs `Interceptors::Noop` instead of `OnlyAggregatorAndSelf`. `Noop::call` returns `Ok(req)` unconditionally, so `is_internal`'s `leaf_cert == our_cert` check is never evaluated, and any unauthenticated caller can invoke `Internal*`-prefixed RPCs such as `internal_send_tx` with attacker-supplied data.

### Finding Description
The binding that is supposed to hold is: for any RPC whose method name starts with `Internal`, `leaf_cert == our_cert` must be true before the call proceeds (core/src/rpc/interceptors.rs:62-69, inside `only_aggregator_and_self`).

Tracing the path: `create_grpc_server` (core/src/servers.rs:106-138) chooses the interceptor based on `config.client_verification`. If true, TLS client-cert verification is configured (`client_ca_root`) and `OnlyAggregatorAndSelf{aggregator_cert, our_cert}` is installed. If false, TLS is set up **without** `client_ca_root` (core/src/servers.rs:106-112) and the `Noop` interceptor is installed (core/src/servers.rs:137).

`Interceptor::call` for `Noop` (core/src/rpc/interceptors.rs:24-32) is:
```
Interceptors::Noop => Ok(req),
```
This means `is_internal` (core/src/rpc/interceptors.rs:12-19) is never invoked and the `leaf_cert == our_cert` equality (line 63) is never evaluated at all — both "before" and "after" the request, the check simply does not run. Any caller who can reach the gRPC port (TCP, no client cert required since `client_ca_root` isn't set) can call any RPC, including `Internal*`-prefixed ones like `internal_send_tx` (referenced in core/src/rpc/aggregator.rs and clementine.proto/clementine.rs), supplying an arbitrary `RawSignedTx`. That request flows into the tx-sender pipeline that broadcasts bitcoin transactions on behalf of the operator/verifier.

No other guard intervenes: `is_internal` is purely metadata/path-based and only consulted inside `only_aggregator_and_self`, which is skipped entirely in the `Noop` branch. There is no secondary authentication, signature check, or self-identity check performed by `internal_send_tx` itself before broadcasting - it relies exclusively on the interceptor layer for authorization.

### Impact Explanation
This is an unauthenticated, state-changing/broadcasting call: an attacker without any certificate, key share, or collateral can submit arbitrary bitcoin transactions through a node's tx-sender/broadcast pipeline whenever that node runs with `client_verification=false`. This matches the "unauthenticated state-changing or broadcasting call" High-severity category. Depending on what `internal_send_tx` accepts and how the tx-sender processes/rebroadcasts fee-bumping or replacement logic, this could interfere with pending Reimburse/Disprove/Challenge transactions for the affected operator/verifier, since the broadcast pipeline is shared infrastructure. The blast radius is scoped to whichever specific node is misconfigured with `client_verification=false` — it does not require compromising the aggregator's or any operator's certificate.

### Likelihood Explanation
The precondition is a deployment-configuration one: `client_verification=false` must be set for the target node's `BridgeConfig`. Given this, the attack is essentially free (no BTC cost beyond crafting a valid signed transaction), fully repeatable, and requires only network access to the node's public gRPC port — squarely within the stated unprivileged attacker capabilities (can send requests to the aggregator's/node's public gRPC port). The likelihood is directly gated by how commonly `client_verification=false` is used in real deployments, which is out of scope to assess from the code, but the code path itself provides no fallback protection once this flag is false.

### Recommendation
Do not allow `Interceptors::Noop` to bypass the `is_internal` check. Either always enforce `is_internal`-gated authorization for `Internal*` RPCs regardless of `client_verification`, or refuse to register the `Internal*` service methods at all when `client_verification=false`, so that disabling general client TLS verification cannot silently strip authorization from privileged internal-only endpoints like `internal_send_tx`.

### Proof of Concept
```rust
// cargo test proof outline
#[tokio::test]
async fn noop_interceptor_allows_unauthenticated_internal_send_tx() {
    // 1. Build a BridgeConfig with client_verification = false.
    // 2. Start an operator/aggregator gRPC server via create_grpc_server
    //    (Interceptors::Noop path, core/src/servers.rs:137).
    // 3. Connect a plain gRPC client with NO client certificate (or any arbitrary cert).
    // 4. Craft a `RawSignedTx` containing an attacker-controlled bitcoin transaction.
    // 5. Call `internal_send_tx` on the channel.
    //
    // Assertion (binding check):
    //   before: leaf_cert == our_cert  -> never evaluated (Noop short-circuits at
    //           core/src/rpc/interceptors.rs:30 before reaching is_internal at line 62-63)
    //   after:  the RPC call succeeds and the transaction is queued/broadcast via tx_sender
    //
    // assert!(response.is_ok(), "expected Noop to incorrectly allow InternalSendTx");
    //
    // Contrast: repeat with client_verification = true (OnlyAggregatorAndSelf) and no
    // client cert presented -> is_internal + leaf_cert == our_cert enforced ->
    // assert!(response.is_err()) with Status::unauthenticated("Unauthorized call to internal method (not self)").
}
``` [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** core/src/rpc/interceptors.rs (L12-32)
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

**File:** core/src/servers.rs (L106-138)
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
```
