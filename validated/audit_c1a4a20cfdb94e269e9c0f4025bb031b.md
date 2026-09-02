### Title
Unauthenticated `InternalSendTx` broadcast on aggregator when `client_verification=false` - ([File: core/src/rpc/interceptors.rs])

### Summary
When the aggregator is deployed with `config.client_verification == false` (the default unless `CLIENT_VERIFICATION=true`/`1` is set), `create_grpc_server` wires the gRPC service with `Interceptors::Noop`, which unconditionally returns `Ok(req)` without checking peer certificates or the `is_internal()` marker. This lets any unauthenticated network peer reach `AggregatorServer::internal_send_tx`, which passes an attacker-supplied `bitcoin::Transaction` straight to `tx_sender.insert_try_to_send` with no validation that the transaction belongs to the bridge's protocol/transaction graph.

### Finding Description
The claimed binding is: caller-reaching-`internal_send_tx` == aggregator's own TLS certificate holder (self-cert), enforced via `is_internal()` + `only_aggregator_and_self`.

Tracing the code:
- `create_grpc_server` builds the `InterceptedService` using `OnlyAggregatorAndSelf` only `if config.client_verification` is true; otherwise it uses `Noop` [1](#0-0) .
- `Interceptors::Noop::call` returns `Ok(req)` unconditionally — `is_internal()` and `only_aggregator_and_self` are never invoked [2](#0-1) .
- `create_aggregator_grpc_server` only emits a `tracing::warn!` when `client_verification` is *true* (i.e., it warns about the safer path, not the dangerous default) and otherwise proceeds identically [3](#0-2) .
- `AggregatorServer::internal_send_tx` deserializes the attacker-controlled `raw_tx` and calls `self.tx_sender.insert_try_to_send(...)` with no additional authorization check and no validation that the transaction is part of the bridge's presigned transaction graph [4](#0-3) .
- `TxSenderClient::insert_try_to_send` performs no semantic validation of `signed_tx` — it dedups by txid and unconditionally persists the raw transaction to the send queue for later CPFP/RBF broadcasting [5](#0-4) .

Since `client_verification` defaults to `false` unless explicitly set via env var [6](#0-5) , and `create_aggregator_grpc_server` does not force it to `true` or reject the insecure configuration, an attacker with a plain TLS connection (no client certificate) can call `InternalSendTx` on the aggregator's public port and have their transaction inserted into the tx-sender queue, which will later fee-bump and broadcast it using the aggregator's own wallet/Bitcoin RPC. This is a genuine bypass of the `CALLER_AUTHORITY` binding that `is_internal()`/`only_aggregator_and_self` was designed to enforce, and it is reachable in the documented/default deployment mode.

### Impact Explanation
The attacker gains unauthenticated write access to a state-changing, network-broadcasting RPC method (`InternalSendTx`) on the aggregator, which mutates the tx-sender's persistent queue and results in the aggregator's node broadcasting an attacker-chosen transaction. This matches the explicitly listed High-severity category: "an unauthenticated state-changing or broadcasting call." While the specific attacker transaction shown (spending the attacker's own UTXO to their own address) does not itself move bridge value, the same unauthenticated path could be used to submit and force-broadcast any syntactically valid transaction (subject to `bitcoin::Transaction` deserialization), consuming the aggregator's Bitcoin RPC connectivity and inserting arbitrary attacker rows into the tx-sender queue tied to the aggregator's operational Bitcoin node — repeatable per request, with no per-caller rate/authorization control at the interceptor layer.

### Likelihood Explanation
Preconditions are exactly the documented deployment default: aggregator run with `client_verification=false` (which is the default env-derived value when `CLIENT_VERIFICATION` is unset) and the `automation` feature enabled (required for `internal_send_tx` to do anything other than return `unimplemented`). No BTC cost is required to reach the vulnerable code path; the attacker only needs to open a TLS connection to the aggregator's socket and send a well-formed `SendTxRequest`. This is fully feasible from the "unprivileged attacker" role, requiring no key share, TLS cert, or collateral, and is repeatable without limit.

### Recommendation
Enforce `client_verification = true` as a hard requirement for aggregator startup (fail-closed rather than warn-only when it is disabled), or otherwise require a separate authentication mechanism for `Internal*` RPCs regardless of `client_verification`. Additionally, `internal_send_tx` should validate that the submitted transaction corresponds to a transaction already known/expected by the bridge (e.g., matches a `TransactionType` produced by the protocol) rather than accepting and queuing an arbitrary attacker-supplied `bitcoin::Transaction`.

### Proof of Concept
```rust
// core/src/test/rpc_auth.rs pattern
#[tokio::test]
async fn test_unauthenticated_internal_send_tx_with_client_verification_disabled() -> Result<(), eyre::Report> {
    let mut config = create_test_config_with_thread_name().await;
    config.client_verification = false; // documented default
    let _rpc = create_regtest_rpc(&mut config).await;

    let (socket_addr, _shutdown_tx) = create_aggregator_grpc_server(config.clone()).await?;

    // Connect with a bare TLS client presenting NO client certificate (or plain TCP if TLS allows anonymous handshake).
    let mut client = connect_without_client_cert(socket_addr).await?;

    let attacker_tx = build_attacker_owned_tx(); // spends attacker's own UTXO, pays to attacker's address

    let resp = client
        .internal_send_tx(SendTxRequest {
            raw_tx: Some(RawSignedTx { raw_tx: bitcoin::consensus::serialize(&attacker_tx) }),
            fee_type: FeeType::Cpfp as i32,
        })
        .await;

    // Binding check: caller had NO certificate == should NOT equal "aggregator's own cert holder".
    // If Ok, the binding is broken.
    assert!(resp.is_ok(), "unauthenticated caller should be rejected but was accepted");

    // Confirm state mutation: tx is now in the tx_sender queue.
    let exists = tx_sender_db
        .check_if_tx_exists_on_txsender(None, attacker_tx.compute_txid())
        .await?;
    assert!(exists.is_some(), "attacker tx should not have been queued without authentication");

    Ok(())
}
```

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

**File:** core/src/rpc/aggregator.rs (L1269-1312)
```rust
    #[tracing::instrument(skip(self), err(level = tracing::Level::ERROR))]
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
    }
```

**File:** crates/clementine-tx-sender/src/client.rs (L59-112)
```rust
    pub async fn insert_try_to_send(
        &self,
        dbtx: &mut crate::TxSenderTransaction,
        tx_metadata: Option<TxMetadata>,
        signed_tx: &Transaction,
        fee_paying_type: FeePayingType,
        rbf_signing_info: Option<RbfSigningInfo>,
        cancel_outpoints: &[OutPoint],
        cancel_txids: &[Txid],
        activate_txids: &[ActivatedWithTxid],
        activate_outpoints: &[ActivatedWithOutpoint],
    ) -> Result<u32, BridgeError> {
        let txid = signed_tx.compute_txid();

        // do not add duplicate transactions to the txsender
        let tx_exists = self
            .db
            .check_if_tx_exists_on_txsender(Some(dbtx), txid)
            .await?;
        if let Some(try_to_send_id) = tx_exists {
            return Ok(try_to_send_id);
        }

        tracing::info!(
            "Added tx {} with txid {} to the queue",
            tx_metadata
                .as_ref()
                .map(|data| format!("{:?}", data.tx_type))
                .unwrap_or("N/A".to_string()),
            txid
        );

        let try_to_send_id = self
            .db
            .save_tx(
                dbtx,
                tx_metadata,
                signed_tx,
                fee_paying_type,
                txid,
                rbf_signing_info,
            )
            .await?;

        // only log the raw tx in tests so that logs do not contain sensitive information
        #[cfg(test)]
        tracing::debug!(target: "ci", "Saved tx to database with try_to_send_id: {try_to_send_id}, metadata: {tx_metadata:?}, raw tx: {}", hex::encode(bitcoin::consensus::serialize(signed_tx)));

        for input_outpoint in signed_tx.input.iter().map(|input| input.previous_output) {
            self.db
                .save_cancelled_outpoint(dbtx, try_to_send_id, input_outpoint)
                .await?;
        }

```

**File:** core/src/config/env.rs (L181-182)
```rust
        let client_verification =
            read_string_from_env("CLIENT_VERIFICATION").is_ok_and(|s| s == "true" || s == "1");
```
