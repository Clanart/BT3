### Title
`InternalWithdraw` gRPC Endpoint Bypasses `aggregator_verification_address` Authorization Check — (`File: core/src/rpc/operator.rs`)

---

### Summary

The operator gRPC server exposes two withdrawal endpoints. `Withdraw` enforces an ECDSA signature check against `aggregator_verification_address` when that config field is set. `InternalWithdraw` unconditionally skips this check and is callable by the operator itself via its own mTLS client certificate. When `aggregator_verification_address` is configured (as in the production `.env.example`), the operator can call `InternalWithdraw` on itself to process any valid withdrawal without the aggregator's approval, defeating the manual-verification safety control.

---

### Finding Description

**Two paths to the same `operator.withdraw()` call:**

`Withdraw` (the public endpoint) enforces the `aggregator_verification_address` guard:

```rust
// core/src/rpc/operator.rs:209-238
if let Some(address_in_config) = self.operator.config.aggregator_verification_address {
    // ... recover address from ECDSA sig, compare to config ...
    if address_from_sig != address_in_config {
        return Err(BridgeError::InvalidECDSAVerificationSignature).map_to_status();
    }
    // if sig missing → ECDSAVerificationSignatureMissing error
}
```

`InternalWithdraw` (the "self-use" endpoint) has no such guard — it calls `operator.withdraw()` directly:

```rust
// core/src/rpc/operator.rs:169-190
async fn internal_withdraw(&self, request: Request<WithdrawParams>)
    -> Result<Response<RawSignedTx>, Status> {
    let (...) = parser::operator::parse_withdrawal_sig_params(request.into_inner())?;
    let payout_tx = self.operator.withdraw(...).await?;
    Ok(Response::new(RawSignedTx::from(&payout_tx)))
}
```

The proto explicitly documents this asymmetry:

> `InternalWithdraw` — "Note: This is intended for operator's own use, so it doesn't include a signature from aggregator."

Unlike `InternalFinalizedPayout`, which is gated by `if !cfg!(test) { return Err(Status::permission_denied(...)) }`, `InternalWithdraw` carries **no test-only guard** and is a live production endpoint.

**The mTLS interceptor allows the operator to call its own internal methods:**

```rust
// core/src/rpc/interceptors.rs:62-69
if is_internal(&req) {
    if leaf_cert == our_cert {
        Ok(req)  // operator's own cert → allowed
    } else {
        Err(Status::unauthenticated("Unauthorized call to internal method (not self)"))
    }
}
```

`is_internal` matches any method whose name starts with `"Internal"`, which includes `InternalWithdraw`. The operator's own client certificate (`client_cert_path`) is always available to the operator process itself.

**The `aggregator_verification_address` field is set in the production reference config:**

```
# .env.example:45
AGGREGATOR_VERIFICATION_ADDRESS=0x242fbec93465ce42b3d7c0e1901824a2697193fd
```

Its documented purpose:

> "Used for both an extra verification of aggregator's identity and to force citrea to check withdrawal params manually during some time after launch."

---

### Impact Explanation

When `aggregator_verification_address` is configured, the operator can call `InternalWithdraw` on itself (using its own mTLS client cert) to process any withdrawal that passes the on-chain/DB checks (`get_withdrawal_utxo_from_citrea_withdrawal`, Schnorr signature verification, profitability check) **without the aggregator's ECDSA approval**. This:

1. Defeats the authorization control that requires the aggregator/Citrea to manually verify and sign off on each withdrawal before the operator fronts it.
2. Allows the operator to initiate the payout → kickoff → reimbursement pipeline for withdrawals the aggregator would have rejected (e.g., during a pause, a detected anomaly, or a Citrea-side discrepancy).
3. The operator fronts the withdrawal from its own funds and then claims reimbursement from the bridge vault via the kickoff/BitVM flow. If the aggregator's rejection would have prevented an invalid reimbursement claim, bypassing it exposes the bridge vault to an unauthorized reimbursement.

**Severity: Medium.** The withdrawal still requires a Citrea-synced UTXO and a valid user Schnorr signature, so the operator cannot fabricate a withdrawal from nothing. The impact is the removal of the aggregator's manual-verification gate, which is the intended safety layer during the early launch period.

---

### Likelihood Explanation

**Medium-High.** The operator process always possesses its own client certificate. No external attacker capability is required — the operator itself is the actor. The only prerequisite is that `aggregator_verification_address` is set in config (which it is in the production reference config) and that a valid Citrea withdrawal UTXO and user signature exist. The endpoint is reachable in production with no code changes.

---

### Recommendation

Apply one of the following:

1. **Add the same `aggregator_verification_address` check to `InternalWithdraw`**, mirroring the logic in `Withdraw`. The check is already factored into `recover_address_from_ecdsa_signature` and can be reused directly.

2. **Gate `InternalWithdraw` with `cfg!(test)`** (as is done for `InternalFinalizedPayout`) if the endpoint is only needed for testing:
   ```rust
   if !cfg!(test) {
       return Err(Status::permission_denied("This method is only available in tests"));
   }
   ```

3. **Remove `InternalWithdraw` from the production proto** if it serves no production purpose.

---

### Proof of Concept

**Setup:** Operator configured with `aggregator_verification_address = 0x242f...` (as in `.env.example`). A valid Citrea withdrawal UTXO exists in the DB and a user has provided a valid Schnorr signature.

**Attack steps:**

1. Operator process constructs a `WithdrawParams` message with `withdrawal_id`, `input_signature`, `input_outpoint`, `output_script_pubkey`, `output_amount`.
2. Operator calls `InternalWithdraw` on its own gRPC server using its own client certificate (`client_cert_path`).
3. The mTLS interceptor (`interceptors.rs:62-64`) allows the call because `leaf_cert == our_cert`.
4. `internal_withdraw` (`operator.rs:169-190`) calls `operator.withdraw()` directly — **no `aggregator_verification_address` check is performed**.
5. `operator.withdraw()` (`operator.rs:560-675`) validates the Citrea UTXO, verifies the Schnorr signature, checks profitability, and enqueues the payout transaction via TxSender.
6. The payout transaction is broadcast. The operator then sends a kickoff transaction and claims reimbursement from the bridge vault — all without the aggregator's ECDSA approval.

**Contrast with the guarded path:** Calling `Withdraw` with the same parameters but without a valid `aggregator_verification_address` ECDSA signature returns `ECDSAVerificationSignatureMissing` at `operator.rs:237`, blocking the withdrawal. `InternalWithdraw` has no equivalent gate. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** core/src/rpc/operator.rs (L168-190)
```rust
    #[tracing::instrument(skip(self), err(level = tracing::Level::ERROR))]
    async fn internal_withdraw(
        &self,
        request: Request<WithdrawParams>,
    ) -> Result<Response<RawSignedTx>, Status> {
        let (withdrawal_id, input_signature, input_outpoint, output_script_pubkey, output_amount) =
            parser::operator::parse_withdrawal_sig_params(request.into_inner())?;

        tracing::warn!("Called internal_withdraw with withdrawal id: {:?}, input signature: {:?}, input outpoint: {:?}, output script pubkey: {:?}, output amount: {:?}", withdrawal_id, input_signature, input_outpoint, output_script_pubkey, output_amount);

        let payout_tx = self
            .operator
            .withdraw(
                withdrawal_id,
                input_signature,
                input_outpoint,
                output_script_pubkey,
                output_amount,
            )
            .await?;

        Ok(Response::new(RawSignedTx::from(&payout_tx)))
    }
```

**File:** core/src/rpc/operator.rs (L209-238)
```rust
        // if verification address is set in config, check if verification signature is valid
        if let Some(address_in_config) = self.operator.config.aggregator_verification_address {
            let verification_signature = params
                .verification_signature
                .map(|sig| {
                    PrimitiveSignature::from_str(&sig).map_err(|e| {
                        Status::invalid_argument(format!("Invalid verification signature: {e}"))
                    })
                })
                .transpose()?;
            // check if verification signature is provided by aggregator
            if let Some(verification_signature) = verification_signature {
                let address_from_sig =
                    recover_address_from_ecdsa_signature::<OperatorWithdrawalMessage>(
                        withdrawal_id,
                        input_signature,
                        input_outpoint,
                        output_script_pubkey.clone(),
                        output_amount,
                        verification_signature,
                    )?;

                // check if verification signature is signed by the address in config
                if address_from_sig != address_in_config {
                    return Err(BridgeError::InvalidECDSAVerificationSignature).map_to_status();
                }
            } else {
                // if verification signature is not provided, but verification address is set in config, return error
                return Err(BridgeError::ECDSAVerificationSignatureMissing).map_to_status();
            }
```

**File:** core/src/rpc/operator.rs (L373-382)
```rust
    #[tracing::instrument(skip(self), err(level = tracing::Level::ERROR), ret(level = tracing::Level::TRACE))]
    async fn internal_finalized_payout(
        &self,
        request: Request<FinalizedPayoutParams>,
    ) -> Result<Response<clementine::Txid>, Status> {
        if !cfg!(test) {
            return Err(Status::permission_denied(
                "This method is only available in tests",
            ));
        }
```

**File:** core/src/rpc/interceptors.rs (L12-76)
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

#[allow(clippy::result_large_err)]
fn only_aggregator_and_self(
    req: Request<()>,
    our_cert: &CertificateDer<'static>,
    aggregator_cert: &CertificateDer<'static>,
) -> Result<Request<()>, Status> {
    let Some(peer_certs) = req.peer_certs() else {
        if cfg!(test) {
            // Test mode, we don't need to verify peer certificates
            return Ok(req);
        } else {
            // If we're not in test mode, we need to check peer certificates
            return Err(Status::unauthenticated(
                "Failed to verify peer certificate, is TLS enabled?",
            ));
        }
    };

    // IMPORTANT: Only check the leaf (end-entity) certificate, which is always the first
    // certificate in the chain. The leaf is the only certificate whose private key the peer
    // proved possession of during the TLS handshake. Checking anywhere else in the chain
    // would allow identity spoofing: an attacker could include a pinned cert as an
    // intermediate in their chain without possessing its private key.
    let Some(leaf_cert) = peer_certs.first() else {
        return Err(Status::unauthenticated("Peer certificate chain is empty"));
    };

    if is_internal(&req) {
        if leaf_cert == our_cert {
            Ok(req)
        } else {
            Err(Status::unauthenticated(
                "Unauthorized call to internal method (not self)",
            ))
        }
    } else if leaf_cert == aggregator_cert || leaf_cert == our_cert {
        Ok(req)
    } else {
        Err(Status::unauthenticated(
            "Unauthorized call to method (not aggregator or self)",
        ))
    }
```

**File:** core/src/config/mod.rs (L148-152)
```rust
    /// The ECDSA address of the citrea/aggregator that will sign the withdrawal params
    /// after manual verification of the optimistic payout and operator's withdrawal.
    /// Used for both an extra verification of aggregator's identity and to force citrea
    /// to check withdrawal params manually during some time after launch.
    pub aggregator_verification_address: Option<alloy::primitives::Address>,
```

**File:** .env.example (L45-45)
```text
AGGREGATOR_VERIFICATION_ADDRESS=0x242fbec93465ce42b3d7c0e1901824a2697193fd
```

**File:** core/src/rpc/clementine.proto (L390-405)
```text
  // Prepares a withdrawal if it's profitable and the withdrawal is correct and
  // registered in Citrea bridge contract. If withdrawal is accepted, the payout
  // tx will be added to the TxSender and success is returned, otherwise an
  // error is returned. If automation is disabled, the withdrawal will not be
  // accepted and an error will be returned. Note: This is intended for
  // operator's own use, so it doesn't include a signature from aggregator.
  rpc InternalWithdraw(WithdrawParams) returns (RawSignedTx) {}

  // First, if verification address in operator's config is set, the signature
  // in rpc is checked to see if it was signed by the verification address. Then
  // prepares a withdrawal if it's profitable and the withdrawal is correct and
  // registered in Citrea bridge contract. If withdrawal is accepted, the payout
  // tx will be added to the TxSender and success is returned, otherwise an
  // error is returned. If automation is disabled, the withdrawal will not be
  // accepted and an error will be returned.
  rpc Withdraw(WithdrawParamsWithSig) returns (RawSignedTx) {}
```
