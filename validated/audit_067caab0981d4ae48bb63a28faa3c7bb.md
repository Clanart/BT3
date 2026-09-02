### Title
`internal_withdraw` RPC bypasses the aggregator-verification-signature gate that its sibling `withdraw` enforces - (File: `core/src/rpc/operator.rs`)

### Summary
`ClementineOperator::withdraw` requires a valid ECDSA verification signature from `aggregator_verification_address` before the operator will build and sign a payout (fronting) transaction for a withdrawal. `internal_withdraw`, exposed on the same gRPC service, performs the exact same state-changing action (`self.operator.withdraw(...)`) but has no such check at all, and — unlike the other `internal_*` methods in the same file — is not gated by a `cfg!(test)`/`permission_denied` guard.

### Finding Description
`withdraw` (core/src/rpc/operator.rs, lines 192-258) first checks `self.operator.config.aggregator_verification_address`; if set, it requires a `verification_signature` recoverable to that configured address, otherwise it returns `BridgeError::ECDSAVerificationSignatureMissing`: [1](#0-0) 

`internal_withdraw` (lines 168-190) parses the identical `WithdrawParams` and calls the identical `self.operator.withdraw(...)` state-changing/signing operation, but performs no verification-signature check whatsoever: [2](#0-1) 

Contrast this with `internal_finalized_payout` in the same file, which is explicitly restricted: [3](#0-2) 

There is no equivalent `if !cfg!(test) { return Err(...) }` guard on `internal_withdraw`, so it is compiled and reachable in production builds, and any caller who can reach the operator's gRPC endpoint can invoke it directly, completely sidestepping the authorization mechanism (`aggregator_verification_address`) that the deployment relies on to control who may trigger the operator into fronting a withdrawal.

This is the same root-cause pattern as the referenced report: a code path exists that performs a privileged/sensitive action while skipping a validation/authorization step that a parallel, "safe" code path enforces — "a caller reaching a signing or state-changing method versus the party it is meant for."

### Impact Explanation
`operator.withdraw` results in the operator creating and signing (and returning, to be broadcast) the `payout_tx`, i.e. the transaction that fronts the user's withdrawal from the operator's own funds ahead of on-chain reimbursement. The `aggregator_verification_address` check exists specifically to ensure only the aggregator (or whichever party holds that key, per protocol design) can request the operator front a specific withdrawal — this coordinates which operator services which withdrawal and prevents unauthorized/duplicate fronting requests. By calling `internal_withdraw` instead of `withdraw`, an unprivileged caller in possession only of the withdrawal's on-chain `input_signature`/`input_outpoint`/output parameters (which are validated inside `operator.withdraw` against the actual withdrawal UTXO) can force the fronting action without ever presenting the required verification signature. This is an unauthenticated state-changing/broadcasting call: it lets a caller who does not hold the aggregator's authorization key trigger the operator's reimbursement-fronting flow, which the config-level authorization gate was explicitly designed to prevent — matching the High-severity "unauthenticated state-changing or broadcasting call" category.

### Likelihood Explanation
The method is exposed on the standard `ClementineOperator` gRPC surface (same service, same generated code, same authentication context as `withdraw`); nothing in this file scopes it to loopback-only or an internal-only channel, and unlike `internal_finalized_payout` it carries no `cfg!(test)` restriction. Exploitability depends on whether the deployment relies on `aggregator_verification_address` as its authorization boundary for this action (as the sibling `withdraw` method's logic implies) versus some transport-level access control (e.g., mTLS) outside this repository that I could not verify from the indexed code.

### Recommendation
Apply the identical `aggregator_verification_address` / verification-signature check inside `internal_withdraw` before invoking `self.operator.withdraw(...)`, or remove `internal_withdraw` from the production build path (e.g., gate it behind `cfg!(test)` like `internal_finalized_payout`) if it is intended purely as a test/debug entry point.

### Proof of Concept
1. Deployment configures `aggregator_verification_address` so that `withdraw` requires a verification signature (core/src/rpc/operator.rs:210-238).
2. An unprivileged caller obtains a legitimate withdrawal's `withdrawal_id`, `input_signature`, `input_outpoint`, `output_script_pubkey`, `output_amount` (these are public/committed on the Citrea side per `core/src/test/common/citrea/requests.rs` deposit/withdrawal flow) but does **not** possess the aggregator's ECDSA key needed to produce `verification_signature`.
3. Instead of calling `withdraw` (which would reject the request per lines 231-238), the caller invokes `internal_withdraw` with the same `WithdrawParams`.
4. `internal_withdraw` calls `self.operator.withdraw(...)` directly (lines 178-187) with no verification-signature check, and the operator signs/returns the payout transaction — completing the fronting action the aggregator gate was meant to block.

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
