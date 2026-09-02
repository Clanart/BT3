### Title
`internal_send_tx` is reachable by any unauthenticated caller when `client_verification` is disabled, allowing an arbitrary transaction to be queued for broadcast via the aggregator's `tx_sender` - ([File: core/src/servers.rs], [File: core/src/rpc/aggregator.rs])

### Summary
The `OnlyAggregatorAndSelf` interceptor is the only mechanism that enforces `is_internal` binding (i.e., that a gRPC method prefixed `Internal` may only be called by the aggregator's own client certificate). When `config.client_verification == false`, `create_grpc_server` installs `Interceptors::Noop` instead, which performs no peer-certificate check whatsoever, so `ClementineAggregator::internal_send_tx` becomes callable by any anonymous TCP client that can reach the aggregator's gRPC port.

### Finding Description
The claimed binding is: `caller_reaching_internal_send_tx == aggregator_self`. Tracing the code shows this binding is enforced only inside `only_aggregator_and_self` in `core/src/rpc/interceptors.rs` (checking `is_internal(&req)` against `leaf_cert == our_cert`) [1](#0-0) . That function is only invoked when the interceptor variant is `OnlyAggregatorAndSelf`; `Interceptors::Noop` simply returns `Ok(req)` unconditionally with no cert check at all [2](#0-1) .

`create_grpc_server` selects the interceptor variant purely based on `config.client_verification`: if it is `false`, `Noop` is installed for the aggregator's TCP listener regardless of the RPC method being internal [3](#0-2) .

`internal_send_tx` (guarded only by the `Internal` prefix convention, not by any in-handler authorization check) parses the attacker-supplied `raw_tx` and directly calls `self.tx_sender.insert_try_to_send(...)`, then commits the DB transaction — with no additional caller-identity check inside the handler itself [4](#0-3) .

Root cause: authorization for "internal-only" RPCs is delegated entirely to a transport-layer interceptor, and that interceptor is disabled as soon as `client_verification=false` — there is no defense-in-depth check inside `internal_send_tx` itself that would still enforce `is_internal` semantics when TLS client-cert verification is off.

### Impact Explanation
Under this configuration, an attacker who can reach the aggregator's public gRPC port can invoke `InternalSendTx` with an arbitrary, already fully-signed Bitcoin transaction, causing the aggregator to queue/broadcast it through its own `tx_sender` fee-management infrastructure. This is exactly the "unauthenticated state-changing or broadcasting call" High-severity category. Note that the attacker still needs a *validly signed* transaction (the aggregator does not sign anything on the attacker's behalf) — they cannot forge N-of-N signatures for bridge UTXOs they do not control. However, if the attacker manages to submit a transaction that spends a bridge-controlled UTXO it observed as fully presigned (e.g., a leaked/rebroadcastable presigned Challenge/Disprove/Reimburse transaction) via this path, they could force premature or out-of-order broadcast, interfering with the deadline-bound challenge/disprove/timeout flow. At minimum, this is an unauthenticated broadcasting call that bypasses the intended `is_internal` self-only binding, letting any unprivileged caller consume the aggregator's fee-bumping (`tx_sender`) service for arbitrary transactions.

### Likelihood Explanation
This requires `config.client_verification == false` and the `automation` feature compiled in, both of which are the question's stated preconditions. Whether this is the *default* deployment configuration could not be conclusively confirmed from the parts of `core/src/config/mod.rs` and `core/src/config/env.rs` inspected — the default value of `client_verification` was not directly observed in the excerpts pulled. If the default (or a documented supported configuration) is `client_verification = false`, this is trivially and repeatably exploitable by any network-reachable attacker at essentially zero cost (just a gRPC call). If `client_verification` defaults to `true` and is only disabled in test/dev configs, real-world likelihood is lower and confined to misconfigured deployments.

### Recommendation
Do not rely solely on the transport-layer interceptor for `Internal*` RPC authorization. Either (a) always enforce `OnlyAggregatorAndSelf`-style certificate checks for methods matching the `Internal` prefix regardless of `client_verification`, or (b) add an explicit in-handler check in `internal_send_tx` (and any other `Internal*` RPC) that rejects the call unless the transport layer has verified the caller's identity as the aggregator itself, independent of the global `client_verification` toggle.

### Proof of Concept
```rust
// cargo test -p clementine-core --features automation -- internal_send_tx_noop_bypass
// 1. Start an aggregator via create_aggregator_grpc_server with a BridgeConfig where
//    config.client_verification = false.
// 2. Connect a bare gRPC client (no TLS client certificate configured) to the aggregator's port.
// 3. Construct a fully-signed bitcoin::Transaction `tx` (e.g. spending attacker-owned regtest funds)
//    and call InternalSendTx(SendTxRequest { raw_tx: Some(tx.into()), fee_type: <any> }).
// 4. Assert the RPC returns Ok(Empty{}) (i.e., is NOT rejected with Status::unauthenticated),
//    proving caller_reaching_internal_send_tx != aggregator_self yet the call succeeded.
// 5. Query the tx_sender DB table / bitcoind mempool to confirm `tx` was inserted/broadcast,
//    demonstrating that insert_try_to_send executed on behalf of an unauthenticated caller.
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

**File:** core/src/rpc/aggregator.rs (L1270-1311)
```rust
    async fn internal_send_tx(
        &self,
        request: Request<clementine::SendTxRequest>,
    ) -> Result<Response<Empty>, Status> {
        #[cfg(not(feature = "automation"))]
        {
            Err(Status::unimplemented("Automation is not enabled"))
        }
        #[cfg(feature = "automation")]
        {
            let send_tx_req = request.into_inner();
            let fee_type = send_tx_req.fee_type();
            let signed_tx: bitcoin::Transaction = send_tx_req
                .raw_tx
                .ok_or(Status::invalid_argument("Missing raw_tx"))?
                .try_into()?;
            tracing::warn!(
                "Internal send tx rpc called with feetype: {:?}, tx hex: {}",
                fee_type,
                bitcoin::consensus::encode::serialize_hex(&signed_tx)
            );

            let mut dbtx = self.db.begin_transaction().await?;
            self.tx_sender
                .insert_try_to_send(
                    &mut dbtx,
                    None,
                    &signed_tx,
                    fee_type.try_into()?,
                    None,
                    &[],
                    &[],
                    &[],
                    &[],
                )
                .await
                .map_to_status()?;
            dbtx.commit()
                .await
                .map_err(|e| Status::internal(format!("Failed to commit db transaction: {e}")))?;
            Ok(Response::new(Empty {}))
        }
```
