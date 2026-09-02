Confirmed: `send_cpfp_tx` in `crates/clementine-tx-sender/src/cpfp.rs` funds CPFP fee-bumping for any transaction queued via `insert_try_to_send` using the entity's own wallet UTXOs (`create_fee_payer_utxo` sends real BTC from `self.rpc` wallet to build fee-payer UTXOs, then broadcasts them and later spends them to bump the parent tx) — with no check that the parent transaction relates to any known bridge protocol transaction.

### Title
Unauthenticated `InternalSendTx` on aggregator allows anyone to make the bridge wallet pay Bitcoin fees for arbitrary attacker-supplied transactions - (File: core/src/rpc/aggregator.rs)

### Summary
The aggregator's `internal_send_tx` gRPC method takes an arbitrary caller-supplied raw Bitcoin transaction and a `fee_type`, and inserts it directly into the `TxSender` queue for automatic fee-bumping/broadcasting, with no validation that the transaction belongs to any legitimate bridge flow. Unlike the operator/verifier servers, which gate `Internal*` RPCs to the entity's own mTLS client certificate via the `is_internal` check in `core/src/rpc/interceptors.rs`, the aggregator is documented to run without client-certificate enforcement (`docs/usage.md:203`: "The aggregator does not enforce client certificates but does use TLS for encryption"). Combined, this means any network caller can reach a method whose name and design intent ("Internal") is that it should only be callable by the entity itself.

### Finding Description
`internal_send_tx` in `core/src/rpc/aggregator.rs:1270-1312` deserializes `send_tx_req.raw_tx` directly from the request and calls `self.tx_sender.insert_try_to_send(...)` with the caller-chosen `fee_type`, without checking that the transaction is a known/expected protocol transaction (kickoff, payout, challenge, etc.) or that it was produced by the aggregator's own signing pipeline: [1](#0-0) 

Once queued, the `TxSender`'s CPFP logic will fund a "fee payer" transaction from the entity's own Bitcoin wallet, broadcast it, and repeatedly bump its fee, entirely using the bridge operator/aggregator's real BTC: [2](#0-1) 

This "Internal"-prefixed RPC is intended to be restricted the same way as other `Internal*` methods, which the interceptor code enforces by checking the gRPC method name prefix against the caller's leaf certificate: [3](#0-2) [4](#0-3) 

However, per project documentation, the aggregator server does not enforce this restriction at all: [5](#0-4) 

This closely mirrors the reported `sponsor()` bug class: a state-changing method meant to be reachable only by a trusted internal caller (the entity itself/factory) is exposed with no authorization check to any caller who can reach the network endpoint.

### Impact Explanation
An unprivileged network caller who can reach the aggregator's gRPC port can force the bridge's own wallet to spend real BTC building and rebroadcasting fee-payer UTXOs for arbitrary attacker-chosen transactions, and can inject unrelated or conflicting transactions into the automated sending/bumping pipeline used for the bridge's protocol-critical transactions (kickoffs, payouts, challenges, round transactions). This matches the "unauthenticated state-changing or broadcasting call" High-impact category, and repeated abuse drains real operator/aggregator BTC funds used for fee-bumping infrastructure.

### Likelihood Explanation
Likelihood depends on the deployed aggregator actually running with `client_verification = false` (or otherwise permitting unauthenticated network access to its gRPC port), which the project's own documentation states is the normal/expected posture for the aggregator ("does not enforce client certificates"). Given this is described as the standard operating configuration rather than a misconfiguration, any deployment following the documented setup and exposing the aggregator's port to untrusted callers is affected. I was not able to fully verify within the available tool budget whether the `#[cfg(feature = "automation")]` gate or network-level firewalling around the aggregator port is assumed in all real deployments, which could reduce practical exposure — this should be verified with a Devin session that can inspect deployment/infra configs (`devops/**`, which are out of scope for this analysis) if a more definitive likelihood assessment is needed.

### Recommendation
Apply the same `is_internal` mTLS leaf-certificate check used for the operator/verifier servers to the aggregator's `Internal*` methods (at minimum `InternalSendTx`), regardless of the broader `client_verification` toggle for public-facing aggregator methods, or otherwise require the caller to authenticate as the aggregator's own trusted internal identity before transactions can be queued into `TxSender`. Additionally, validate that any transaction passed to `internal_send_tx` corresponds to a transaction type/protocol state actually expected by the bridge (e.g., matches a known pending `TransactionType` in the database) rather than accepting arbitrary attacker-supplied raw transactions.

### Proof of Concept
1. Deploy the aggregator per the documented configuration (`client_verification` disabled for the aggregator, as stated in `docs/usage.md`).
2. As an unauthenticated network client with access to the aggregator's gRPC port, call `ClementineAggregator/InternalSendTx` with any syntactically valid Bitcoin transaction containing a P2A anchor output and an arbitrary `fee_type` (e.g., CPFP), as shown in the test harness pattern in `core/src/rpc/aggregator.rs:1270-1312`.
3. Observe that the transaction is accepted into the `TxSender` queue (`crates/clementine-tx-sender/src/client.rs:59-71`) without any check tying it to a legitimate bridge protocol transaction.
4. Observe the `TxSender` background loop create and broadcast a fee-payer transaction funded from the entity's Bitcoin wallet (`crates/clementine-tx-sender/src/cpfp.rs:195-254`) and repeatedly bump its fee (`crates/clementine-tx-sender/src/cpfp.rs:443-538`), spending real bridge funds on an attacker-chosen transaction.

### Citations

**File:** core/src/rpc/aggregator.rs (L1279-1306)
```rust
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

**File:** crates/clementine-tx-sender/src/cpfp.rs (L195-254)
```rust
        let fee_payer_tx = Transaction {
            version: Version::TWO,
            lock_time: LockTime::ZERO,
            input: vec![],
            output: vec![TxOut {
                value: new_fee_payer_amount,
                script_pubkey: self.signer.address().script_pubkey(),
            }],
        };

        let fee_payer_bytes = crate::serialize_tx_for_fund_raw(&fee_payer_tx);

        let funded_fee_payer_tx = self
            .rpc
            .fund_raw_transaction(
                &fee_payer_bytes,
                Some(&FundRawTransactionOptions {
                    add_inputs: Some(true),
                    // for cpfp txs, the speed of tx inclusion is not that important, so we can not use unsafe utxos and wait for them to become safe. Also all cpfp fee payer tx's are safe (all wallet owned inputs), so wallet can already chain them
                    include_unsafe: Some(self.include_unsafe),
                    change_address: None,
                    change_position: None,
                    change_type: None,
                    include_watching: None,
                    lock_unspents: None,
                    fee_rate: Some(Amount::from_sat(fee_rate.to_sat_per_kvb())),
                    subtract_fee_from_outputs: None,
                    replaceable: Some(true),
                    conf_target: None,
                    estimate_mode: None,
                }),
                None,
            )
            .await
            .wrap_err("Failed to fund cpfp fee payer tx")?
            .hex;

        let signed_fee_payer_tx: Transaction = bitcoin::consensus::deserialize(
            &self
                .rpc
                .sign_raw_transaction_with_wallet(&funded_fee_payer_tx, None, None)
                .await
                .wrap_err("Failed to sign funded tx through bitcoin RPC")?
                .hex,
        )
        .wrap_err("Failed to deserialize signed tx")?;

        let outpoint_vout = signed_fee_payer_tx
            .output
            .iter()
            .position(|o| {
                o.value == new_fee_payer_amount
                    && o.script_pubkey == self.signer.address().script_pubkey()
            })
            .ok_or(eyre!("Failed to find outpoint vout"))?;

        self.rpc
            .send_raw_transaction(&signed_fee_payer_tx)
            .await
            .wrap_err("Failed to send signed fee payer tx")?;
```

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
