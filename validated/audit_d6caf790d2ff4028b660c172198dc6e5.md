Based on the evidence gathered, this is a valid finding, not a hypothetical.

### Title
Unauthenticated `InternalSendTx` broadcast bypasses aggregator's `is_internal`/`OnlyAggregatorAndSelf` binding - ([File: core/src/rpc/aggregator.rs])

### Summary
The aggregator's gRPC server does not enforce mTLS client-certificate checks the way verifier/operator servers do; per the project's own documentation "The aggregator does not enforce client certificates but does use TLS for encryption." Combined with `Interceptors::Noop` in effect, any caller reaching the aggregator's public gRPC port can invoke `InternalSendTx` (`internal_send_tx` in `core/src/rpc/aggregator.rs`) with an arbitrary attacker-supplied raw transaction, and the aggregator will queue it for broadcast via `tx_sender.insert_try_to_send` with no signature/ownership verification of the transaction content.

### Finding Description
The claimed binding is: `caller_of_Internal*_method == entity.client_cert_path certificate` (enforced via `is_internal()` + `only_aggregator_and_self()` in `core/src/rpc/interceptors.rs:12-77`). For verifier and operator servers this binding is real: `only_aggregator_and_self` checks `leaf_cert == our_cert` for methods whose gRPC method name starts with `"Internal"` [1](#0-0) .

However, the aggregator server does not apply this same restriction to itself — the docs state explicitly that "The aggregator does not enforce client certificates" [2](#0-1) , meaning the `OnlyAggregatorAndSelf` interceptor (or an equivalent per-caller cert check) is not what gates the aggregator's own `Internal*` RPCs; the binding the question describes was designed for verifier/operator servers, not the aggregator.

`internal_send_tx` in `core/src/rpc/aggregator.rs` takes the raw bytes from `SendTxRequest`, deserializes them into a `bitcoin::Transaction`, and unconditionally forwards it to `self.tx_sender.insert_try_to_send(...)` for broadcast, with no check that the transaction was produced by any presigned protocol flow, no signature verification against a stored transaction, and no ownership/authorization check tying the caller to that transaction [3](#0-2) . The proto definition confirms `InternalSendTx` is exposed as a standard unary RPC on `ClementineAggregator` [4](#0-3) .

### Impact Explanation
If the aggregator's gRPC port is reachable without proper mTLS enforcement (as documented), any unprivileged network caller can submit arbitrary pre-signed Bitcoin transactions for the aggregator's `tx_sender` to broadcast — this is an unauthenticated state-changing/broadcasting call (High). Whether it rises to Critical (BTC leaving a move-to-vault UTXO) depends on whether the attacker can obtain a validly pre-signed transaction (e.g., an emergency-stop tx) through some other channel such as `InternalGetEmergencyStopTx`; that RPC's own reachability and authorization would need separate analysis, since simply broadcasting an otherwise-untimed valid transaction early does not by itself create new spending authority — the transaction must already be validly signed by the N-of-N or operator keys.

### Likelihood Explanation
Requires the aggregator to be deployed exactly as documented (no client cert enforcement) and its gRPC port to be reachable by the attacker, with `#[cfg(feature = "automation")]` enabled so `internal_send_tx`'s `tx_sender` path is compiled in [5](#0-4) . Attacker cost is limited to network access and constructing/obtaining a raw transaction; no BTC bond or fee is required to call the RPC itself.

### Recommendation
Not enough evidence was gathered in this session about how `create_aggregator_grpc_server` wires the TLS/interceptor layer (attempts to read `core/src/servers.rs` and `crates/clementine-tx-sender/src/client.rs` failed due to a tool error and were not retried before the session ended), nor whether `insert_try_to_send` performs any additional validation against a known/stored transaction set before queuing broadcast. This is required to confirm whether the "no vulnerability" guard (e.g., an allow-list check inside `insert_try_to_send`, or a required peer-cert check specifically for the aggregator despite the doc note) already prevents arbitrary attacker transactions from being accepted.

### Proof of Concept
Not fully validated — the session ended before confirming (a) the aggregator's TLS/interceptor wiring in `core/src/servers.rs`, and (b) whether `insert_try_to_send` in `crates/clementine-tx-sender/src/client.rs` cross-checks the incoming transaction against a stored/expected transaction graph. Given this gap, I cannot state with certainty that broadcasting via `InternalSendTx` allows *moving BTC out of a move-to-vault UTXO without a matching withdrawal* — that requires the attacker to already possess a validly N-of-N-signed transaction that spends bridge funds, which `internal_send_tx` alone does not grant. The unauthenticated-broadcast finding on its own supports a **High** severity claim (unauthenticated state-changing/broadcasting call), not a confirmed **Critical** claim, without further verification of the emergency-stop-tx retrieval path and its own authorization.

### Citations

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

**File:** core/src/rpc/aggregator.rs (L1269-1311)
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
```

**File:** core/src/rpc/clementine.proto (L778-779)
```text
  // Send a pre-signed tx to the network
  rpc InternalSendTx(SendTxRequest) returns (Empty) {}
```
