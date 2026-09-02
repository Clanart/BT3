Found it: `internal_withdraw` in `core/src/rpc/operator.rs` is an unauthenticated gRPC method that reaches `Operator::withdraw` without going through any of the aggregator-verification-signature checks that gate the sibling `withdraw` method.

### Title
Unauthenticated `InternalWithdraw` RPC bypasses aggregator verification-signature binding required by `Withdraw` - (File: core/src/rpc/operator.rs)

### Summary
`ClementineOperator::withdraw` (the intended, externally reachable withdrawal path) requires a valid ECDSA `verification_signature` from the configured `aggregator_verification_address` before fronting a payout, whenever `operator.config.aggregator_verification_address` is set. A second exported method, `internal_withdraw`, calls the exact same `Operator::withdraw` business logic but has none of these checks, allowing any caller with network access to the operator's gRPC endpoint to trigger a real, funds-moving payout transaction while completely skipping the authorization binding the protocol relies on.

### Finding Description
`Operator::withdraw` in `core/src/operator.rs` (around lines 560-627) itself only checks: (1) the input UTXO matches the withdrawal UTXO known from Citrea, and (2) that the payout is "profitable" for the operator. It performs no check on *who is authorized to request it*. That authorization is added only in the RPC layer, inside `withdraw` (`core/src/rpc/operator.rs:192-258`): [1](#0-0) 

This mirrors exactly the pattern in the external report: the "withdrawal limit"-equivalent check (here, the aggregator-verification-signature requirement) was implemented in one call path (`withdraw`) but not propagated into the sibling function that performs the same state-changing action (`internal_withdraw`), which calls `self.operator.withdraw(...)` directly with zero authorization gating: [2](#0-1) 

Unlike `internal_finalized_payout`, which is explicitly gated with `if !cfg!(test) { return Err(Status::permission_denied(...)) }` [3](#0-2) , `internal_withdraw` has no such guard and is compiled into the production gRPC service (`ClementineOperator::internal_withdraw`) unconditionally.

### Impact Explanation
This is an unauthenticated state-changing/broadcasting call: any network caller who can reach the operator's gRPC port can invoke `internal_withdraw` with an arbitrary `withdrawal_id`, `input_signature`, `input_outpoint`, `output_script_pubkey`, and `output_amount`, causing the operator to sign and fund a `payout_tx` from its own hot wallet — bypassing the aggregator-verification-signature control that `withdraw` is supposed to enforce as the sole authorization gate for triggering an operator payout. The only remaining checks are the Citrea withdrawal-UTXO match and operator profitability, both of which an attacker who has captured (or replayed) a user's off-chain withdrawal signature (which per the code's own docstring is "given to operators off-chain", i.e., not secret to a network attacker with visibility into that off-chain channel) can satisfy without ever presenting the aggregator's verification signature.

### Likelihood Explanation
High: `internal_withdraw` is present in the compiled RPC surface with no build-time/test-time restriction (contrast with `internal_finalized_payout`), no additional caller authentication at the gRPC layer, and requires no privileged role (verifier/operator/aggregator key) to invoke — an unprivileged attacker with network reachability and a valid withdrawal UTXO/signature pair can call it directly.

### Recommendation
Add the same `aggregator_verification_address` / ECDSA verification-signature check that `withdraw` performs into `internal_withdraw` before calling `self.operator.withdraw(...)`, or remove/gate `internal_withdraw` behind the same test-only guard used for `internal_finalized_payout` (`if !cfg!(test) { return Err(Status::permission_denied(...)) }`).

### Proof of Concept
1. Configure an operator with `aggregator_verification_address` set (production mode requiring aggregator authorization for withdrawals).
2. As an unprivileged network caller, obtain/replay a valid `input_signature`, `input_outpoint` (matching a pending Citrea withdrawal UTXO), `output_script_pubkey`, and `output_amount` for a withdrawal (this data is exchanged off-chain between user and operators per the code's own comments, so it is not protected by the aggregator's key).
3. Call `ClementineOperator/InternalWithdraw` directly (bypassing `ClementineOperator/Withdraw`) with these parameters and no `verification_signature`.
4. Observe `internal_withdraw` → `Operator::withdraw` executes fully (only checking UTXO match and profitability), builds `create_payout_txhandler`, signs it, and returns the raw payout transaction — with no aggregator authorization ever validated, unlike the `Withdraw` code path in `core/src/rpc/operator.rs:209-239`.

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
