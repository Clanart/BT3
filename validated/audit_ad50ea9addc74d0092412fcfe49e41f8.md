Confirmed: no additional validation exists before broadcast. `send_no_funding_tx`/`send_rbf_tx` simply call `self.rpc.send_raw_transaction(&final_tx)` [1](#0-0)  with no check that the transaction spends the aggregator's own presigned tree — the only gate is fee/PSBT plumbing.

### Title
Unauthenticated `InternalSendTx` lets any caller queue an arbitrary attacker-chosen transaction for aggregator broadcast - (core/src/rpc/aggregator.rs)

### Summary
`AggregatorServiceImpl::internal_send_tx` deserializes `SendTxRequest.raw_tx` into a `bitcoin::Transaction` and immediately hands it to `self.tx_sender.insert_try_to_send(...)` with no check that the transaction originates from, or spends any UTXO belonging to, this aggregator's presigned transaction graph. When the aggregator's gRPC server is run with `client_verification = false`, the `Interceptors::Noop` variant admits every request unauthenticated, so any network caller can make the aggregator's tx-sender broadcast a transaction of their choosing.

### Finding Description
The binding that should hold is: `tx_sender's queued/broadcast transaction` == `a transaction from this aggregator's own presigned N-of-N/operator transaction graph (Round, Kickoff, Reimburse, etc.)`. `internal_send_tx` breaks this binding: [2](#0-1) 

The request is only gated by:
1. `#[cfg(feature = "automation")]` — a build-time flag, not an authorization check.
2. The gRPC interceptor chosen at server start-up. When `config.client_verification == false`, `create_grpc_server` wraps the service in `Interceptors::Noop`, which returns `Ok(req)` unconditionally for every method, including ones with the `Internal` prefix: [3](#0-2)  and [4](#0-3) .

Unlike the sibling RPC `send_move_to_vault_tx`, which validates input/output counts, output amounts against `bridge_amount`, and output script pubkeys against the aggregated N-of-N/security-council scripts before calling the tx-sender [5](#0-4) , `internal_send_tx` performs zero structural or provenance validation on the deserialized transaction. It passes it straight to `TxSenderClient::insert_try_to_send`, which only records fee-paying type, cancel/activate dependencies, and persists the raw tx for later broadcast [6](#0-5) . Downstream, the sender loop (`send_no_funding_tx`, `send_rbf_tx`) does nothing but fee-bump/sign-if-needed and call `send_raw_transaction` — there is no re-check against any known/expected outpoint set before broadcast [7](#0-6) .

Exploit flow: an attacker crafts any valid, fully-signed Bitcoin transaction they can fund themselves (e.g., spending their own UTXO), wraps its serialized bytes in `SendTxRequest{raw_tx, fee_type}`, and calls `InternalSendTx` on the aggregator's open port. With `client_verification=false`, the call is admitted by `Noop`, deserialized, and queued; the tx-sender then attempts to broadcast it using the aggregator's own Bitcoin RPC connection/wallet for fee funding as configured.

### Impact Explanation
This is an unauthenticated, state-changing/broadcasting call reachable without any credential, matching the "High" category defined in the rubric ("an unauthenticated state-changing or broadcasting call"). Direct bridge-value theft is not established here because the attacker cannot forge inputs they don't control — they can only get the aggregator's infrastructure to relay/fee-bump transactions of their choosing, which can be repeated for any transaction and imposes broadcasting-infrastructure abuse (e.g., using the aggregator's node/wallet to fund and propagate attacker transactions, potential fee-fund drain if `FeePayingType::RBF`/CPFP wallet funding is used, and queue/database pollution). It does not by itself move bridge BTC out of a vault or credit a false reimbursement, since the attacker cannot supply a valid N-of-N signature over a vault UTXO — but it is a clear authorization-binding break at the RPC layer with a working attacker-controlled queuing/broadcasting primitive.

### Likelihood Explanation
Requires the aggregator to be deployed with `config.client_verification == false` and `feature = "automation"` enabled, and the attacker able to reach the aggregator's gRPC port. Documented/shipped configs (`scripts/docker/configs/testnet4/bridge_config.toml`, `.env.regtest`) set `client_verification = true`/`CLIENT_VERIFICATION=1` [8](#0-7) , so this is not the default/documented deployment; it is only exploitable if an operator explicitly disables client verification, a config choice explicitly called out as a precondition of this question. Given that precondition, the attacker cost is just gas/fee for their own transaction and the call is trivially repeatable.

### Recommendation
Add provenance validation to `internal_send_tx` (or the underlying `insert_try_to_send` call site) similar to `send_move_to_vault_tx`: verify the transaction's inputs spend outpoints known to belong to this aggregator's/operator's presigned transaction graph (or verify a signature/authorization token issued by the entity that produced the presigned tx) before queuing for broadcast, independent of `client_verification`.

### Proof of Concept
```rust
#[tokio::test]
async fn test_internal_send_tx_unauthenticated_arbitrary_broadcast() {
    // 1. Start aggregator with config.client_verification = false, automation enabled, regtest.
    // 2. Build a certificate-less/plain client (or one using an arbitrary/self-signed cert)
    //    connecting to the aggregator's public gRPC address.
    // 3. Craft an attacker-funded, fully-signed bitcoin::Transaction unrelated to any
    //    deposit/round/kickoff presigned by this aggregator (e.g. spend attacker's own regtest UTXO).
    // 4. Call client.internal_send_tx(SendTxRequest { raw_tx: Some(serialize(attacker_tx)), fee_type: FeeType::NoFunding }).
    // assert!(response.is_ok());
    // 5. Assert binding is broken:
    //    assert!(tx_sender_db.check_if_tx_exists_on_txsender(None, attacker_tx.compute_txid()).await.unwrap().is_some());
    //    ensure_tx_onchain(&rpc, attacker_tx.compute_txid()).await.unwrap(); // tx appears in regtest mempool/chain
    //    // despite attacker_tx never being part of any deposit's presigned N-of-N tree.
}
```

### Citations

**File:** crates/clementine-tx-sender/src/lib.rs (L571-588)
```rust
    #[tracing::instrument(skip_all, fields(try_to_send_id, tx_meta=?tx_metadata))]
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
```

**File:** core/src/rpc/aggregator.rs (L1278-1306)
```rust
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
```

**File:** core/src/rpc/aggregator.rs (L2019-2036)
```rust
            // check if transaction is a movetx
            if movetx.input.len() != 1 || movetx.output.len() != 2 {
                return Err(Status::invalid_argument(
                    "Transaction is not a movetx, input or output lengths are not correct",
                ));
            }
            // check output values
            // movetx always has 0 sat anchor output
            if !(movetx.output[0].value == self.config.protocol_paramset().bridge_amount
                && movetx.output[1].value == Amount::from_sat(0))
            {
                return Err(Status::invalid_argument(format!(
                    "Transaction is not a movetx, output sat values are not correct, should be ({}, 0), got ({}, {})",
                    self.config.protocol_paramset().bridge_amount,
                    movetx.output[0].value,
                    movetx.output[1].value,
                )));
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

**File:** crates/clementine-tx-sender/src/client.rs (L59-70)
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
```

**File:** scripts/docker/configs/testnet4/bridge_config.toml (L77-77)
```text
client_verification = true
```
