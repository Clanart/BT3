### Title
Unauthenticated `ClementineAggregator::setup` call reachable when `client_verification=false` - ([File: core/src/servers.rs], [File: core/src/rpc/interceptors.rs])

### Summary
When the aggregator is deployed with `config.client_verification == false`, `create_grpc_server` wires the gRPC service through `Interceptors::Noop`, whose `call` implementation returns `Ok(req)` for every request with no identity check. This lets any TCP client without a TLS client certificate invoke the state-changing `ClementineAggregator::setup` RPC.

### Finding Description
The intended binding is CALLER_AUTHORITY: only the `clementine-backend` service (holding the aggregator's own client credentials) should reach `AggregatorServer::setup`. In code: [1](#0-0) 

when `client_verification` is `false`, `ServerTlsConfig` is built without `client_ca_root` and the service is wrapped with `Interceptors::Noop` instead of `OnlyAggregatorAndSelf`. [2](#0-1) 

`Interceptors::Noop => Ok(req)` performs no peer-certificate or identity check whatsoever, so `Interceptors::call` unconditionally admits the request into `InterceptedService`, and it reaches `AggregatorServer::setup`: [3](#0-2) 

`setup` runs a compatibility check and then fetches operator params from all operators and propagates them to all verifiers via a broadcast/`set_operator_params` pipeline — a genuine state-changing, network-wide side effect triggerable by any unauthenticated caller.

However, the specific Critical mechanism claimed in the question — that re-running `setup` "corrupts the nofn key" for already-presigned deposits — is not supported by the code. Verifier key collection is memoized and does not overwrite already-collected keys: [4](#0-3) 

`fetch_pubkeys_from_entities` short-circuits and returns the already-collected keys without re-querying verifiers if `all_collected` is true, so as long as the configured verifier set is unchanged, repeated `setup` calls do not produce a different `verifier_keys` list and therefore cannot derive a different `nofn_xonly_pk` (which is a deterministic MuSig2 aggregate of that same list, see `get_nofn_aggregated_xonly_pk`). This means the "permanently freezing move-to-vault UTXOs by redistributing a different key set" scenario described in the question does not occur under normal operation with a stable verifier config — the keys are cached, not overwritten, on each `setup` invocation.

### Impact Explanation
What remains valid is that an unprivileged network attacker (no TLS cert, no protocol role) can trigger `setup`'s operator-params propagation pipeline at will while `client_verification=false`. This is an unauthenticated, state-changing RPC call reachable by anyone who can open a TCP connection to the aggregator's public port — matching the "High: an unauthenticated state-changing or broadcasting call" category. It is repeatable at will and affects the whole deployment (all configured operators/verifiers), not a single deposit. The specific Critical claim (nofn key corruption / permanent UTXO freeze from re-running verifier key collection) is not substantiated by the code because of the memoized `fetch_pubkeys_from_entities` guard.

### Likelihood Explanation
Requires the shipped default `client_verification=false` deployment configuration; no BTC cost, no special role, just network reachability to the aggregator's gRPC port. Trivially repeatable.

### Recommendation
Enforce authentication on the `Setup` RPC independent of `client_verification`, e.g. always require the `OnlyAggregatorAndSelf`-equivalent check for the aggregator's control-plane methods, or gate `setup`/other backend-only RPCs behind a Unix socket / mTLS regardless of the `client_verification` flag, since these methods perform network-wide configuration propagation.

### Proof of Concept
Not reproducible as a Critical nofn-corruption bug: a `cargo test` that starts `create_aggregator_grpc_server` with `client_verification=false`, dials with a bare `tonic::transport::Channel` (no `Identity`), and calls `setup()` will succeed (no `Status::unauthenticated`), confirming the unauthenticated-access finding. But asserting that this call changes `get_nofn_aggregated_xonly_pk()`'s output for an existing deposit will fail, because `fetch_verifier_keys` returns cached keys and the aggregated key remains identical across repeated `setup` calls with an unchanged verifier configuration — disproving the Critical freeze scenario as stated.

### Citations

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

**File:** core/src/rpc/aggregator.rs (L1318-1330)
```rust
    ) -> Result<Response<VerifierPublicKeys>, Status> {
        tracing::info!("Setup rpc called");
        self.check_compatibility_with_actors(CompatibilityCheckScope::Both)
            .await?;
        // Propagate Operators configurations to all verifier clients
        const CHANNEL_CAPACITY: usize = 1024 * 16;
        let (operator_params_tx, operator_params_rx) =
            tokio::sync::broadcast::channel(CHANNEL_CAPACITY);
        let operator_params_rx_handles = (0..self.get_verifier_clients().len())
            .map(|_| operator_params_rx.resubscribe())
            .collect::<Vec<_>>();

        let operators = self.get_operator_clients().to_vec();
```

**File:** core/src/aggregator.rs (L263-267)
```rust
        // Check if all keys are collected
        let all_collected = {
            let keys = keys_storage.read().await;
            keys.iter().all(|key| key.is_some())
        };
```
