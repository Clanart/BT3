## Title
Aggregator gRPC server bypasses client certificate authentication entirely when `client_verification=false`, exposing `Internal*`-prefixed self-only methods (e.g. `internal_send_tx`) to any TLS client - (File: core/src/servers.rs, core/src/rpc/interceptors.rs)

## Summary
When `BridgeConfig.client_verification` is `false`, `create_grpc_server` builds a `ServerTlsConfig` with only `.identity(server_identity)` and no `.client_ca_root(...)`, so the server never requests or validates a client certificate during the TLS handshake. Simultaneously, the same `false` branch wires the service through `Interceptors::Noop` instead of `OnlyAggregatorAndSelf`, meaning the `is_internal`/leaf-certificate-equality check that is supposed to gate `Internal*`-prefixed RPCs is skipped entirely rather than merely weakened.

## Finding Description
The intended binding is: `leaf_cert_of_caller == our_cert` (self) must hold before an `Internal*`-prefixed method such as `ClementineAggregator::internal_send_tx` is allowed to execute, as implemented in `only_aggregator_and_self`: [1](#0-0) 

This check only runs inside the `Interceptors::OnlyAggregatorAndSelf` variant, which is only constructed when `config.client_verification` is `true`: [2](#0-1) 

When `config.client_verification` is `false`:
1. The TLS config is built with `ServerTlsConfig::new().identity(server_identity)` and no `client_ca_root`, so the TLS handshake never requests a client certificate - any TLS client can complete the handshake without presenting an identity at all. [3](#0-2) 
2. The service is wrapped with `Interceptors::Noop`, which unconditionally returns `Ok(req)` for every request, including ones whose `grpc-method` header starts with `Internal`: [4](#0-3) 

So for this configuration, the equality `leaf_cert_of_caller == our_cert` is never evaluated at all - the binding fails unconditionally, not merely due to a missing client CA. Any caller who can open a TCP connection and complete a one-sided TLS handshake to the aggregator's `socket_addr` can invoke `ClementineAggregator::internal_send_tx` (an `Internal`-prefixed RPC by naming convention enforced by `is_internal`), passing an attacker-chosen `SendTxRequest { raw_tx: Some(attacker_tx), fee_type: ... }`, which is expected to route into `self.tx_sender.insert_try_to_send`, scheduling that transaction for fee-paying broadcast through the protocol's own tx-sending infrastructure.

## Impact Explanation
An attacker-chosen `bitcoin::Transaction` gets scheduled for broadcast through the aggregator's fee-paying `tx_sender`. This is a broadcasting call that requires no authentication under this configuration, matching the rubric's High severity category: "an unauthenticated state-changing or broadcasting call." Depending on how `tx_sender` selects UTXOs/anchors for fee-bumping, an attacker-inserted transaction could compete for shared UTXOs or fee-bumping outputs used by pending Reimburse/Challenge/Disprove transactions, potentially delaying or displacing a deadline-bound transaction's confirmation. This is repeatable per call and not tied to a specific deposit/operator - any successful connection while `client_verification=false` grants this capability.

## Likelihood Explanation
This vulnerability is entirely conditioned on the deployment configuration flag `client_verification` being set to `false`. The code path in `core/src/servers.rs` demonstrates that this is a first-class, documented configuration option (not a test-only artifact) that a real aggregator operator could set, and the aggregator's own startup code explicitly warns only about the "enabled" (`true`) case, not the risk of disabling it: [5](#0-4) 
I was not able to confirm within this session what the shipped default value of `client_verification` is (whether production/testnet configs set it to `true` or `false`), which is necessary to assess real-world exposure versus a theoretical misconfiguration state. If any officially-documented or default deployment configuration ships with `client_verification=false` for the aggregator, this is directly exploitable by any unprivileged network attacker with no BTC cost, no key material, and no TLS certificate, requiring only network reachability to the aggregator's public gRPC port.

## Recommendation
Do not allow the `client_verification=false` branch to bypass authentication for `Internal*`-prefixed methods. At minimum:
- Always attach `client_ca_root` and always use `Interceptors::OnlyAggregatorAndSelf` for internal methods, independent of the `client_verification` toggle, or
- Explicitly document/enforce that `client_verification=false` is only safe for fully trusted/local deployments, and add a hard runtime check that refuses to expose `Internal*` methods over a TCP socket_addr binding unless client TLS authentication is enabled.

## Proof of Concept
```
cargo test -p core -- create_aggregator_grpc_server_no_client_verification
```
Test plan:
1. Build `BridgeConfig` with `client_verification = false` and start `create_aggregator_grpc_server`.
2. Connect to the aggregator's `socket_addr` via a `tonic` client configured with TLS but presenting no client identity/certificate (or an arbitrary self-signed one).
3. Assert the TLS handshake succeeds (proving no `client_ca_root` was requested).
4. Call `internal_send_tx` with an attacker-constructed `SendTxRequest { raw_tx: Some(attacker_tx), fee_type: ... }`.
5. Assert the call returns `Ok(...)` (not `Status::unauthenticated`) and that `tx_sender` recorded/scheduled `attacker_tx` (e.g., by inspecting the `tx_sender` DB/queue state), proving `leaf_cert_of_caller == our_cert` was never evaluated for this request.

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

**File:** core/src/servers.rs (L306-308)
```rust
    if config.client_verification {
        tracing::warn!("Client verification is enabled on aggregator gRPC server",);
    }
```
