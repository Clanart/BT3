### Title
Operator's `InternalWithdraw` gRPC endpoint bypasses ECDSA verification-signature authentication that gates the authenticated `Withdraw` endpoint - (File: core/src/rpc/operator.rs)

### Summary
`ClementineOperator::withdraw` (the `Withdraw` RPC) requires and validates an `aggregator_verification_address`-bound ECDSA signature over the withdrawal params before calling `self.operator.withdraw(...)`, which builds and broadcasts a payout transaction. `ClementineOperator::internal_withdraw` (the `InternalWithdraw` RPC) parses the same `WithdrawParams` and calls the exact same `self.operator.withdraw(...)` state-changing/broadcasting function, but performs none of the ECDSA verification-signature checks present in `withdraw`.

### Finding Description
The two RPC handlers converge on the identical state-changing sink `Operator::withdraw`, but only one of the two entrypoints enforces the authentication binding that the protocol relies on (verification signature signed by `aggregator_verification_address` proving the request originated from the aggregator/Citrea flow).

`withdraw()` (the gated path): [1](#0-0) 

`internal_withdraw()` (the ungated path) parses the same params and calls the same `operator.withdraw` sink without any verification-signature check: [2](#0-1) 

Both eventually reach the identical underlying logic: [3](#0-2) 

The `Operator::withdraw` function itself only checks that the input UTXO matches the withdrawal UTXO recorded from Citrea and that the withdrawal is "profitable" for the operator fee — it does not itself re-derive or require the aggregator's verification signature; that check exists solely in the `withdraw` RPC handler (`core/src/rpc/operator.rs:210-239`), not in `internal_withdraw`, and not in the `Operator::withdraw` core method.

This mirrors the report's bug class: an interface/handler is missing a parameter/check ("the receiver parameter"/`_minRedeemOrder` case) that is present in a sibling implementation, producing a functional mismatch between two call paths meant to reach the same protected operation. Here the mismatch is not a compile-time interface signature (Rust would catch that) but a security-relevant control-flow divergence between two RPC handlers exposing the same underlying state-changing/broadcasting call — i.e., "a caller reaching a state-changing or broadcasting method versus the party it is meant for."

### Impact Explanation
If `internal_withdraw` is reachable by the same class of caller as `withdraw` (i.e., any client holding valid mTLS credentials to the operator's gRPC service, without needing to also hold the aggregator's ECDSA verification key), it lets that caller trigger operator-funded payouts for arbitrary withdrawal indices without proving the request was routed/approved by the aggregator — the authentication binding "party that requested the payout == the address in `aggregator_verification_address`" is broken for this specific entrypoint. This crosses the "unauthenticated state-changing or broadcasting call" bound in the rules (High impact), since it lets a caller invoke a state-changing/broadcasting operator method that the protocol intends to gate behind the aggregator's ECDSA verification signature.

However, this call still requires the caller to be an authorized mTLS client of the operator's gRPC surface (the CLI tool that exercises `internal_withdraw` explicitly uses client cert/key/CA cert TLS credentials, per `core/src/bin/cli.rs:266-283, 285-296, 375-378`). Whether an entity possessing valid operator-client TLS credentials but not the aggregator ECDSA key is considered an "unprivileged attacker" for this system's threat model is not fully resolved from the code alone; if such credential separation is a documented, intended operational safeguard (i.e., `internal_withdraw` is explicitly meant as an operator-local/admin bypass), this would fall under the excluded "role/certificate held" or "local access" categories rather than a true unauthenticated-caller vulnerability.

### Likelihood Explanation
The two handlers are unambiguously present in the compiled RPC surface (`ClementineOperator` trait impl) and reachable via the same gRPC service/port as `withdraw`; the CLI ships a ready-made `Operator Withdraw` subcommand that calls `internal_withdraw` directly, confirming it is a real, invokable production interface rather than test-only code (`core/src/bin/cli.rs:78-92, 348-379`). No additional secret, nonce, or one-time credential is required beyond whatever TLS client credential set is already required to reach the operator's other gRPC methods.

### Recommendation
Add the same ECDSA verification-signature check (verification against `self.operator.config.aggregator_verification_address`) inside `internal_withdraw`, or better, move the check into `Operator::withdraw` itself so both RPC entrypoints share one authorization gate and cannot diverge again. Alternatively, if `internal_withdraw` is intentionally meant to be an operator-only local bypass, restrict it at the transport layer (e.g. separate unauthenticated-by-signature but operator-local-only socket) and document that this is not reachable by the aggregator's counterpart credential set, and clarify this is out of scope for the aggregator-signature threat model.

### Proof of Concept
1. Obtain an operator-client mTLS credential (as used by the `clementine_core::rpc::operator_client_builder` client) without possessing the aggregator's `aggregator_verification_secret_key`.
2. Construct a `WithdrawParams` with a valid Citrea-recorded `withdrawal_index`, matching `input_outpoint`, and any desired `output_script_pubkey`/`output_amount` satisfying the profitability check in `Operator::withdraw` (`core/src/operator.rs:598-612`).
3. Call `ClementineOperator/InternalWithdraw` (as exercised by `clementine_core::bin::cli::OperatorCommands::Withdraw`, `core/src/bin/cli.rs:348-379`) — no `verification_signature` field exists on `WithdrawParams`/`internal_withdraw`'s request type at all, so the aggregator-signature check that exists in `withdraw()` (`core/src/rpc/operator.rs:210-239`) is structurally impossible to satisfy or bypass-check here.
4. `Operator::withdraw` executes and creates/broadcasts a signed payout transaction (`core/src/operator.rs:620-627`) without any confirmation that the aggregator authorized this specific payout.

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

**File:** core/src/rpc/operator.rs (L192-250)
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
```

**File:** core/src/operator.rs (L560-627)
```rust
    pub async fn withdraw(
        &self,
        withdrawal_index: u32,
        in_signature: taproot::Signature,
        in_outpoint: OutPoint,
        out_script_pubkey: ScriptBuf,
        out_amount: Amount,
    ) -> Result<Transaction, BridgeError> {
        tracing::info!(
            "Withdrawing with index: {}, in_signature: {:?}, in_outpoint: {:?}, out_script_pubkey: {}, out_amount: {}",
            withdrawal_index,
            in_signature,
            in_outpoint,
            out_script_pubkey,
            out_amount
        );

        // Prepare input and output of the payout transaction.
        let input_prevout = self.rpc.get_txout_from_outpoint(&in_outpoint).await?;
        let input_utxo = UTXO {
            outpoint: in_outpoint,
            txout: input_prevout,
        };
        let output_txout = TxOut {
            value: out_amount,
            script_pubkey: out_script_pubkey,
        };

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
