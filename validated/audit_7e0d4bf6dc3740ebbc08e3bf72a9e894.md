Confirmed: the user signature for a `payout_tx` uses `TapSighashType::SinglePlusAnyoneCanPay`, which under BIP341 only commits to the single output at the same index as the input (index 0, the user's payout output) and to the single spent prevout — it does **not** commit to any other outputs. This is enforced explicitly in `parse_withdrawal_sig_params` [1](#0-0)  and in `Operator::withdraw`, which verifies the sighash with this exact type [2](#0-1) .

### Title
Payout tx OP_RETURN operator identity is unauthenticated, letting a third party redirect reimbursement credit away from the operator who actually fronted the withdrawal - (File: core/src/builder/transaction/operator_reimburse.rs)

### Summary
The `payout_tx` is built with a `SIGHASH_SINGLE|ANYONECANPAY` user signature that only binds the single withdrawal input and the single user-payout output. The `OP_RETURN` output carrying the `operator_xonly_pk` (used later to attribute credit for having fronted the withdrawal) sits at a different output index and is completely outside the signed message. Anyone observing the unconfirmed `payout_tx` in the mempool can rebuild an equivalent transaction with an arbitrary `operator_xonly_pk` in the `OP_RETURN`, re-use the same valid user signature, and get their version mined instead (e.g. via a higher fee/RBF), all without ever having funded anything or being registered as that operator.

### Finding Description
`create_payout_txhandler` embeds `operator_xonly_pk` in a plain `OP_RETURN` output with no cryptographic binding to the transaction signer or funder [3](#0-2) . The witness set on the transaction is only the user's `SinglePlusAnyoneCanPay` Schnorr signature over input 0 and output 0 [4](#0-3) . Because of `SIGHASH_ANYONECANPAY`, additional inputs (fee funding) can be freely added/changed by whoever broadcasts the tx, and because of `SIGHASH_SINGLE`, only output index 0 is committed — the anchor output and the `OP_RETURN` operator-identity output are unconstrained.

Downstream, verifiers determine "who fronted the payout" purely by parsing this unauthenticated `OP_RETURN` field from the confirmed on-chain payout transaction: `update_finalized_payouts` extracts `operator_xonly_pk` directly from the `OP_RETURN` bytes of whichever `payout_tx` gets confirmed and stores it as `payout_payer_operator_xonly_pk` [5](#0-4) . This value is later used to give reimbursement rights and kickoff assignment exclusively to that xonly pk via `validate_payer_is_operator` (which asserts `payer_xonly_pk == self.signer.xonly_public_key`) [6](#0-5) , `get_first_unhandled_payout_by_operator_xonly_pk` (which is what the `PayoutCheckerTask` polls per-operator) [7](#0-6) , and `is_kickoff_malicious`, which treats a kickoff as fraudulent if its operator doesn't match the `OP_RETURN` pubkey [8](#0-7) .

Because none of this chain re-derives or checks who actually supplied/broadcast the transaction (e.g., no requirement that the operator's own key sign anything in the payout tx, no requirement that the funding UTXOs belong to that operator), an unprivileged attacker who observes a legitimate operator's unconfirmed `payout_tx` in the mempool can:
1. Extract the withdrawal `input_outpoint`/`user_sig`/output requirements from the public mempool transaction (or independently construct the same payout tx once the user's off-chain signature becomes public through any leak/relay, e.g. after first broadcast).
2. Build an equivalent `payout_tx` with the same signed input/output 0, but swap the `OP_RETURN` to declare a **different** `operator_xonly_pk` — either their own key (if it happens to belong to a currently active operator they control) or a targeted third-party operator's key.
3. RBF-replace or otherwise get their version confirmed instead of the original operator's version.

This breaks the intended binding **operator credited == party that funded/paid the withdrawal**: the party whose Bitcoin actually left as the payout is not necessarily the entity later assigned the `Reimburse`/kickoff obligation and the `ReadyToReimburse`/`Reimburse` payout in `create_reimburse_txhandler` [9](#0-8) .

### Impact Explanation
This maps to the "operator reimbursed for a payout it never funded" / "honest operator permanently unable to be reimbursed" impact classes: an attacker (or a malicious operator) can cause a legitimate, honest operator's xonly_pk to be recorded as the fronting party for a withdrawal that operator never actually paid for — assigning that operator the associated kickoff/challenge/BitVM obligations and financial exposure (collateral at risk if the kickoff is challenged and no funds were actually paid by them, or conversely denying the real payer their credit and the ability to be reimbursed through the round/kickoff mechanism, since `validate_payer_is_operator` will reject the true payer's `get_reimbursement_txs` call because the DB now shows a different `payer_xonly_pk`). This is Critical-adjacent: it can permanently prevent an honest operator from being reimbursed, or force an operator's collateral into the obligations of a kickoff for a payout it never made.

### Likelihood Explanation
Requires an unprivileged party to observe the payout tx before it confirms (public Bitcoin mempool) and win a fee race or RBF race, or to reconstruct the tx from the user's signature if it becomes available through any channel before confirmation. No verifier/operator/aggregator role, key compromise, or majority hashrate is required — only normal mempool visibility and standard fee-bumping, which is realistically achievable given `payout_tx` explicitly supports RBF funding via `fund_raw_transaction`/RBF as documented in `Operator::withdraw` [10](#0-9) .

### Recommendation
Cryptographically bind the operator identity to the payout tx: e.g., require the operator's key to co-sign the payout tx (so `SIGHASH_ALL` or the operator's own signature covers the `OP_RETURN`/anchor outputs), or require the funding input(s) added to the payout tx to be provably controlled by/attributed to the claimed `operator_xonly_pk`, rather than trusting an arbitrary unauthenticated `OP_RETURN` field parsed post-confirmation.

### Proof of Concept
1. Operator A calls `withdraw`/`internal_withdraw`, which builds and broadcasts (via RBF) `payout_tx_A` spending withdrawal UTXO `W` with `user_sig` (`SIGHASH_SINGLE|ANYONECANPAY`), output 0 = user's payout, output 2 = `OP_RETURN(A's xonly_pk)` [11](#0-10) , [3](#0-2) .
2. `payout_tx_A` sits unconfirmed in the mempool; its witness (just `user_sig`) and prevout/output-0 details are public.
3. Attacker builds `payout_tx_B`: same input `W` with the same witness `user_sig` (valid, since it only commits input 0/output 0), same output 0, but `OP_RETURN(B's xonly_pk)` where B is any operator (including one the attacker does not control), and different anchor/fee inputs (allowed by `ANYONECANPAY`).
4. Attacker fee-bumps `payout_tx_B` (e.g., higher feerate, or RBF over `payout_tx_A`) so it confirms instead.
5. `update_finalized_payouts` reads the confirmed tx's `OP_RETURN`, records `payout_payer_operator_xonly_pk = B` [5](#0-4) .
6. Operator A (the true payer) can no longer be reimbursed via `validate_payer_is_operator`/`get_reimbursement_txs` because the stored payer no longer matches A [6](#0-5) , while operator B is now on the hook for a kickoff/reimbursement flow for a payout B never made.

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

**File:** core/src/operator.rs (L651-663)
```rust
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
```

**File:** core/src/operator.rs (L1703-1729)
```rust
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
```

**File:** core/src/builder/transaction/operator_reimburse.rs (L341-385)
```rust
pub fn create_reimburse_txhandler(
    move_txhandler: &TxHandler,
    round_txhandler: &TxHandler,
    kickoff_txhandler: &TxHandler,
    kickoff_idx: usize,
    paramset: &'static ProtocolParamset,
    operator_reimbursement_address: &bitcoin::Address,
) -> Result<TxHandler, BridgeError> {
    let builder = TxHandlerBuilder::new(TransactionType::Reimburse)
        .with_version(NON_STANDARD_V3)
        .add_input(
            NormalSignatureKind::Reimburse1,
            move_txhandler.get_spendable_output(UtxoVout::DepositInMove)?,
            builder::script::SpendPath::ScriptSpend(0),
            DEFAULT_SEQUENCE,
        )
        .add_input(
            NormalSignatureKind::Reimburse2,
            kickoff_txhandler.get_spendable_output(UtxoVout::ReimburseInKickoff)?,
            builder::script::SpendPath::ScriptSpend(0),
            DEFAULT_SEQUENCE,
        )
        .add_input(
            NormalSignatureKind::OperatorSighashDefault,
            round_txhandler.get_spendable_output(UtxoVout::ReimburseInRound(
                kickoff_idx,
                paramset.num_kickoffs_per_round,
            ))?,
            builder::script::SpendPath::KeySpend,
            DEFAULT_SEQUENCE,
        );

    Ok(builder
        .add_output(UnspentTxOut::from_partial(TxOut {
            value: move_txhandler
                .get_spendable_output(UtxoVout::DepositInMove)?
                .get_prevout()
                .value,
            script_pubkey: operator_reimbursement_address.script_pubkey(),
        }))
        .add_output(UnspentTxOut::from_partial(
            builder::transaction::anchor_output(paramset.anchor_amount()),
        ))
        .finalize())
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

**File:** core/src/verifier.rs (L1885-1890)
```rust
        };

        if operator_xonly_pk != kickoff_data.operator_xonly_pk {
            tracing::warn!("Operator xonly pk for the payout does not match with the kickoff_data");
            return Ok(true);
        }
```

**File:** core/src/verifier.rs (L2312-2342)
```rust
            // Find the first output that contains OP_RETURN
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

            payout_txs_and_payer_operator_idx.push((
                idx,
                payout_txid,
                operator_xonly_pk,
                block_hash,
            ));
```

**File:** core/src/database/verifier.rs (L282-313)
```rust
    pub async fn get_first_unhandled_payout_by_operator_xonly_pk(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        operator_xonly_pk: XOnlyPublicKey,
    ) -> Result<Option<(u32, Txid, BlockHash)>, BridgeError> {
        let query = sqlx::query_as::<_, (i32, Option<TxidDB>, Option<BlockHashDB>)>(
            "SELECT w.idx, w.move_to_vault_txid, w.payout_tx_blockhash
             FROM withdrawals w
             WHERE w.payout_txid IS NOT NULL
                AND w.is_payout_handled = FALSE
                AND w.payout_payer_operator_xonly_pk = $1
                ORDER BY w.idx ASC
             LIMIT 1",
        )
        .bind(XOnlyPublicKeyDB(operator_xonly_pk));

        let results = execute_query_with_tx!(self.connection, tx, query, fetch_optional)?;

        results
            .map(|(citrea_idx, move_to_vault_txid, payout_tx_blockhash)| {
                Ok((
                    u32::try_from(citrea_idx).wrap_err("Failed to convert citrea index to u32")?,
                    move_to_vault_txid
                        .expect("move_to_vault_txid Must be Some")
                        .0,
                    payout_tx_blockhash
                        .expect("payout_tx_blockhash Must be Some")
                        .0,
                ))
            })
            .transpose()
    }
```
