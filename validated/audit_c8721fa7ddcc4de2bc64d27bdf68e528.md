### Title
Aggregator Verification Signature Check Absent in `internal_withdraw` Allows Unauthorized Payout Submission — (`File: core/src/rpc/operator.rs`)

### Summary

The `ClementineOperator` gRPC service exposes two withdrawal endpoints: `withdraw` and `internal_withdraw`. The `withdraw` handler enforces an ECDSA aggregator-verification-signature check when `aggregator_verification_address` is configured. The `internal_withdraw` handler calls the identical underlying `operator.withdraw()` function but **omits this check entirely**, allowing any caller with network access to the operator's gRPC port to submit payout transactions without aggregator approval.

### Finding Description

`withdraw` (the externally-facing endpoint) conditionally enforces an aggregator-signed ECDSA proof that the aggregator approved the specific withdrawal parameters: [1](#0-0) 

If `aggregator_verification_address` is set in config and the caller cannot produce a valid signature from that address, the call is rejected with `ECDSAVerificationSignatureMissing` or `InvalidECDSAVerificationSignature`.

`internal_withdraw`, registered on the **same** `ClementineOperator` gRPC service, calls the same `self.operator.withdraw(...)` but performs **no such check**: [2](#0-1) 

The proto comment acknowledges the asymmetry — "intended for operator's own use, so it doesn't include a signature from aggregator" — but there is no code-level enforcement of that intent (no interceptor, no role check, no mTLS-only guard visible in the handler). Both endpoints are registered under the same `ClementineOperator` service and are reachable on the same gRPC port. [3](#0-2) 

The underlying `operator.withdraw()` still validates that the withdrawal UTXO matches Citrea's record and that the user's taproot signature is valid, so the attacker cannot fabricate a withdrawal from thin air. However, the aggregator-verification gate — the only control that ensures the aggregator has reviewed and approved the specific payout before the operator commits funds — is completely absent on the `internal_withdraw` path.

### Impact Explanation

When `aggregator_verification_address` is configured (the security-hardened deployment), any party that can reach the operator's gRPC port can call `InternalWithdraw` with a valid Citrea-registered withdrawal and a correct user signature, bypassing the aggregator's approval step. The operator will build and broadcast a payout transaction, spending its own BTC liquidity, without the aggregator having reviewed the request. This:

- Breaks the trust-boundary invariant that the aggregator must authorize every payout before the operator acts.
- Allows an attacker to drain operator liquidity by replaying or racing legitimate Citrea withdrawals directly to `internal_withdraw`, circumventing any aggregator-side rate-limiting, fraud detection, or sequencing controls.
- Constitutes an authorization bypass in the gRPC actor-role boundary: a caller that is not the operator itself obtains the ability to trigger privileged bridge fund movements.

### Likelihood Explanation

Exploitability requires network access to the operator's gRPC port. If the operator's port is firewalled to localhost or protected by mTLS with client-certificate enforcement, the attack surface is limited to the operator process itself. However, the handler contains no code-level guard, so any misconfiguration or deployment that exposes the port (common in cloud or containerized setups) immediately enables the bypass. The `aggregator_verification_address` field is optional, so operators who do not configure it are unaffected; those who do configure it (precisely the ones trying to enforce aggregator approval) are the ones exposed.

### Recommendation

Add the same aggregator-verification-signature check to `internal_withdraw`, or remove the endpoint from the public gRPC surface and restrict it to an internal-only channel (e.g., a separate Unix-socket listener or a dedicated mTLS-enforced port with a client-certificate tied to the operator's own identity). The minimal code fix mirrors the pattern already present in `withdraw`:

```rust
async fn internal_withdraw(
    &self,
    request: Request<WithdrawParams>,
) -> Result<Response<RawSignedTx>, Status> {
    // Add the same aggregator verification check as in `withdraw`
    if let Some(address_in_config) = self.operator.config.aggregator_verification_address {
        // ... enforce verification_signature ...
        return Err(BridgeError::ECDSAVerificationSignatureMissing).map_to_status();
    }
    // ... rest of handler
}
```

Alternatively, if `internal_withdraw` is genuinely only for the operator's own automation, it should be removed from the public proto surface and invoked only through an in-process call, eliminating the gRPC exposure entirely.

### Proof of Concept

1. Deploy an operator with `aggregator_verification_address` set to a known address `A`.
2. Observe that calling `Withdraw` without a valid signature from `A` returns `ECDSAVerificationSignatureMissing`.
3. Call `InternalWithdraw` on the same gRPC port with a valid Citrea-registered withdrawal UTXO and a correct user taproot signature — **no aggregator signature required**.
4. The operator builds and broadcasts the payout transaction, spending its BTC, without any approval from the aggregator.

The relevant divergence in the two handlers: [2](#0-1) [4](#0-3)

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

**File:** core/src/rpc/operator.rs (L192-258)
```rust
    #[tracing::instrument(skip(self), err(level = tracing::Level::ERROR))]
    async fn withdraw(
        &self,
        request: Request<WithdrawParamsWithSig>,
    ) -> Result<Response<RawSignedTx>, Status> {
        tracing::info!("Withdraw rpc called");
        let params = request.into_inner();
        let withdraw_params = params.withdrawal.ok_or(Status::invalid_argument(
            "Withdrawal params not found for withdrawal",
        ))?;
        let (withdrawal_id, input_signature, input_outpoint, output_script_pubkey, output_amount) =
            parser::operator::parse_withdrawal_sig_params(withdraw_params)?;

        tracing::warn!(
            "Parsed withdraw rpc params, withdrawal id: {:?}, input signature: {:?}, input outpoint: {:?}, output script pubkey: {:?}, output amount: {:?}, verification signature: {:?}", withdrawal_id, input_signature, input_outpoint, output_script_pubkey, output_amount, params.verification_signature
        );

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
        }

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

        tracing::info!(
            "Withdraw rpc completed successfully for withdrawal id: {:?}",
            withdrawal_id
        );

        Ok(Response::new(RawSignedTx::from(&payout_tx)))
    }
```

**File:** core/src/rpc/clementine.rs (L1380-1404)
```rust
        /// accepted and an error will be returned. Note: This is intended for
        /// operator's own use, so it doesn't include a signature from aggregator.
        pub async fn internal_withdraw(
            &mut self,
            request: impl tonic::IntoRequest<super::WithdrawParams>,
        ) -> std::result::Result<tonic::Response<super::RawSignedTx>, tonic::Status> {
            self.inner
                .ready()
                .await
                .map_err(|e| {
                    tonic::Status::unknown(
                        format!("Service was not ready: {}", e.into()),
                    )
                })?;
            let codec = tonic::codec::ProstCodec::default();
            let path = http::uri::PathAndQuery::from_static(
                "/clementine.ClementineOperator/InternalWithdraw",
            );
            let mut req = request.into_request();
            req.extensions_mut()
                .insert(
                    GrpcMethod::new("clementine.ClementineOperator", "InternalWithdraw"),
                );
            self.inner.unary(req, path, codec).await
        }
```
