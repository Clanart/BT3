### Title
`Operator::withdraw()` Accepts Arbitrary `out_script_pubkey` Without Script-Type Validation — (`File: core/src/operator.rs`)

### Summary

The `Operator::withdraw()` function in `core/src/operator.rs` accepts a caller-supplied `out_script_pubkey` and builds a payout transaction without validating that the script is a standard, relay-able Bitcoin script type. The optimistic-payout path (`Verifier::sign_optimistic_payout()` and `AggregatorService::optimistic_payout()`) performs this validation explicitly; the standard operator withdrawal path does not, creating an inconsistency that can result in the user's bridged BTC being permanently unspendable or the payout transaction being unbroadcastable.

### Finding Description

`Operator::withdraw()` receives `out_script_pubkey: ScriptBuf` from the caller and immediately wraps it into a `TxOut` without any script-type check:

```rust
// core/src/operator.rs  L583-L586
let output_txout = TxOut {
    value: out_amount,
    script_pubkey: out_script_pubkey,   // ← no validation
};
```

The function then verifies the user's Schnorr signature over the sighash that commits to this output (via `TapSighashType::SinglePlusAnyoneCanPay`), funds the transaction with `fund_raw_transaction`, and broadcasts it. No check equivalent to the one present in both the aggregator and verifier optimistic-payout paths is performed:

```rust
// core/src/rpc/aggregator.rs  L1044-L1054  (optimistic_payout)
if !(output_script_pubkey.is_p2tr()
    || output_script_pubkey.is_p2pkh()
    || output_script_pubkey.is_p2sh()
    || output_script_pubkey.is_p2wpkh()
    || output_script_pubkey.is_p2wsh())
{
    return Err(Status::invalid_argument(...));
}
```

The same guard is present in `Verifier::sign_optimistic_payout()` at lines 1589–1598. Neither the `internal_withdraw` nor the `withdraw` RPC handler in `core/src/rpc/operator.rs` adds this check before delegating to `Operator::withdraw()`.

### Impact Explanation

Two concrete outcomes are possible:

1. **Non-standard script (e.g., bare `OP_RETURN` with non-zero value, custom script):** Bitcoin nodes reject the transaction as non-standard. `send_raw_transaction` fails, the withdrawal is not processed, and the operator's wallet UTXOs added by `fund_raw_transaction` remain temporarily locked. The user's withdrawal UTXO is not spent, so the user can retry, but the operator suffers a transient liquidity disruption.

2. **Valid but unspendable standard script (e.g., P2PKH with an all-zero hash, P2TR with an uncontrolled key):** The transaction is standard and will be relayed and confirmed. The user's bridged BTC is sent to an address from which it can never be recovered — a permanent loss of bridged BTC. The operator is subsequently reimbursed through the kickoff/reimburse flow regardless of whether the user's output is spendable, so the operator does not lose funds; only the user does.

### Likelihood Explanation

The `aggregator_verification_address` guard is optional (config-gated). When unset — which is the default — any caller who can produce a valid `TapSighashType::SinglePlusAnyoneCanPay` signature over the desired output can trigger the path. A user who mistakenly or maliciously constructs a withdrawal request with an unspendable destination script will have their BTC permanently burned with no protocol-level safeguard stopping it, while the identical request routed through the optimistic-payout path would be rejected.

### Recommendation

Add the same script-type guard that exists in `Verifier::sign_optimistic_payout()` and `AggregatorService::optimistic_payout()` at the top of `Operator::withdraw()`, before the `TxOut` is constructed:

```rust
if !(out_script_pubkey.is_p2tr()
    || out_script_pubkey.is_p2pkh()
    || out_script_pubkey.is_p2sh()
    || out_script_pubkey.is_p2wpkh()
    || out_script_pubkey.is_p2wsh())
{
    return Err(eyre::eyre!(
        "Output script pubkey is not a standard script: {out_script_pubkey}"
    ).into());
}
```

This makes the standard withdrawal path consistent with the optimistic-payout path and prevents both non-standard-script broadcast failures and accidental permanent BTC loss.

### Proof of Concept

1. User holds a valid withdrawal UTXO registered in Citrea.
2. User constructs `WithdrawParams` with `output_script_pubkey` set to an arbitrary byte sequence (e.g., `OP_RETURN <data>` with non-zero `output_amount`, or a P2PKH with a zeroed hash).
3. User signs the payout sighash (committing to this output) with `TapSighashType::SinglePlusAnyoneCanPay`.
4. User calls `Operator::withdraw` (via `internal_withdraw` or `withdraw` RPC).
5. `parse_withdrawal_sig_params` accepts the script without validation.
6. `Operator::withdraw` builds the `TxOut`, verifies the signature (valid, since the user signed for this exact output), funds and signs the transaction, and calls `send_raw_transaction`.
7. For a non-standard script: Bitcoin node rejects the transaction; operator's wallet UTXOs are locked.
8. For a valid unspendable script: transaction is confirmed; user's BTC is permanently unspendable; operator proceeds to kickoff/reimburse and recovers its funds from the bridge.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** core/src/operator.rs (L560-586)
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

**File:** core/src/rpc/aggregator.rs (L1044-1054)
```rust
        // check for some standard script pubkeys
        if !(output_script_pubkey.is_p2tr()
            || output_script_pubkey.is_p2pkh()
            || output_script_pubkey.is_p2sh()
            || output_script_pubkey.is_p2wpkh()
            || output_script_pubkey.is_p2wsh())
        {
            return Err(Status::invalid_argument(format!(
                "Output script pubkey is not a valid script pubkey: {output_script_pubkey}, must be p2tr, p2pkh, p2sh, p2wpkh, or p2wsh"
            )));
        }
```

**File:** core/src/verifier.rs (L1588-1599)
```rust
        // check for some standard script pubkeys
        if !(output_script_pubkey.is_p2tr()
            || output_script_pubkey.is_p2pkh()
            || output_script_pubkey.is_p2sh()
            || output_script_pubkey.is_p2wpkh()
            || output_script_pubkey.is_p2wsh())
        {
            return Err(eyre::eyre!(format!(
                "Output script pubkey is not a valid script pubkey: {}, must be p2tr, p2pkh, p2sh, p2wpkh, or p2wsh",
                output_script_pubkey
            )).into());
        }
```
