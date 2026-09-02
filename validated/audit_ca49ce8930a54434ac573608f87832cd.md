### Title
`ClementineOperator::internal_withdraw` bypasses the aggregator verification-signature gate enforced by `withdraw` - (File: `core/src/rpc/operator.rs`)

### Summary
The `ClementineOperator` gRPC service exposes two separate entry points that both end up calling `Operator::withdraw` and broadcasting a signed Bitcoin payout transaction: `withdraw` and `internal_withdraw`. `withdraw` enforces an ECDSA "verification signature" gate (bound to `config.aggregator_verification_address`) before funding/broadcasting the payout, while `internal_withdraw` calls the exact same underlying state-changing/broadcasting logic with none of that gating. This mirrors the `SettV3.transferFrom` pattern: a protection meant to restrict who can trigger a sensitive action is enforced on one call path but is trivially bypassed via a sibling call path that reaches the same effect.

### Finding Description
`ClementineOperator::withdraw` in [1](#0-0)  requires a valid `verification_signature` recovered to `self.operator.config.aggregator_verification_address` before it calls `self.operator.withdraw(...)`: [2](#0-1) 

`ClementineOperator::internal_withdraw`, however, parses the same `WithdrawParams` and calls the identical `self.operator.withdraw(...)` function with none of this signature check: [3](#0-2) 

Both RPCs are exposed on the same `ClementineOperator` gRPC service surface (see the generated client stubs referencing both `Withdraw` and `InternalWithdraw` methods) and both terminate in `Operator::withdraw`, which does the security-sensitive work: verifying the user's Schnorr signature over the payout sighash, funding the transaction from the operator's own wallet via RBF, signing, and broadcasting it to the network: [4](#0-3) 

Because the `verification_signature` check exists only in the `withdraw` handler and not in `internal_withdraw`, any caller able to reach the operator's gRPC surface can invoke `internal_withdraw` directly with a validly-signed `WithdrawParams` (the user-signature portion is unrelated to the aggregator gate) and skip the aggregator-authorization step entirely — exactly as `transferFrom` let an attacker skip the `_blockLocked(msg.sender)` check that `transfer`/`deposit` enforced.

### Impact Explanation
This is an unauthenticated/unauthorized state-changing and broadcasting call: the intended control flow is that only the aggregator (after its own coordination/validation logic) triggers operator payouts through `withdraw`, gated by `aggregator_verification_address`. `internal_withdraw` reaches the same fund-and-broadcast logic (`Operator::withdraw`, which funds via `fund_raw_transaction`/`sign_raw_transaction_with_wallet`/`send_raw_transaction` from the operator's hot wallet) without that gate, letting any party who learns/replays a `WithdrawParams` for a given withdrawal id force the operator to front a payout outside of the aggregator-authorized flow, defeating the purpose of the `aggregator_verification_address` control. Depending on what other protections that gate was meant to enforce (e.g. preventing double/duplicate front-run submission handling, or restricting to the sanctioned aggregator flow), this can cause an operator to be triggered into fronting payouts it was not meant to process through this path.

### Likelihood Explanation
Likelihood is high whenever `aggregator_verification_address` is configured (the code explicitly supports operating with it unset, in which case there's no gate to bypass at all) and `internal_withdraw` is reachable on the operator's gRPC endpoint. No cryptographic secret needs to be broken — the attacker only needs a `WithdrawParams` including a user signature (obtainable the same way a legitimate caller of `withdraw` would obtain it) and to call `internal_withdraw` instead of `withdraw`.

### Recommendation
Enforce the same `verification_signature` check inside `Operator::withdraw` itself (or in a shared pre-check used by both RPC handlers) rather than only in the `withdraw` gRPC handler, so that no alternate entry point (`internal_withdraw`, or any future one) can bypass the aggregator-authorization gate. This mirrors the correct fix from the referenced report: bind the protection to the operation being protected, not to the specific call path used to reach it.

### Proof of Concept
1. Configure an operator with `aggregator_verification_address` set, expecting all payouts to be authorized by the aggregator's ECDSA signature.
2. Obtain a valid `WithdrawParams` (`withdrawal_id`, `input_signature`, `input_outpoint`, `output_script_pubkey`, `output_amount`) for a pending withdrawal — the same data a legitimate `withdraw` caller would supply.
3. Instead of calling `ClementineOperator::withdraw` (which requires a `verification_signature`), call `ClementineOperator::internal_withdraw` with just the `WithdrawParams`, as implemented at [3](#0-2) .
4. The call proceeds straight to `Operator::withdraw`, which funds, signs, and broadcasts the payout transaction — with the `aggregator_verification_address` gate never evaluated, confirming the bypass.

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

**File:** core/src/operator.rs (L560-691)
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

        // tracing::info!("Payout txhandler: {:?}", hex::encode(bitcoin::consensus::serialize(&payout_txhandler.get_cached_tx())));

        let sighash = payout_txhandler.calculate_sighash_txin(0, in_signature.sighash_type)?;

        SECP.verify_schnorr(
            &in_signature.signature,
            &Message::from_digest(*sighash.as_byte_array()),
            user_xonly_pk,
        )
        .wrap_err("Failed to verify signature received from user for payout txin. Ensure the signature uses SinglePlusAnyoneCanPay sighash type.")?;

        let fee_rate = self
            .rpc
            .get_fee_rate_kvb(
                self.config.protocol_paramset.network,
                &self.config.mempool_api_host,
                &self.config.mempool_api_endpoint,
                self.config.tx_sender_limits.mempool_fee_rate_multiplier,
                self.config.tx_sender_limits.mempool_fee_rate_offset_sat_kvb,
                self.config.tx_sender_limits.fee_rate_hard_cap,
            )
            .await?;

        // send payout tx using RBF
        let funded_tx = self
            .rpc
            .fund_raw_transaction(
                payout_txhandler.get_cached_tx(),
                Some(&bitcoincore_rpc::json::FundRawTransactionOptions {
                    add_inputs: Some(true),
                    include_unsafe: Some(false),
                    change_address: None,
                    change_position: Some(1),
                    change_type: None,
                    include_watching: None,
                    lock_unspents: Some(false),
                    fee_rate: Some(Amount::from_sat(fee_rate.to_sat_per_kvb())),
                    subtract_fee_from_outputs: None,
                    replaceable: None,
                    conf_target: None,
                    estimate_mode: None,
                }),
                None,
            )
            .await
            .wrap_err("Failed to fund raw transaction")?
            .hex;

        let signed_tx = self
            .rpc
            .sign_raw_transaction_with_wallet(&funded_tx, None, None)
            .await
            .wrap_err("Failed to sign withdrawal transaction")?
            .hex;

        let signed_tx: Transaction = bitcoin::consensus::deserialize(&signed_tx)
            .wrap_err("Failed to deserialize signed withdrawal transaction")?;

        self.rpc
            .send_raw_transaction(&signed_tx)
            .await
            .wrap_err("Failed to send withdrawal transaction")?;

        Ok(signed_tx)
```
