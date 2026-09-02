This is a critical finding. `internal_send_tx` on the aggregator takes **any raw transaction** the caller provides and queues it for broadcast, and the aggregator's gRPC server does not enforce client certificates at all.

### Title
Unauthenticated `InternalSendTx` on Aggregator allows any caller to inject and broadcast arbitrary Bitcoin transactions - (`core/src/rpc/aggregator.rs`)

### Summary
`ClementineAggregator::InternalSendTx` accepts an arbitrary `raw_tx` and `fee_type` from the caller and unconditionally enqueues it for broadcast via `self.tx_sender.insert_try_to_send(...)`, with no validation that the transaction originated from, or was authorized by, the aggregator's own protocol flow. Unlike verifier/operator servers, the aggregator's gRPC server is explicitly documented and implemented to **not enforce client certificates** (`config.client_verification` controls whether the `OnlyAggregatorAndSelf` interceptor or a `Noop` interceptor is installed, and the aggregator is designed to run with no meaningful caller restriction) [1](#0-0) . This mirrors the `createStream()` bug class: a state-changing, event/side-effect-producing entry point that is reachable without any authorization check on the caller.

### Finding Description
The RPC is defined as "Internal" only by naming convention (`InternalSendTx`), but the `is_internal` check in the interceptor only restricts calls when `Interceptors::OnlyAggregatorAndSelf` is active on a verifier/operator server; the aggregator server explicitly does not install this restriction: [2](#0-1) 

The RPC method itself performs no additional caller-identity check — it deserializes the transaction and inserts it into the broadcast queue: [3](#0-2) 

Any Bitcoin transaction consensus-decodable can be submitted this way; the tx-sender then attempts to broadcast it via `send_raw_transaction`: [4](#0-3) 

The intended use, per the codebase's own e2e tests, is for the aggregator to submit fully protocol-signed transactions such as an operator's `Round` transaction on the operator's behalf: [5](#0-4) 

Because the endpoint is unauthenticated, any external caller reachable to the aggregator's TCP port can submit **any** transaction. This breaks the caller-authorization boundary: "a caller reaching a signing or state-changing method versus the party it is meant for." Concretely, an attacker can use this open queue to:
- Prematurely broadcast a protocol transaction that an operator or verifier has already fully pre-signed but not yet intended to reveal/broadcast (e.g., causing early exposure of a kickoff/round/challenge tx before its intended timing), which is "premature disclosure of a protocol commitment."
- Force fee-bumping/broadcast-queue side effects unrelated to actual protocol state by injecting arbitrary standard transactions into the aggregator's DB-backed sending queue, consuming its `tx_sender` infrastructure to broadcast transactions of the attacker's choosing.

This is the analog of `createStream()`: a public entry point with essentially no meaningful input-validation-based access control, letting anyone unauthenticated trigger a state-changing/broadcasting action that is meant to be restricted to the protocol's own actors.

### Impact Explanation
This matches the High-severity bucket explicitly listed: "an unauthenticated state-changing or broadcasting call." Concretely, since `InternalSendTx` will broadcast *any* transaction the caller supplies (not just ones tied to that deposit/operator's own signed state), an attacker can cause the aggregator to force-broadcast a fully-signed but time-sensitive transaction ahead of schedule (e.g., an operator's Round/Kickoff transaction), causing premature disclosure of a protocol commitment that the honest operator did not intend to reveal yet, potentially exposing WOTS keys or committing operators to states earlier than planned, and interfering with the timing assumptions built into the challenge/disprove/timeout game.

### Likelihood Explanation
Likelihood is high given the endpoint requires no privileged secret, key, or role — only network reachability to the aggregator's gRPC port, and the codebase itself documents that "the aggregator does not enforce client certificates" for this purpose. The only barrier is TLS transport encryption, not caller authentication, so any party who can connect to the aggregator endpoint (which per design must be reachable by external protocol participants for `NewDeposit`/`Withdraw`) can call it.

### Recommendation
Restrict `InternalSendTx` (and other `Internal*` aggregator RPCs) so that they can only be invoked by the aggregator's own internal callers (or with mTLS-based sender identity + tx-content-binding checks proving the tx was produced by the protocol's own signing flow), rather than relying on the aggregator's design assumption of "no client-cert enforcement is safe here." At minimum, validate that any transaction submitted to `InternalSendTx` corresponds to a transaction the aggregator itself previously requested/tracked (e.g., check against known pending kickoff/round/challenge txids from the DB) before queuing it for broadcast.

### Proof of Concept
1. Start an aggregator per the documented default posture (client certificate verification not enforced on the aggregator server, per `docs/usage.md`).
2. As an unauthenticated party with only TLS connectivity, connect a `ClementineAggregatorClient` to the aggregator's port.
3. Call `InternalSendTx` with a `SendTxRequest { raw_tx: <any protocol tx bytes learned/derived off-band or a fully pre-signed operator tx captured from the network>, fee_type: Cpfp }`.
4. Observe that `internal_send_tx` in `core/src/rpc/aggregator.rs:1270-1312` accepts the request without any per-caller check and queues it for broadcast via the tx sender, forcing early/unauthorized broadcast.

### Citations

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

**File:** core/src/servers.rs (L105-139)
```rust
            // Build TLS configuration
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

**File:** crates/clementine-tx-sender/src/lib.rs (L572-617)
```rust
    pub async fn send_no_funding_tx(
        &self,
        try_to_send_id: u32,
        tx: Transaction,
        tx_metadata: Option<TxMetadata>,
    ) -> Result<()> {
        match self.rpc.send_raw_transaction(&tx).await {
            Ok(sent_txid) => {
                tracing::debug!(
                    try_to_send_id,
                    "Successfully sent no funding tx with txid {}",
                    sent_txid
                );
                let _ = self
                    .db
                    .update_tx_debug_sending_state(try_to_send_id, "no_funding_send_success", true)
                    .await;
            }
            Err(e) => {
                let err_str = e.to_string();
                if rpc_errors::is_rejecting_replacement_error(&err_str) {
                    tracing::debug!(
                        try_to_send_id,
                        "No funding tx rejected (tx already in mempool): {err_str}"
                    );
                    return Ok(());
                } else {
                    tracing::error!(
                        "Failed to send no funding tx with try_to_send_id: {try_to_send_id:?} and metadata: {tx_metadata:?}"
                    );
                    log_error_for_tx!(
                        self.db,
                        try_to_send_id,
                        format!("send_raw_transaction error for no funding tx: {err_str}")
                    );
                }
                let _ = self
                    .db
                    .update_tx_debug_sending_state(try_to_send_id, "no_funding_send_failed", true)
                    .await;
                return Err(SendTxError::Other(eyre::eyre!(e)));
            }
        };

        Ok(())
    }
```

**File:** core/src/test/deposit_and_withdraw_e2e.rs (L1793-1807)
```rust
    // get first round's tx
    let round_tx =
        get_tx_from_signed_txs_with_type(&first_round_txs, TransactionType::Round).unwrap();
    // send first round tx
    let mut aggregator = actors.get_aggregator();
    aggregator
        .internal_send_tx(SendTxRequest {
            raw_tx: Some(RawSignedTx {
                raw_tx: bitcoin::consensus::serialize(&round_tx),
            }),
            fee_type: FeeType::Cpfp as i32,
        })
        .await
        .unwrap();
    tokio::time::sleep(std::time::Duration::from_secs(2)).await;
```
