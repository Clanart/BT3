### Title
Unauthenticated `InternalSendTx` bypasses self-only gating when `client_verification=false`, allowing injection of conflicting fee-bump transactions into the aggregator's broadcast queue - (File: core/src/rpc/aggregator.rs)

### Summary
`internal_send_tx` (core/src/rpc/aggregator.rs:1270) performs no caller-authentication of its own; it relies entirely on the transport-level `only_aggregator_and_self` interceptor to enforce that "Internal*"-prefixed RPCs are only callable by the aggregator itself (leaf cert == `our_cert`). When the deployment runs with `client_verification=false`, `servers.rs` installs `Interceptors::Noop` instead, which passes every request through unchecked, so any unauthenticated caller who can reach the gRPC port can invoke `internal_send_tx` and have `tx_sender.insert_try_to_send` queue an arbitrary raw transaction for broadcasting/fee-bumping.

### Finding Description
The intended binding is: `CALLER_IDENTITY(request) == self-cert (aggregator's own client cert)`, enforced only by `only_aggregator_and_self` for methods whose gRPC path starts with `"Internal"` [1](#0-0) [2](#0-1) . This check exists only inside the `OnlyAggregatorAndSelf` interceptor variant; the alternative `Noop` variant, which `servers.rs` selects whenever `config.client_verification` is `false`, returns `Ok(req)` unconditionally with no identity check at all [3](#0-2) [4](#0-3) .

`internal_send_tx` itself does no additional authorization — it deserializes `raw_tx` from the request and hands it straight to `self.tx_sender.insert_try_to_send(...)` with empty cancel/activate lists [5](#0-4) . `insert_try_to_send` performs no ownership or provenance check on which inputs the supplied transaction spends: it computes the txid, checks only for exact-txid duplication, saves the tx, and records its inputs as "cancelled outpoints" (invalidating other queued sends of the same outpoints once this tx confirms) [6](#0-5) . There is no check that the caller is the aggregator, nor that the inputs spent by `signed_tx` belong to a transaction the caller is entitled to fee-bump.

Consequently, under `client_verification=false`, an unprivileged network caller can call `InternalSendTx` directly and get an arbitrary attacker-supplied transaction inserted into the aggregator's own broadcast/fee-bumping queue, competing for the same UTXO(s) as a legitimate, deadline-bound protocol transaction (e.g. Reimburse/Payout/Challenge) that the aggregator has already queued through its normal flow (e.g. optimistic payout enqueue at aggregator.rs:1242-1253). None of the listed guards (`only_aggregator_and_self`, `is_deposit_valid`, `is_profitable`, `SPV::verify`, presigned tx graph, DB uniqueness) apply here, because `only_aggregator_and_self` is simply not active in this mode, and the txsender-level dedup only rejects exact-txid repeats, not conflicting-input transactions.

### Impact Explanation
This is an unauthenticated state-changing/broadcasting call: an attacker with no TLS certificate, key share, or aggregator role can cause the aggregator's tx-sender to accept and attempt to broadcast an arbitrary transaction. If the input in question is one that permits any valid witness to be constructed by the attacker (e.g. an anyone-can-spend fee-bumping/anchor-style output that a CPFP child spends), the attacker can inject a competing spend of that exact input, racing the aggregator's own legitimate deadline-bound transaction (Reimburse/Payout/Challenge) for confirmation and potentially making it unconfirmable in time — a High severity outcome per the defined impact categories. This is repeatable for every deposit/withdrawal cycle for as long as `client_verification` stays disabled, since the underlying gating gap is structural (interceptor-only enforcement with no defense-in-depth in the handler itself).

### Likelihood Explanation
Requires the deployment to run with `client_verification=false` (a real, supported configuration flag, not test-only) and the `automation` feature compiled in — both stated as preconditions. Given those, the attacker needs only network access to the aggregator's public gRPC port, no BTC cost beyond constructing a transaction, and (for real conflicting-input impact) the ability to produce a valid witness for the targeted input, which is only feasible if that input's spending condition does not require an N-of-N/verifier signature (e.g. an anyone-can-spend CPFP anchor). I was not able to fully confirm from the available index whether the aggregator's CPFP fee-bumping construction uses such an anyone-can-spend anchor output for Reimburse/Payout transactions; this detail affects whether the attacker can craft a *valid* conflicting spend versus merely queuing an unbroadcastable transaction. Regardless, the lack of any authentication inside `internal_send_tx`/`insert_try_to_send` itself (relying solely on the interceptor) is a real gap independent of that detail.

### Recommendation
Do not rely solely on the transport-level interceptor for "Internal*" methods. Add an explicit self-identity check inside `internal_send_tx` (or reject the call outright unless `client_verification` is enabled), and additionally have `insert_try_to_send`/`internal_send_tx` validate that the inputs of `signed_tx` correspond to outpoints the aggregator itself already has queued/owns before accepting them into the fee-bumping queue.

### Proof of Concept
```rust
// cargo test -p core --features automation -- internal_send_tx_noop_unauthenticated
// 1. Start an aggregator test harness with config.client_verification = false
//    (forces Interceptors::Noop per servers.rs create_grpc_server).
// 2. Through the normal flow, enqueue a legitimate deadline-bound tx (e.g. call
//    optimistic_payout or the Reimburse flow) so tx_sender holds a queued tx
//    spending outpoint O.
// 3. As an unauthenticated client (no client cert, or any TLS identity, since
//    Noop performs no check), call InternalSendTx with a raw_tx that also spends
//    outpoint O and fee_type = CPFP.
// 4. Assert: (a) the RPC returns Ok(Empty{}) despite no self-cert being
//    presented — i.e. CALLER_IDENTITY(request) != self-cert yet the call
//    succeeded; (b) query the tx_sender DB and assert the attacker's tx is now
//    present alongside the legitimate tx, both referencing outpoint O with no
//    ownership check having been performed by insert_try_to_send.
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

**File:** crates/clementine-tx-sender/src/client.rs (L70-117)
```rust
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

        for outpoint in cancel_outpoints {
            self.db
                .save_cancelled_outpoint(dbtx, try_to_send_id, *outpoint)
                .await?;
        }
```
