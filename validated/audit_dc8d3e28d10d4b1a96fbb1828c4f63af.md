### Title
Unauthenticated `InternalSendTx` accepts and broadcasts attacker-chosen Bitcoin transactions via the aggregator's tx_sender queue - ([File: core/src/rpc/aggregator.rs])

### Summary
`ClementineAggregator::internal_send_tx` deserializes a caller-supplied `SendTxRequest.raw_tx` and forwards it directly to `TxSenderClient::insert_try_to_send` with no check that the transaction is one the protocol actually produced. The `is_internal()` gate that is supposed to restrict `Internal*`-prefixed RPCs to self-only callers is only enforced by the `OnlyAggregatorAndSelf` interceptor; the `Noop` interceptor used on the aggregator's gRPC server never calls `is_internal`, so any unauthenticated caller reaching the aggregator's public port can invoke `InternalSendTx`.

### Finding Description
The intended binding is: `caller_of_InternalSendTx == aggregator_itself` (enforced elsewhere for verifier/operator servers via the `Internal` method-name convention checked in `is_internal`). On the aggregator server this binding does not hold, because:

- `is_internal` simply checks whether the gRPC method name starts with `"Internal"` [1](#0-0) , and this check is only exercised inside `only_aggregator_and_self` [2](#0-1) .
- `Interceptors::Noop` is a pass-through that never calls `is_internal` or checks any certificate [3](#0-2) .
- `create_grpc_server` selects `Noop` whenever `config.client_verification` is false [4](#0-3) , and the project's own documentation states this is the expected posture for the aggregator: "The aggregator does not enforce client certificates but does use TLS for encryption" [5](#0-4) .
- `internal_send_tx` performs zero validation of the transaction's structure, provenance, or relationship to any deposit/protocol tx before queuing it: it just deserializes `raw_tx` and calls `self.tx_sender.insert_try_to_send(...)` [6](#0-5) .

Because of this, an attacker who can reach the aggregator's public gRPC port can sign any transaction of their own choosing (spending their own funds/UTXOs) and call `InternalSendTx`, causing the aggregator to accept it into its `tx_sender` queue and CPFP-fee-bump/broadcast it as if it were an aggregator-authorized action — with no check that it is a presigned N-of-N or protocol-derived transaction at all.

By contrast, `SendMoveToVaultTx` does perform structural validation (input/output counts, exact output amounts, and exact output script pubkeys matching the NofN + security-council vault address) [7](#0-6) , and it is not "Internal"-prefixed — it is intentionally public per its proto comment ("Used by the clementine-backend service to initiate a deposit") [8](#0-7) . An attacker cannot forge a valid witness for an existing deposit outpoint's N-of-N script, so `SendMoveToVaultTx` cannot be abused to move real vault funds — at most an attacker can pay their own coins into the vault address, which is not a loss of bridge value. `InternalSendTx`, however, has no such structural guard, and its name/proto comment ("Send a pre-signed tx to the network") signals it is meant only for the aggregator's own automation to re-submit already protocol-validated transactions, not to accept arbitrary externally supplied ones.

### Impact Explanation
This is an unauthenticated state-changing/broadcasting RPC call reachable on the aggregator's public gRPC port. It does not by itself let an attacker steal a move-to-vault UTXO or forge N-of-N signatures (Bitcoin consensus still requires a valid signature for whatever input the attacker's transaction spends, and the attacker can only spend their own coins). The concrete exploitable effect is that the aggregator's transaction-sending/fee-bumping infrastructure can be driven by an unauthenticated third party to accept and process arbitrary transactions, matching the "High - an unauthenticated state-changing or broadcasting call" category rather than a Critical fund-loss category. It is repeatable per attacker call and not specific to any single deposit/operator.

### Likelihood Explanation
The only precondition is that the aggregator's gRPC server is deployed with `client_verification = false` (i.e., the `Noop` interceptor), which the project's own documentation describes as the aggregator's standard operating mode ("does not enforce client certificates"). Under that documented configuration, exploitation requires nothing more than network access to the aggregator's gRPC port and a self-signed Bitcoin transaction — no keys, collateral, or special role are needed.

### Recommendation
Enforce the `is_internal` restriction independent of TLS client-certificate configuration for `Internal*`-prefixed aggregator RPCs (e.g., reject calls to `InternalSendTx`/`InternalGetEmergencyStopTx` when the interceptor is `Noop`, or require these to only be invocable via a loopback/local transport rather than the public TCP listener), and/or add protocol-level validation in `internal_send_tx` that the submitted transaction corresponds to a transaction the aggregator itself already produced/tracks (e.g., match against `TxMetadata`/deposit records) before queuing it for CPFP broadcast.

### Proof of Concept
```rust
// cargo test in core/src/rpc/aggregator.rs test module, using create_aggregator_unix_server/create_aggregator_grpc_server
// with config.client_verification = false (forces Interceptors::Noop).
#[tokio::test]
async fn poc_unauthenticated_internal_send_tx() {
    let mut config = create_test_config_with_thread_name().await;
    config.client_verification = false; // forces Noop interceptor, no cert required
    let regtest = create_regtest_rpc(&mut config).await;
    let rpc = regtest.rpc();

    // Start aggregator server without any client cert/identity provided by the caller.
    let (addr, _shutdown) = create_aggregator_grpc_server(config.clone()).await.unwrap();
    let mut aggregator = /* connect without presenting a client certificate */;

    // Attacker crafts and signs their own transaction spending their own coins.
    let attacker_tx = /* build+sign arbitrary attacker-owned transaction */;

    let resp = aggregator
        .internal_send_tx(SendTxRequest {
            raw_tx: Some(attacker_tx.clone().into()),
            fee_type: FeeType::Cpfp as i32,
        })
        .await;

    // Assert the binding is broken: an unauthenticated, non-aggregator-produced tx
    // is accepted and queued rather than rejected for lacking provenance.
    assert!(resp.is_ok());
    // Verify it was inserted into the tx_sender queue (i.e., DB row / mempool broadcast),
    // confirming acceptance without any aggregator/self authorization.
}
```

### Citations

**File:** core/src/rpc/interceptors.rs (L12-20)
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

**File:** core/src/rpc/interceptors.rs (L62-70)
```rust
    if is_internal(&req) {
        if leaf_cert == our_cert {
            Ok(req)
        } else {
            Err(Status::unauthenticated(
                "Unauthorized call to internal method (not self)",
            ))
        }
    } else if leaf_cert == aggregator_cert || leaf_cert == our_cert {
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

**File:** core/src/rpc/aggregator.rs (L2019-2073)
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
            // check output scriptpubkeys
            let verifier_keys = self.fetch_verifier_keys().await?;
            let nofn_xonly_pk =
                bitcoin::XOnlyPublicKey::from_musig2_pks(verifier_keys.clone(), None).map_err(
                    |e| {
                        Status::internal(format!(
                            "Failed to aggregate verifier public keys, err: {e}, pubkeys: {verifier_keys:?}"
                        ))
                    },
                )?;
            let nofn_script = Arc::new(CheckSig::new(nofn_xonly_pk));
            let security_council_script = Arc::new(Multisig::from_security_council(
                self.config.security_council.clone(),
            ));

            let (addr, _) = create_taproot_address(
                &[
                    nofn_script.to_script_buf(),
                    security_council_script.to_script_buf(),
                ],
                None,
                self.config.protocol_paramset().network,
            );
            let bridge_script_pubkey = addr.script_pubkey();

            if !(movetx.output[1].script_pubkey
                == anchor_output(self.config.protocol_paramset().anchor_amount()).script_pubkey
                && movetx.output[0].script_pubkey == bridge_script_pubkey)
            {
                return Err(Status::invalid_argument(
                    format!("Transaction is not a movetx, output scriptpubkeys are not correct, expected: (vault: {:?}, anchor: {:?}), got: (vault: {:?}, anchor: {:?})",
                    bridge_script_pubkey,
                    anchor_output(self.config.protocol_paramset().anchor_amount()).script_pubkey,
                    movetx.output[0].script_pubkey,
                    movetx.output[1].script_pubkey,
                )));
            }
```

**File:** core/src/rpc/clementine.proto (L778-781)
```text
  // Send a pre-signed tx to the network
  rpc InternalSendTx(SendTxRequest) returns (Empty) {}

  rpc SendMoveToVaultTx(SendMoveTxRequest) returns (Txid) {}
```
