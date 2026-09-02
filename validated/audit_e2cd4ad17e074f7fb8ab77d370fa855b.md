### Title
`internal_withdraw` RPC bypasses the operator's ECDSA verification-signature gate that `withdraw` enforces - (File: core/src/rpc/operator.rs)

### Summary
The operator gRPC service exposes two distinct entry points that both end up calling the same fund-committing `Operator::withdraw` (which builds, funds, and broadcasts a Bitcoin payout transaction fronting a user's withdrawal): the public `withdraw` RPC and an `internal_withdraw` RPC. `withdraw` enforces an aggregator-issued ECDSA `verification_signature` when `aggregator_verification_address` is configured, but `internal_withdraw` performs no such check at all before invoking the same state-changing/broadcasting logic.

### Finding Description
`ClementineOperator::withdraw` in `core/src/rpc/operator.rs` gates the call to `self.operator.withdraw(...)` behind a verification step: if `self.operator.config.aggregator_verification_address` is set, the caller must supply a `verification_signature` that recovers to that configured address, otherwise the request is rejected with `BridgeError::ECDSAVerificationSignatureMissing`. [1](#0-0) 

`ClementineOperator::internal_withdraw`, however, parses the same `WithdrawParams` and calls the identical `self.operator.withdraw(...)` function directly, with no verification-signature check and no `cfg!(test)` guard (unlike other "internal_*" methods such as `internal_finalized_payout`, which explicitly rejects non-test callers). [2](#0-1) [3](#0-2) 

The underlying `Operator::withdraw` only validates that (1) the withdrawal UTXO matches the one recorded from Citrea, (2) the payout is profitable for the operator, and (3) the user's Schnorr signature over the payout sighash is valid — it performs no aggregator/verification-signature check itself, since that responsibility is delegated to the RPC layer. [4](#0-3) 

Because `internal_withdraw` skips the RPC-layer verification-signature gate entirely, any caller who can reach this endpoint and who possesses a valid user withdrawal signature (which, per the code's own documentation, is "given to operators off-chain") can trigger the operator to build, fund, and broadcast the fronting payout transaction — bypassing the authorization boundary that `aggregator_verification_address` is designed to enforce (i.e., that only the aggregator authorizes/coordinates which operator processes a given withdrawal).

### Impact Explanation
This is an unauthenticated state-changing and broadcasting call: it allows bypassing an authorization check (`aggregator_verification_address`) that is present and enforced on the sibling `withdraw` endpoint, causing the operator to commit its own Bitcoin funds to a payout transaction outside of the intended, ECDSA-gated flow. This maps to the "High - an unauthenticated state-changing or broadcasting call" category, since it lets a caller reach a fund-committing broadcast method that the protocol clearly intends to restrict via the verification-signature mechanism.

### Likelihood Explanation
Exploitability depends on (a) whether the operator's gRPC endpoint is reachable by a party other than the aggregator, and (b) whether the caller can obtain a valid user withdrawal signature (`in_signature`) for a real pending withdrawal, since the underlying `Operator::withdraw` still validates that signature. I could not confirm within the indexed code whether the "internal_" endpoints are additionally protected by transport-level access control (e.g., mTLS/allow-list) outside of `core/src/rpc/operator.rs`, so likelihood is uncertain without that context — the code-level authorization gap is nonetheless concrete and asymmetric between the two nearly-identical endpoints.

### Recommendation
Apply the same `aggregator_verification_address` / `verification_signature` check present in `withdraw` to `internal_withdraw` (or remove/gate `internal_withdraw` behind `cfg!(test)` the same way `internal_finalized_payout` is gated), so that the fund-committing payout path cannot be triggered without the intended aggregator authorization.

### Proof of Concept
1. Obtain a valid user withdrawal signature `in_signature` for pending withdrawal index `withdrawal_index` (as would be needed for the normal `withdraw` flow) together with the matching `input_outpoint`, `output_script_pubkey`, `output_amount`.
2. Call the operator's `internal_withdraw` gRPC method directly with a `WithdrawParams` built from these values — no `verification_signature` field exists on this RPC's request type, so none can be supplied. [2](#0-1) 
3. The operator proceeds to build, fund, and broadcast the payout transaction via `Operator::withdraw`, exactly as if the request had come through the aggregator with a valid `verification_signature` — the check performed in `withdraw` at lines 209-239 is never executed for this code path. [1](#0-0)

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

**File:** core/src/rpc/operator.rs (L209-239)
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

**File:** core/src/operator.rs (L588-637)
```rust
        // Check Citrea for the withdrawal state.
        let withdrawal_utxo = self
            .db
            .get_withdrawal_utxo_from_citrea_withdrawal(None, withdrawal_index)
            .await?;

        if withdrawal_utxo != input_utxo.outpoint {
            return Err(eyre::eyre!("Input UTXO does not match withdrawal UTXO from Citrea: Input Outpoint: {0}, Withdrawal Outpoint (from Citrea): {1}", input_utxo.outpoint, withdrawal_utxo).into());
        }

        let operator_withdrawal_fee_sats =
            self.config
                .operator_withdrawal_fee_sats
                .ok_or(BridgeError::ConfigError(
                    "Operator withdrawal fee sats is not specified in configuration file"
                        .to_string(),
                ))?;
        if !Self::is_profitable(
            input_utxo.txout.value,
            output_txout.value,
            self.config.protocol_paramset().bridge_amount,
            operator_withdrawal_fee_sats,
        ) {
            return Err(eyre::eyre!("Not enough fee for operator").into());
        }

        let user_xonly_pk = &input_utxo
            .txout
            .script_pubkey
            .try_get_taproot_pk()
            .wrap_err("Input utxo script pubkey is not a valid taproot script")?;

        let payout_txhandler = builder::transaction::create_payout_txhandler(
            input_utxo,
            output_txout,
            self.signer.xonly_public_key,
            in_signature,
            self.config.protocol_paramset().network,
        )?;

        // tracing::info!("Payout txhandler: {:?}", hex::encode(bitcoin::consensus::serialize(&payout_txhandler.get_cached_tx())));

        let sighash = payout_txhandler.calculate_sighash_txin(0, in_signature.sighash_type)?;

        SECP.verify_schnorr(
            &in_signature.signature,
            &Message::from_digest(*sighash.as_byte_array()),
            user_xonly_pk,
        )
        .wrap_err("Failed to verify signature received from user for payout txin. Ensure the signature uses SinglePlusAnyoneCanPay sighash type.")?;
```
