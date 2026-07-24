### Title
Optional `aggregator_verification_address` Silently Disables Withdrawal Authentication Gate — (`core/src/rpc/operator.rs`, `core/src/verifier.rs`)

### Summary

`aggregator_verification_address` is an `Option<Address>` in `BridgeConfig`. When it is `None` (unset), the ECDSA identity check that is supposed to gate both the operator `Withdraw` RPC and the verifier `OptimisticPayoutSign` RPC is silently skipped in full. Any caller who can reach those endpoints — including any mTLS-authenticated peer when `client_verification` is also unset — can trigger payout signing without the required Citrea-side manual approval, bypassing the authorization decision the protocol explicitly relies on during launch.

### Finding Description

`BridgeConfig.aggregator_verification_address` is declared `Option<alloy::primitives::Address>` and sourced from an optional environment variable: [1](#0-0) 

In the operator's `Withdraw` RPC handler the guard is:

```rust
if let Some(address_in_config) = self.operator.config.aggregator_verification_address {
    // … verify ECDSA sig …
}
// falls through silently when None
``` [2](#0-1) 

The identical pattern appears in the verifier's `sign_optimistic_payout`:

```rust
if let Some(address_in_config) = self.config.aggregator_verification_address {
    // … verify ECDSA sig …
}
// falls through silently when None
``` [3](#0-2) 

The field's own doc-comment acknowledges the intent: *"Used for both an extra verification of aggregator's identity and to force citrea to check withdrawal params manually during some time after launch."* [1](#0-0) 

When the field is absent the entire authentication decision is dropped. The `Withdraw` RPC then calls `operator.withdraw()` unconditionally, and `OptimisticPayoutSign` calls `sign_optimistic_payout()` unconditionally, with no record that the Citrea-side manual approval ever occurred.

The `InternalWithdraw` RPC carries no verification at all by design (it is operator-internal), so the `Withdraw` RPC with its optional ECDSA gate is the only external-facing authentication layer for operator-initiated payouts: [4](#0-3) 

### Impact Explanation

When `aggregator_verification_address` is `None`:

1. **Operator `Withdraw` path** — any caller who can reach the operator gRPC (trivially possible when `client_verification` is also unset, or via any mTLS-authenticated peer) can submit `WithdrawParamsWithSig` with an absent or arbitrary `verification_signature` and the operator will execute the payout transaction without Citrea's manual sign-off. The operator fronts real BTC from its collateral/payout budget.

2. **Verifier `OptimisticPayoutSign` path** — the aggregator's public-facing `optimistic_payout` RPC passes the caller-supplied `verification_signature` straight through to each verifier. With the gate absent, verifiers co-sign the optimistic payout MuSig2 partial signature for any withdrawal that exists in the DB, regardless of whether Citrea approved it.

The corrupted authorization decision is: *"this withdrawal has been manually verified by Citrea"* — that invariant is silently dropped. The downstream checks (withdrawal UTXO must match Citrea DB state, output amount ≤ `bridge_amount − NON_EPHEMERAL_ANCHOR_AMOUNT`) bound the worst-case BTC moved per call, but they do not restore the missing approval gate. [5](#0-4) 

### Likelihood Explanation

`AGGREGATOR_VERIFICATION_ADDRESS` is not a required environment variable. The env parser uses `.ok()` to make it optional: [6](#0-5) 

Any operator or verifier that omits this variable — which is the default state — silently runs without the gate. Because the field is `Option` and the code compiles and runs correctly without it, there is no startup-time warning or error. The proto comment itself frames the check as a transitional measure ("during some time after launch"), increasing the chance that operators treat it as optional indefinitely. [7](#0-6) 

### Recommendation

- **Short term:** Emit a startup-time `tracing::warn!` (or hard error) when `aggregator_verification_address` is `None` on any node that exposes `Withdraw` or `OptimisticPayoutSign`. Make the absence explicit rather than silent.
- **Long term:** Promote `aggregator_verification_address` to a required field, or replace the `Option`-based gate with a mandatory check that defaults to requiring the aggregator's mTLS certificate identity, so the authentication decision cannot be accidentally dropped by omitting a config key.

### Proof of Concept

1. Deploy operator and verifier nodes without setting `AGGREGATOR_VERIFICATION_ADDRESS`.
2. Call the operator's `Withdraw` gRPC with a valid `WithdrawParams` (matching a Citrea-registered withdrawal UTXO) and `verification_signature: None`.
3. Observe that `operator.rs:210` evaluates `self.operator.config.aggregator_verification_address` as `None`, skips the entire `if` block, and proceeds to `operator.withdraw(...)` at line 241 — executing the payout without any Citrea manual approval.
4. Repeat via the aggregator's `optimistic_payout` RPC: supply `verification_signature: None`; verifiers at `verifier.rs:1602` skip the gate and return a valid partial signature, allowing the aggregator to assemble and broadcast the optimistic payout transaction. [8](#0-7) [3](#0-2)

### Citations

**File:** core/src/config/mod.rs (L148-152)
```rust
    /// The ECDSA address of the citrea/aggregator that will sign the withdrawal params
    /// after manual verification of the optimistic payout and operator's withdrawal.
    /// Used for both an extra verification of aggregator's identity and to force citrea
    /// to check withdrawal params manually during some time after launch.
    pub aggregator_verification_address: Option<alloy::primitives::Address>,
```

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

**File:** core/src/rpc/operator.rs (L209-250)
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
```

**File:** core/src/verifier.rs (L1601-1623)
```rust
        // if verification address is set in config, check if verification signature is valid
        if let Some(address_in_config) = self.config.aggregator_verification_address {
            // check if verification signature is provided by aggregator
            if let Some(verification_signature) = verification_signature {
                let address_from_sig =
                    recover_address_from_ecdsa_signature::<OptimisticPayoutMessage>(
                        deposit_id,
                        input_signature,
                        input_outpoint,
                        output_script_pubkey.clone(),
                        output_amount,
                        verification_signature,
                    )?;

                // check if verification signature is signed by the address in config
                if address_from_sig != address_in_config {
                    return Err(BridgeError::InvalidECDSAVerificationSignature);
                }
            } else {
                // if verification signature is not provided, but verification address is set in config, return error
                return Err(BridgeError::ECDSAVerificationSignatureMissing);
            }
        }
```

**File:** core/src/verifier.rs (L1634-1643)
```rust
        // amount in move_tx is exactly the bridge amount
        if output_amount
            > self.config.protocol_paramset().bridge_amount - NON_EPHEMERAL_ANCHOR_AMOUNT
        {
            return Err(eyre::eyre!(
                "Output amount is greater than the bridge amount: {} > {}",
                output_amount,
                self.config.protocol_paramset().bridge_amount - NON_EPHEMERAL_ANCHOR_AMOUNT
            )
            .into());
```

**File:** core/src/config/env.rs (L165-171)
```rust
        let aggregator_verification_address = std::env::var("AGGREGATOR_VERIFICATION_ADDRESS")
            .ok()
            .map(|addr| {
                addr.parse::<alloy::primitives::Address>()
                    .wrap_err("Failed to parse AGGREGATOR_VERIFICATION_ADDRESS")
            })
            .transpose()?;
```

**File:** core/src/rpc/clementine.proto (L398-405)
```text
  // First, if verification address in operator's config is set, the signature
  // in rpc is checked to see if it was signed by the verification address. Then
  // prepares a withdrawal if it's profitable and the withdrawal is correct and
  // registered in Citrea bridge contract. If withdrawal is accepted, the payout
  // tx will be added to the TxSender and success is returned, otherwise an
  // error is returned. If automation is disabled, the withdrawal will not be
  // accepted and an error will be returned.
  rpc Withdraw(WithdrawParamsWithSig) returns (RawSignedTx) {}
```
