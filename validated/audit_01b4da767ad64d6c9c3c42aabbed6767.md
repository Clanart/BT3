### Title
`internal_withdraw` accepts operator-triggered payouts without the aggregator/Citrea verification signature - ([File: core/src/rpc/operator.rs])

### Summary
The bug class in LID-5 is that a state-changing call binds only *some* of the fields a downstream actor cares about into a signature, letting an unprivileged party supply the unsigned remainder and change the effective operation. In Clementine, the `withdraw` RPC binds `withdrawal_id`, `input_signature`, `input_outpoint`, `output_script_pubkey`, and `output_amount` into an EIP-712 `OperatorWithdrawalMessage`/`OptimisticPayoutMessage` signature that must be produced by the configured `aggregator_verification_address`, precisely so that only Citrea/the aggregator can trigger a payout for a given withdrawal id. [1](#0-0) [2](#0-1) 

The sibling RPC `internal_withdraw` takes the exact same `WithdrawParams` (`withdrawal_id`, `input_signature`, `input_outpoint`, `output_script_pubkey`, `output_amount`) and calls the identical `operator.withdraw(...)` state-changing path, but performs **no** verification-signature check at all before doing so. [3](#0-2) 

### Finding Description
`withdraw` explicitly guards the state-changing payout path: if `aggregator_verification_address` is configured, it requires and validates a `verification_signature` recovered via `recover_address_from_ecdsa_signature::<OperatorWithdrawalMessage>` against that configured address, rejecting the call with `InvalidECDSAVerificationSignature`/`ECDSAVerificationSignatureMissing` otherwise. [4](#0-3) 

`internal_withdraw`, however, parses the identical `WithdrawParams` and immediately forwards them to `self.operator.withdraw(...)` — the same underlying function that constructs and signs the payout transaction — with no equivalent check. [3](#0-2) 

This breaks the intended binding: "party that is meant to authorize a payout (aggregator/Citrea, verified via ECDSA signature over the withdrawal params) == party that is able to trigger the operator's payout state transition." With `internal_withdraw`, any caller able to reach the operator's gRPC surface can trigger the operator to sign and produce a payout transaction for an arbitrary, valid withdrawal_id/input_signature/outpoint/output tuple without presenting the aggregator's authorization signature — i.e., exactly the missing-binding class the report describes (a downstream state-changing action reachable without the signature that is supposed to gate it).

Note: I was unable to fully verify whether `internal_withdraw` is additionally gated by a distinct transport-level authorization mechanism (e.g., mTLS client-certificate role checks in `core/src/rpc/interceptors.rs` / `core/src/servers.rs`), because I ran out of tool iterations before reading those files. If such a role-based interceptor restricts `internal_withdraw` to a specific authenticated role (e.g., only the local operator itself), this finding would fall under the excluded "key/certificate" authorization category. This is a material open question that determines validity.

### Impact Explanation
If `internal_withdraw` is reachable by an unprivileged caller with only network access to the operator (i.e., not gated behind a certificate/role restricted to a legitimately privileged party), this is an unauthenticated state-changing call: it lets a caller force the operator into producing/broadcasting a payout transaction for a withdrawal without the aggregator's authorization signature that `withdraw` otherwise mandates when `aggregator_verification_address` is configured. This maps to the "High - an unauthenticated state-changing or broadcasting call" category, since it lets a party other than the one the check is meant for (the aggregator/Citrea) drive the operator's payout/reimbursement flow.

### Likelihood Explanation
Given the naming and comment context ("This will be signed manually by citrea after manual verification of the optimistic payout"), `internal_withdraw` appears intended as an internal/manual-verification bypass path used by the operator or Citrea operator itself, which suggests it might be intentionally unauthenticated at the application layer and relies on transport-level restriction. Because I could not confirm the transport-level ACL for this method within the remaining iterations, likelihood is uncertain pending that verification.

### Recommendation
Confirm whether `internal_withdraw` is restricted at the RPC-interceptor/mTLS layer to a role equivalent to "aggregator/Citrea operator." If it is not restricted, either remove the endpoint or require the same `verification_signature` check that `withdraw` performs before calling `self.operator.withdraw(...)`.

### Proof of Concept
Not applicable without confirming the RPC's authorization gating; contingent on interceptor review, a PoC would be: call `internal_withdraw` (gRPC) directly with valid `WithdrawParams` for an existing withdrawal but omit any `verification_signature`, and observe that `operator.withdraw` proceeds to build/sign the payout transaction, whereas the equivalent call to `withdraw` without a `verification_signature` is rejected with `ECDSAVerificationSignatureMissing`. [3](#0-2) [5](#0-4)

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

**File:** core/src/rpc/operator.rs (L192-239)
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
```

**File:** core/src/rpc/ecdsa_verification_sig.rs (L20-45)
```rust
alloy_sol_types::sol! {
    #[derive(Debug)]
    struct OptimisticPayoutMessage {
        uint32 withdrawal_id;
        bytes input_signature;
        bytes32 input_outpoint_txid;
        uint32 input_outpoint_vout;
        bytes output_script_pubkey;
        uint64 output_amount;
    }

    #[derive(Debug)]
    struct OperatorWithdrawalMessage  {
        uint32 withdrawal_id;
        bytes input_signature;
        bytes32 input_outpoint_txid;
        uint32 input_outpoint_vout;
        bytes output_script_pubkey;
        uint64 output_amount;
    }
}

pub static CLEMENTINE_EIP712_DOMAIN: Eip712Domain = alloy_sol_types::eip712_domain! {
    name: "ClementineVerification",
    version: "1",
};
```
