### Title
Operator's `internal_withdraw` RPC bypasses the aggregator ECDSA verification-signature check that `withdraw` enforces - (File: core/src/rpc/operator.rs)

### Summary
`ClementineOperator::withdraw` and `ClementineOperator::internal_withdraw` are two nearly identical gRPC handlers that both end up calling `Operator::withdraw(...)` to build, sign and broadcast a real Bitcoin payout transaction fronting a Citrea withdrawal. `withdraw` additionally recovers and checks an ECDSA `verification_signature` against `self.operator.config.aggregator_verification_address` before invoking `Operator::withdraw`, but `internal_withdraw` skips this check entirely and calls `Operator::withdraw` directly from the raw, unauthenticated request parameters.

### Finding Description
The `withdraw` handler parses the request and, if `aggregator_verification_address` is configured, requires a valid ECDSA signature over the withdrawal parameters recovered via `recover_address_from_ecdsa_signature::<OperatorWithdrawalMessage>`, rejecting the request with `BridgeError::InvalidECDSAVerificationSignature` / `ECDSAVerificationSignatureMissing` if this check fails or is missing: [1](#0-0) 

`internal_withdraw`, however, only calls `parser::operator::parse_withdrawal_sig_params` and immediately forwards the parsed parameters to `self.operator.withdraw(...)` — there is no ECDSA verification-signature check at all: [2](#0-1) 

`Operator::withdraw` itself performs no aggregator-authorization check either — it only validates that the input outpoint matches the withdrawal UTXO tracked from Citrea, checks profitability against the operator's own configured fee, and then constructs/signs the payout transaction with the operator's key: [3](#0-2) 

This is the same bug class as the referenced report: two structurally similar entry points exist, one performs a critical guard (the aggregator-signed authorization binding), the other calls straight through to the sensitive state-changing logic and omits it — exactly analogous to `BribeFactoryV3.recoverERC20AndUpdateData` calling `emergencyRecoverERC20` instead of the function that updates the critical accounting/authorization state.

### Impact Explanation
The `aggregator_verification_address` mechanism exists specifically to ensure that operator payout requests were vetted/approved (e.g., against replay across the two different fee-paths noted in `generate_withdrawal_transaction_and_signatures`, where the same dust UTXO can be pre-signed by the user for both an operator-fee payout and a lower, contract-fee optimistic payout): [4](#0-3) 

By calling `internal_withdraw` instead of `withdraw`, any caller reaching the operator's gRPC surface can force the operator to sign and broadcast a real payout transaction for a pending withdrawal while completely bypassing the aggregator-authorization gate that the deployment relies on, i.e. reaching a signing/broadcasting method meant to be authorized by the aggregator without presenting that authorization. This maps to the "unauthenticated state-changing or broadcasting call" impact category — a party who is not the intended authorizer (the aggregator) can trigger operator fund-committing behavior.

### Likelihood Explanation
`internal_withdraw` is a first-class gRPC method on `ClementineOperator` (registered in the proto/service definitions and exercised in `core/src/bin/cli.rs`), reachable the same way any other operator RPC is reachable; it requires no special role, key, or node compromise — only the ability to call the operator's gRPC endpoint with parameters for an already-registered Citrea withdrawal, which by design any unprivileged party interacting with the withdrawal flow can obtain.

### Recommendation
Remove the `internal_withdraw` RPC, or make it perform the identical `aggregator_verification_address` / ECDSA `verification_signature` check that `withdraw` performs before calling `Operator::withdraw`. Preferably, move the verification-signature check into `Operator::withdraw` itself so all callers (present and future) are forced through the same authorization gate rather than relying on each RPC wrapper to duplicate it correctly.

### Proof of Concept
1. A withdrawal is registered on Citrea (`get_withdrawal_utxo_from_citrea_withdrawal` returns a valid `withdrawal_utxo`) and the operator has `aggregator_verification_address` configured, per [5](#0-4) .
2. An attacker who is not the aggregator, but who has access to a validly user-signed `WithdrawParams` (e.g., leaked, observed off-chain, or reused from the optimistic-payout flow, per [4](#0-3) ), calls `internal_withdraw` directly with those parameters and no `verification_signature`.
3. `internal_withdraw` forwards straight to `Operator::withdraw` ( [6](#0-5) ), which only checks the withdrawal UTXO and profitability ( [7](#0-6) ) — no aggregator authorization is ever checked — and the operator signs and returns a broadcastable payout transaction, something `withdraw` would have refused without a valid `verification_signature`.

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

**File:** core/src/operator.rs (L588-627)
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

```

**File:** core/src/test/common/setup_utils.rs (L451-478)
```rust
/// Optimistic payout registration must use the contract-accepted fee amount,
/// while operator payouts use the configured operator fee.
pub async fn generate_withdrawal_transaction_and_signatures(
    config: &BridgeConfig,
    rpc: &ExtendedBitcoinRpc,
    withdrawal_address: &bitcoin::Address,
    operator_amount: bitcoin::Amount,
    optimistic_amount: bitcoin::Amount,
) -> (
    UTXO,
    bitcoin::TxOut,
    taproot::Signature,
    bitcoin::TxOut,
    taproot::Signature,
) {
    let dust_utxo = generate_withdrawal_utxo(config, rpc).await;
    let (operator_txout, operator_sig) =
        sign_withdrawal_output(config, &dust_utxo, withdrawal_address, operator_amount);
    let (optimistic_txout, optimistic_sig) =
        sign_withdrawal_output(config, &dust_utxo, withdrawal_address, optimistic_amount);
    (
        dust_utxo,
        operator_txout,
        operator_sig,
        optimistic_txout,
        optimistic_sig,
    )
}
```
