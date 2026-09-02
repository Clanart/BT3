### Title
Unauthenticated `InternalSendTx` broadcasting when `client_verification == false` - ([File: core/src/rpc/interceptors.rs])

### Summary
When an aggregator is deployed with `config.client_verification == false`, `create_grpc_server` installs `Interceptors::Noop`, whose `call` implementation simply returns `Ok(req)` for every request, without ever invoking `is_internal`/`only_aggregator_and_self` [1](#0-0) [2](#0-1) . This means any caller — including an unauthenticated, certificate-less attacker — can call `InternalSendTx` on the aggregator's gRPC endpoint and have an arbitrary raw Bitcoin transaction forwarded into the tx-sender/broadcast layer.

### Finding Description
The intended binding is: `CALLER_AUTHORITY(InternalSendTx) == SELF_CERT` (i.e., only a caller presenting the aggregator's own client certificate as the TLS leaf certificate may call any RPC method whose gRPC path is prefixed `Internal`), as enforced by `only_aggregator_and_self` via `is_internal(&req)` checking `leaf_cert == our_cert` [3](#0-2) .

This binding is only enforced by the `OnlyAggregatorAndSelf` interceptor variant. `create_grpc_server` selects between the two variants purely based on `config.client_verification`: if true, `OnlyAggregatorAndSelf` is installed with the aggregator/self certificates loaded from disk; if false, `Interceptors::Noop` is installed instead [2](#0-1) . `Noop::call` unconditionally returns `Ok(req)`, so no certificate or `is_internal` check ever executes [4](#0-3) . In this configuration, the equality "caller cert == self cert" is never evaluated on either side — the binding is vacuously and completely broken, not merely bypassed at the margins.

Exploit flow: an attacker connects to the aggregator's public gRPC port (with or without TLS, since `ServerTlsConfig` in this mode has no `client_ca_root` and thus doesn't require client certs), crafts a `SendTxRequest` containing an arbitrary raw transaction they authored, and calls `InternalSendTx`. The request passes through `AddMethodMiddlewareLayer`, `Noop` (no-op), and reaches `AggregatorServer::internal_send_tx`, which forwards the tx to the tx-sender/broadcast layer, causing it to be broadcast/rebroadcast using the aggregator's fee-paying infrastructure.

### Impact Explanation
This is a state-changing/broadcasting call reachable without any authentication, matching the "unauthenticated state-changing or broadcasting call" category. An attacker can inject arbitrary transactions into the aggregator's broadcast pipeline, potentially conflicting with (e.g., via RBF or mempool/fee competition against) in-flight bridge transactions such as pending Reimburse, Challenge, or Disprove transactions, and can consume the aggregator's fee-bumping/CPFP resources. This is repeatable indefinitely and applies to every deployment run with `client_verification = false`, regardless of deposit or operator identity, since it does not depend on any specific deposit/withdrawal state — it is a blanket authorization bypass on the interceptor layer.

### Likelihood Explanation
The precondition is explicit and operator-controlled: `config.client_verification` must be `false` on the aggregator deployment. This is a deployment/configuration matter, not a code-level guarantee — the code path exists and is fully exploitable whenever that flag is set. The attacker cost is minimal (just network access to the gRPC port and ability to pay any fees on the crafted transaction they wish to broadcast); no key, collateral, or certificate is required. Given the flag defaults and its exposure in `core/src/config/mod.rs`/`env.rs` control whether this is enabled in practice, and I was unable to fully confirm the production default value for `client_verification` from the available index (config default not fully resolved in this pass), the severity should be treated as configuration-dependent High rather than universally applicable — but when the precondition holds, exploitation is trivial and fully reliable.

### Recommendation
Do not allow `Noop` to silently disable authentication for `Internal`-prefixed methods regardless of `client_verification`. At minimum, `is_internal` checks should be enforced independent of the `client_verification` toggle (e.g., always require mTLS/self-cert for methods with the `Internal` prefix, or reject `Internal*` calls entirely when TLS client verification is disabled), and the aggregator should refuse to start, or should hard-fail `Internal*` calls, if `client_verification` is `false`.

### Proof of Concept
```rust
// cargo test -p clementine-core --test <aggregator interceptor tests>
// 1. Build a BridgeConfig with client_verification = false.
// 2. Call create_aggregator_grpc_server(config) to start the aggregator with Interceptors::Noop.
// 3. Create a plain (no client cert) tonic Channel/Endpoint to the aggregator address.
// 4. Construct an attacker-authored SendTxRequest wrapping an arbitrary raw signed Bitcoin transaction.
// 5. Call client.internal_send_tx(request).
// assert_eq!(result.is_ok(), true); // demonstrates the call succeeds
// Compare against: with client_verification = true (OnlyAggregatorAndSelf), the same
// certificate-less client call to internal_send_tx must return Err(Status::unauthenticated(_)).
// The two outcomes diverging confirms CALLER_AUTHORITY(InternalSendTx) == SELF_CERT
// is enforced only conditionally, and is completely bypassed when client_verification == false.
```

### Citations

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
