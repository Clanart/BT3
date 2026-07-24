### Title
Operator Payout Transaction Front-Running via Unbound `SIGHASH_SINGLE|ANYONECANPAY` Signature Allows Reimbursement Theft — (`core/src/builder/transaction/operator_reimburse.rs`)

---

### Summary

The user's withdrawal signature is enforced to use `SIGHASH_SINGLE | SIGHASH_ANYONECANPAY`. This sighash type commits only to the user's own input (index 0) and the output at the same index (output 0, the user's payout). It does **not** commit to the `OP_RETURN` output (output 2) that encodes the operator's `xonly_public_key` — the field that determines which operator is credited and reimbursed from the bridge vault. Any registered operator who observes a pending payout transaction in the Bitcoin mempool can extract the user's signature, construct a competing payout transaction substituting their own `xonly_public_key` in the `OP_RETURN`, and broadcast it with a higher fee. If confirmed first, the attacker's operator receives the bridge-vault reimbursement; the original operator's transaction is evicted as a double-spend.

---

### Finding Description

**`create_payout_txhandler`** in `core/src/builder/transaction/operator_reimburse.rs` builds the payout transaction as:

| Index | Output | Committed by user sig? |
|---|---|---|
| 0 | User payout (`output_txout`) | **Yes** (`SIGHASH_SINGLE`) |
| 1 | Anchor output | No |
| 2 | `OP_RETURN(operator_xonly_pk)` | **No** | [1](#0-0) 

The `OP_RETURN` at output 2 carries the operator's `xonly_public_key` and is the sole on-chain signal used by the bridge to identify which operator performed the payout and is entitled to reimbursement. [2](#0-1) 

`parse_withdrawal_sig_params` enforces `SinglePlusAnyoneCanPay` on every incoming user signature: [3](#0-2) 

Because `SIGHASH_SINGLE | SIGHASH_ANYONECANPAY` does not cover outputs beyond index 0, the `OP_RETURN` at index 2 is entirely mutable by any third party who re-uses the same user signature. The user's signature is embedded in the payout transaction and is therefore publicly visible in the Bitcoin mempool once operator A broadcasts it.

The `OperatorWithdrawalMessage` / `OptimisticPayoutMessage` EIP-712 structs — the off-chain authorization layer — also contain no operator identity field: [4](#0-3) 

So the aggregator's `verification_signature` is equally valid for any operator, and the `aggregator_verification_address` guard is itself optional (`Option<Address>`): [5](#0-4) 

When `None`, the `withdraw` RPC skips all verification: [6](#0-5) 

The `internal_withdraw` endpoint carries **no** verification check at all: [7](#0-6) 

---

### Impact Explanation

A malicious registered operator (operator B) who monitors the Bitcoin mempool:

1. Extracts the user's `SinglePlusAnyoneCanPay` signature from operator A's pending payout transaction.
2. Constructs a new payout transaction with the same input (user UTXO + user signature), the same output 0 (user payout — committed by the user's signature), but substitutes their own `xonly_public_key` in the `OP_RETURN` at output 2.
3. Broadcasts with a higher fee.

If operator B's transaction confirms first:
- The bridge vault's `PayoutCheckerTask` reads operator B's `xonly_public_key` from the `OP_RETURN` and credits operator B as the payer.
- Operator B sends their own pre-signed kickoff transaction and receives the full bridge-amount reimbursement from the move-to-vault UTXO.
- Operator A's transaction is evicted as a double-spend; operator A loses the withdrawal fee (`operator_withdrawal_fee_sats`) and any Bitcoin transaction fees already paid.

The corrupted value is the `operator_xonly_pk` field in the `OP_RETURN` of the confirmed payout transaction, which redirects the bridge-vault reimbursement to the wrong operator.

---

### Likelihood Explanation

- Requires the attacker to be a registered operator (has collateral and pre-signed kickoff transactions for the target deposit).
- Requires monitoring the Bitcoin mempool — trivially achievable by any Bitcoin node operator.
- No cryptographic material needs to be forged; the user's signature is reused verbatim.
- The attack window is the time between operator A broadcasting the payout transaction and it being confirmed (typically seconds to minutes).
- The `aggregator_verification_address` guard, even when enabled, does not prevent this because the signed message contains no operator identity.

---

### Recommendation

1. **Bind the operator identity in the user's signed data.** Move the `OP_RETURN` output to index 0 so that `SIGHASH_SINGLE` commits to it, or include the operator's `xonly_public_key` as a data push inside the output that the user signs (e.g., a `OP_RETURN` at index 0 before the payout output).
2. **Alternatively, add the operator's `xonly_public_key` to `OperatorWithdrawalMessage` / `OptimisticPayoutMessage`.** The aggregator signs over the specific operator identity, and verifiers/operators reject any request where the claimed operator does not match the signed field.
3. **Make `aggregator_verification_address` mandatory** in production configurations so that the off-chain authorization layer is always active.

---

### Proof of Concept

```
1. Operator A calls withdraw() → payout_tx is created with:
     input[0]:  user_utxo  (user sig: SIGHASH_SINGLE|ANYONECANPAY)
     output[0]: user_payout_txout          ← committed by user sig
     output[1]: anchor_output
     output[2]: OP_RETURN(operator_A_xonly_pk)  ← NOT committed

2. Operator A broadcasts payout_tx to Bitcoin mempool.

3. Operator B (registered, has kickoff txs for this deposit) reads
   payout_tx from mempool, extracts user_sig.

4. Operator B constructs payout_tx_B:
     input[0]:  user_utxo  (same user_sig — still valid)
     output[0]: user_payout_txout          ← identical, sig still valid
     output[1]: anchor_output
     output[2]: OP_RETURN(operator_B_xonly_pk)  ← substituted

5. Operator B broadcasts payout_tx_B with fee > payout_tx fee.

6. payout_tx_B confirms; payout_tx is evicted (double-spend).

7. Bridge PayoutCheckerTask reads operator_B_xonly_pk from OP_RETURN,
   records operator B as payer.

8. Operator B sends kickoff_tx → receives bridge_amount reimbursement
   from move-to-vault UTXO.

9. Operator A: withdrawal fee lost; Bitcoin tx fees lost.
```

### Citations

**File:** core/src/builder/transaction/operator_reimburse.rs (L407-436)
```rust
pub fn create_payout_txhandler(
    input_utxo: UTXO,
    output_txout: TxOut,
    operator_xonly_pk: XOnlyPublicKey,
    user_sig: taproot::Signature,
    _network: bitcoin::Network,
) -> Result<TxHandler<Signed>, BridgeError> {
    let txin = SpendableTxIn::new_partial(input_utxo.outpoint, input_utxo.txout);

    let output_txout = UnspentTxOut::from_partial(output_txout.clone());

    let op_return_txout = op_return_txout(PushBytesBuf::from(operator_xonly_pk.serialize()));

    let mut txhandler = TxHandlerBuilder::new(TransactionType::Payout)
        .with_version(NON_STANDARD_V3)
        .add_input(
            NormalSignatureKind::NotStored,
            txin,
            SpendPath::KeySpend,
            DEFAULT_SEQUENCE,
        )
        .add_output(output_txout)
        .add_output(UnspentTxOut::from_partial(anchor_output(
            NON_EPHEMERAL_ANCHOR_AMOUNT,
        )))
        .add_output(UnspentTxOut::from_partial(op_return_txout))
        .finalize();
    txhandler.set_p2tr_key_spend_witness(&user_sig, 0)?;
    txhandler.promote()
}
```

**File:** core/src/rpc/parser/operator.rs (L174-187)
```rust
    if input_signature.sighash_type == TapSighashType::Default {
        tracing::warn!(
            "Input signature for withdrawal {} has sighash type default, setting to SinglePlusAnyoneCanPay", params.withdrawal_id,
        );
        input_signature.sighash_type = TapSighashType::SinglePlusAnyoneCanPay;
    }

    // enforce sighash type here
    if input_signature.sighash_type != TapSighashType::SinglePlusAnyoneCanPay {
        return Err(Status::invalid_argument(format!(
            "Input signature has wrong sighash type, SinglePlusAnyoneCanPay expected, got {}",
            input_signature.sighash_type
        )));
    }
```

**File:** core/src/rpc/ecdsa_verification_sig.rs (L20-40)
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
```

**File:** core/src/config/mod.rs (L148-153)
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
