Confirmed: `insert_try_to_send` performs no validation of tx origin or bridge relevance — it deserializes any raw tx, dedups by txid, and inserts into `tx_sender_try_to_send_txs` for CPFP/RBF fee-bumping and broadcast by the tx-sender background task. [1](#0-0) [2](#0-1) 

### Title
Unauthenticated `InternalSendTx` allows any network caller to queue arbitrary transactions for the aggregator's fee-paying tx-sender - ([File: core/src/rpc/interceptors.rs])

### Summary
`is_internal` gating is only enforced inside the `OnlyAggregatorAndSelf` interceptor branch; the aggregator's gRPC server is, by documented design, run without client certificate verification (`Interceptors::Noop`), so the `grpc-method`-derived internal check never runs on the aggregator. As a result, any unauthenticated network client that can reach the aggregator's public gRPC port can call `ClementineAggregator::InternalSendTx` and insert an attacker-supplied raw transaction into `tx_sender_try_to_send_txs`, a state-changing/broadcasting call that was intended to be restricted to the aggregator's own internal automation.

### Finding Description
Binding claimed: `CALLER_AUTHORITY` — "a party reaching `InternalSendTx` == a party the interceptor/protocol intends to allow (i.e., the aggregator itself)".

Trace:
- `AddMethodMiddlewareLayer` stamps every request with a `grpc-method` header derived purely from the URI path, with no cryptographic binding to the caller [3](#0-2) .
- `is_internal` reads that header to decide whether a call is "Internal*", but this function is only invoked from `only_aggregator_and_self`, which is the `OnlyAggregatorAndSelf` interceptor variant [4](#0-3) .
- `create_grpc_server` selects the interceptor based on `config.client_verification`: `OnlyAggregatorAndSelf` when true, `Noop` (pass-through, no cert check at all) when false [5](#0-4) .
- Documentation and code explicitly state the aggregator's port is intended to run without client-cert enforcement: "The aggregator does not enforce client certificates but does use TLS for encryption," and `create_aggregator_grpc_server` even logs a warning if `client_verification` is turned on for the aggregator (implying it's not the expected mode) [6](#0-5) [7](#0-6) .
- Under `Noop`, `is_internal`/the "Internal-only" restriction is never evaluated, so `InternalSendTx` is exposed exactly like any public aggregator method to any TCP client reaching the port.
- `internal_send_tx` deserializes the attacker-provided `raw_tx`, logs it, and calls `insert_try_to_send`, which performs no ownership/authorization check — it only dedups by txid and persists the transaction for the tx-sender's CPFP/RBF broadcasting loop [1](#0-0) [8](#0-7) .

Existing guards (`only_aggregator_and_self`, `is_internal`) do not apply because they are structurally excluded from the aggregator's deployment path by design. No other check (deposit validity, signature verification, etc.) sits in front of `internal_send_tx`.

### Impact Explanation
The mutation is real and reachable: an unauthenticated caller can insert arbitrary transaction rows into `tx_sender_try_to_send_txs`, which the aggregator's automation will subsequently attempt to CPFP/RBF-fee-bump and broadcast using the aggregator's own wallet/fee-bumping funds. However, `insert_try_to_send` only stores/broadcasts a transaction the attacker must already supply pre-signed — it cannot forge signatures for bridge UTXOs, so it cannot move BTC out of a move-to-vault UTXO or forge an N-of-N spend. The actual damage is limited to abusing the aggregator's fee-bumping infrastructure to relay/fee-bump attacker-chosen transactions (consuming the aggregator's fee-bumping wallet funds) — this is an unauthenticated state-changing/broadcasting call, matching the High severity bucket ("an unauthenticated state-changing or broadcasting call"), but does not itself demonstrate BTC leaving a vault, a wrongful reimbursement, or frozen funds.

### Likelihood Explanation
This requires the deployed aggregator to run with `client_verification=false` (Noop), which per the docs/code comments is the intended/expected default operational mode for the aggregator specifically (verifier/operator nodes use `OnlyAggregatorAndSelf`, aggregator is documented as unauthenticated for client certs). Given the attacker model explicitly grants "send requests to the aggregator's public gRPC port," this precondition is squarely in scope and requires no special cost beyond crafting a gRPC request with a raw, self-signed Bitcoin transaction.

### Recommendation
Restrict `Internal*`-prefixed methods on the aggregator server the same way as on verifier/operator servers: require a caller-presented shared secret/mTLS client cert bound specifically to the aggregator's own background task caller (e.g., loopback-only Unix socket or a dedicated internal-only listener), rather than relying on a header injected by middleware with no relation to authentication. At minimum, bind `InternalSendTx` (and other `Internal*` aggregator RPCs) to a separate non-public listener (e.g., localhost-only or Unix socket) so they are unreachable from the public gRPC port regardless of `client_verification`.

### Proof of Concept
```rust
// core/src/test/rpc_auth.rs (new test)
#[tokio::test]
async fn test_unauthenticated_internal_send_tx_on_aggregator() -> Result<(), eyre::Report> {
    let mut config = create_test_config_with_thread_name().await;
    let _rpc = create_regtest_rpc(&mut config).await;
    config.client_verification = false; // Noop interceptor, matches documented aggregator default

    let (socket_addr, _shutdown) = create_aggregator_grpc_server(config.clone()).await?;

    // Attacker: no client cert, no aggregator identity
    let endpoint = format!("http://{}", socket_addr); // or plain TCP client with no mTLS material
    let mut client = ClementineAggregatorClient::connect(endpoint).await?;

    let raw_tx = /* attacker's own self-signed, fee-bearing tx */;
    let resp = client.internal_send_tx(SendTxRequest {
        raw_tx: Some(RawSignedTx { raw_tx: bitcoin::consensus::serialize(&raw_tx) }),
        fee_type: FeeType::Cpfp as i32,
    }).await;

    assert!(resp.is_ok(), "unauthenticated caller succeeded calling InternalSendTx");

    // Assert both sides of the CALLER_AUTHORITY binding:
    // LHS: caller identity == None/unauthenticated
    // RHS: protocol-intended caller == aggregator-self only
    // Observe the queued row exists despite mismatch:
    let exists = tx_sender_db.check_if_tx_exists_on_txsender(None, raw_tx.compute_txid()).await?;
    assert!(exists.is_some(), "attacker-supplied tx was queued in tx_sender_try_to_send_txs without authorization");
    Ok(())
}
```

### Citations

**File:** core/src/rpc/aggregator.rs (L1270-1310)
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
```

**File:** crates/clementine-tx-sender/src/client.rs (L59-101)
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
```

**File:** core/src/utils.rs (L277-291)
```rust
        Box::pin(async move {
            let path = req.uri().path();

            let grpc_method =
                if let &[_, _, method] = &path.split("/").collect::<Vec<&str>>().as_slice() {
                    Some(method.to_string())
                } else {
                    None
                };

            if let Some(grpc_method) = grpc_method {
                if let Ok(grpc_method) = HeaderValue::from_str(&grpc_method) {
                    req.headers_mut().insert("grpc-method", grpc_method);
                }
            }
```

**File:** core/src/rpc/interceptors.rs (L12-33)
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

**File:** docs/usage.md (L192-203)
```markdown
## RPC Authentication

Clementine uses mutual TLS (mTLS) to secure gRPC communications between entities
and to authenticate clients. Client certificates are verified and filtered by
the verifier/operator to ensure that:

1. Verifier/Operator methods can only be called by the aggregator (using
   aggregator's client certificate `aggregator_cert_path`)
2. Internal methods can only be called by the entity's own client certificate
   (using the entity's client certificate `client_cert_path`)

The aggregator does not enforce client certificates but does use TLS for encryption.
```
