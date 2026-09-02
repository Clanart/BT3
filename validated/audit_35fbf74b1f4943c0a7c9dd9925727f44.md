Confirmed: the withdrawal signature uses `TapSighashType::SinglePlusAnyoneCanPay`, and `parse_withdrawal_sig_params` in `core/src/rpc/parser/operator.rs:161-203` explicitly enforces this sighash type [1](#0-0) . `create_payout_txhandler` builds the OP_RETURN output that names the fronting operator's xonly pubkey, but only output index 0 (the user payout) is signed by the withdrawer via `set_p2tr_key_spend_witness(&user_sig, 0)` — the OP_RETURN operator-attribution output and any funding inputs are unsigned/unconstrained [2](#0-1) . Reimbursement eligibility is later determined purely by reading this OP_RETURN pubkey back off-chain via `update_finalized_payouts` and cross-checked in `validate_payer_is_operator` against `self.signer.xonly_public_key` [3](#0-2) [4](#0-3) .

### Title
Unauthenticated OP_RETURN operator-attribution in `SinglePlusAnyoneCanPay` payout tx allows reassigning or nullifying reimbursement credit - (File: `core/src/builder/transaction/operator_reimburse.rs`)

### Summary
`create_payout_txhandler` signs only the user-payout output (index 0) with `SinglePlusAnyoneCanPay`, leaving the OP_RETURN output that names "the operator who fronted the peg-out" completely unauthenticated and outside the signed message. Because `SinglePlusAnyoneCanPay` permits arbitrary additional/replacement inputs and outputs beyond the signed one, any party who observes the signed witness (e.g., in a broadcast-but-unconfirmed payout transaction in the Bitcoin mempool) can construct a competing, higher-fee transaction that reuses the same signed input/output and pays the exact same user, but substitutes an arbitrary xonly pubkey into the OP_RETURN — attributing the reimbursement credit to an operator (or an unregistered key) that did not actually front the funds.

### Finding Description
The payout transaction has one signed input (the user's withdrawal-claim UTXO, signed `SinglePlusAnyoneCanPay`) and the fronting operator's identity is embedded in an OP_RETURN output that is never covered by that signature: [2](#0-1) 

`parse_withdrawal_sig_params` strictly enforces the `SinglePlusAnyoneCanPay` sighash type for the withdrawal signature [5](#0-4) , and `Operator::withdraw` verifies this exact signature/sighash against the payout-tx sighash before funding and broadcasting [6](#0-5) . Under `SinglePlusAnyoneCanPay`, only the txout at the signed input's index is committed by the signature — additional inputs (used to fund the large payout amount, since the underlying UTXO is only a dust amount, see `generate_withdrawal_utxo`/`WITHDRAWAL_EMPTY_UTXO_SATS` in `core/src/test/common/setup_utils.rs:480-497`) and all other outputs, including the OP_RETURN carrying the operator's xonly pubkey, are unconstrained.

Downstream, the bridge's bookkeeping for "who gets reimbursed" is derived solely from parsing that OP_RETURN once the payout transaction confirms: [3](#0-2) 

and reimbursement eligibility is gated on that recorded pubkey matching the operator's own signer key: [4](#0-3) 

Because the OP_RETURN content is neither signed by the user nor otherwise bound to whoever actually supplies the funding inputs, any unprivileged observer who captures the signed witness (trivially visible once one operator broadcasts its payout attempt to the public Bitcoin mempool) can assemble a rival transaction: same signed input, same committed user-payout output, but a different funding-input set and an arbitrary OP_RETURN pubkey, then replace the original transaction via ordinary fee-bumping (RBF). This breaks the equality that should hold — `operator credited == party that actually paid` — allowing:
- Misattribution of reimbursement credit to an operator who never fronted the withdrawal (an operator is reimbursed for a payout it never funded), or
- Insertion of a pubkey that matches no live operator, which permanently prevents any operator from passing `validate_payer_is_operator`, leaving the `MoveToVaultTx`'s `DepositInMove` output un-reimbursable through the normal Reimburse path.

### Impact Explanation
This crosses the "operator credited versus the party that paid" custody binding. A legitimate operator can be credited with reimbursement rights for a payout it never funded (Critical: operator reimbursed for a payout it never funded), while the operator that actually fronted real BTC to the withdrawing user loses out on its designated reimbursement path (Critical: honest operator permanently unable to be reimbursed) — or, if the injected pubkey belongs to no registered operator, the deposit's vault UTXO becomes permanently unclaimable via the Reimburse flow (Critical: vault UTXO permanently frozen).

### Likelihood Explanation
The attack requires no special role (verifier/operator/aggregator/security council) — only capital to front the same withdrawal output and the ability to observe an unconfirmed transaction in the public Bitcoin mempool and replace it with a higher fee, both of which are available to any unprivileged network participant. The only "cost" is fronting the withdrawal amount, which is inherent to the payout mechanism itself, not a special privilege.

### Recommendation
Bind the OP_RETURN operator-attribution output (and ideally the full set of funding inputs) into the signed message committed by the withdrawal signature, e.g. by using `AllPlusAnyoneCanPay` combined with a covenant/committed template for the OP_RETURN output, or by deriving operator credit from a value that is cryptographically tied to whichever party actually supplied the funding inputs (such as requiring the additional funding inputs to originate from a script that only the credited operator's key can spend), rather than trusting an unauthenticated on-chain OP_RETURN field.

### Proof of Concept
1. Operator A observes withdrawal request (id, dust UTXO, `SinglePlusAnyoneCanPay` signature) and builds/broadcasts `payout_tx_A`: input = user's dust UTXO + valid witness, output[0] = user payout (committed by signature), output[2] = OP_RETURN(A's xonly pubkey), funded with A's own additional inputs, per `core/src/builder/transaction/operator_reimburse.rs:407-436` and `core/src/operator.rs:620-674`.
2. `payout_tx_A` sits unconfirmed in the Bitcoin mempool; its witness (containing the `SinglePlusAnyoneCanPay` signature) is public.
3. Attacker extracts the signed input+witness and constructs `payout_tx_B`: identical signed input/witness, identical output[0] (required by `SinglePlusAnyoneCanPay`'s output-index commitment), but with attacker-supplied funding inputs and OP_RETURN(B's xonly pubkey) where B is any other registered operator (or an arbitrary/unregistered key).
4. Attacker broadcasts `payout_tx_B` with a higher fee, replacing `payout_tx_A` in the mempool; `payout_tx_B` confirms.
5. `Verifier::update_finalized_payouts` parses the OP_RETURN from the confirmed `payout_tx_B` and records B's pubkey as `payout_payer_operator_xonly_pk` (`core/src/verifier.rs:2313-2342`).
6. Operator B later calls the reimbursement flow; `validate_payer_is_operator` succeeds because the DB-recorded payer key equals B's signer key (`core/src/operator.rs:1686-1739`), so B is reimbursed for a withdrawal it never funded — while A, the true funder in this scenario, has no recorded claim.

### Citations

**File:** core/src/rpc/parser/operator.rs (L161-187)
```rust
#[allow(clippy::result_large_err)]
pub fn parse_withdrawal_sig_params(
    params: WithdrawParams,
) -> Result<(u32, taproot::Signature, OutPoint, ScriptBuf, Amount), Status> {
    let mut input_signature =
        taproot::Signature::from_slice(&params.input_signature).map_err(|e| {
            Status::invalid_argument(format!("Can't convert input to taproot Signature - {e}"))
        })?;

    // If the Taproot sighash type is Default (no explicit type attached; i.e. a 64-byte
    // signature without a sighash flag), normalize it to SinglePlusAnyoneCanPay.
    // Prior to v0.5 this was Clementine's implicit behavior; we retain it here for
    // backwards compatibility when a 64-byte signature is provided.
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

**File:** core/src/verifier.rs (L2313-2335)
```rust
            let circuit_payout_tx = CircuitTransaction::from(payout_tx.clone());
            let op_return_output = get_first_op_return_output(&circuit_payout_tx);

            // If OP_RETURN doesn't exist in any outputs, or the data in OP_RETURN is not a valid xonly_pubkey,
            // operator_xonly_pk will be set to None, and the corresponding column in DB set to NULL.
            // This can happen if optimistic payout is used, or an operator constructs the payout tx wrong.
            let operator_xonly_pk = op_return_output
                .and_then(|output| parse_op_return_data(&output.script_pubkey))
                .and_then(|bytes| XOnlyPublicKey::from_slice(bytes).ok());

            if operator_xonly_pk.is_none() {
                tracing::info!(
                    "No valid operator xonly pk found in payout tx {:?} OP_RETURN. Either it is an optimistic payout or the operator constructed the payout tx wrong",
                    payout_txid
                );
            }

            tracing::info!(
                "A new payout tx detected for withdrawal {}, payout txid: {:?}, operator xonly pk: {:?}",
                idx,
                payout_txid,
                operator_xonly_pk
            );
```

**File:** core/src/operator.rs (L620-637)
```rust
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

**File:** core/src/operator.rs (L1686-1739)
```rust
    /// For a deposit_id checks that the payer for that deposit is the operator, and the payout blockhash and kickoff txid are set.
    async fn validate_payer_is_operator(
        &self,
        dbtx: Option<DatabaseTransaction<'_>>,
        deposit_id: u32,
    ) -> Result<(BlockHash, Txid), BridgeError> {
        let (payer_xonly_pk, payout_blockhash, kickoff_txid) = self
            .db
            .get_payer_xonly_pk_blockhash_and_kickoff_txid_from_deposit_id(dbtx, deposit_id)
            .await?;

        tracing::info!(
            "Payer xonly pk and kickoff txid found for the requested deposit, payer xonly pk: {:?}, kickoff txid: {:?}",
            payer_xonly_pk,
            kickoff_txid
        );

        // first check if the payer is the operator, and the kickoff is handled
        // by the PayoutCheckerTask, meaning kickoff_txid is set
        let (payout_blockhash, kickoff_txid) = match (
            payer_xonly_pk,
            payout_blockhash,
            kickoff_txid,
        ) {
            (Some(payer_xonly_pk), Some(payout_blockhash), Some(kickoff_txid)) => {
                if payer_xonly_pk != self.signer.xonly_public_key {
                    return Err(eyre::eyre!(
                        "Payer is not own operator for deposit, payer xonly pk: {:?}, operator xonly pk: {:?}",
                        payer_xonly_pk,
                        self.signer.xonly_public_key
                    )
                    .into());
                }
                (payout_blockhash, kickoff_txid)
            }
            _ => {
                return Err(eyre::eyre!(
                    "Payer info not found for deposit, payout blockhash: {:?}, kickoff txid: {:?}",
                    payout_blockhash,
                    kickoff_txid
                )
                .into());
            }
        };

        tracing::info!(
            "Payer xonly pk, payout blockhash and kickoff txid found and valid for own operator for the requested deposit id: {}, payer xonly pk: {:?}, payout blockhash: {:?}, kickoff txid: {:?}",
            deposit_id,
            payer_xonly_pk,
            payout_blockhash,
            kickoff_txid
        );

        Ok((payout_blockhash, kickoff_txid))
```
