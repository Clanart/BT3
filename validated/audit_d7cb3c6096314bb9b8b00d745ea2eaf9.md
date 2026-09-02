Confirmed: `parse_withdrawal_sig_params` enforces `TapSighashType::SinglePlusAnyoneCanPay` on the user's payout-input signature, and `create_payout_txhandler` builds only the user output, an anchor, and the OP_RETURN (with the *caller-supplied* `operator_xonly_pk`) without any additional binding of that OP_RETURN value to the signature.

### Title
Unsigned OP_RETURN operator-attribution field lets an unprivileged relayer misattribute a payout to an uninvolved operator - (File: `core/src/builder/transaction/operator_reimburse.rs`)

### Summary
The withdrawal accounting binding that must hold is: `operator credited for reimbursement == operator who actually funded/broadcast the payout output`. Because the user's withdrawal signature uses `SIGHASH_SINGLE|ANYONECANPAY` [1](#0-0)  and the payout transaction's OP_RETURN operator-attribution output is added *after* signing and is not covered by that sighash, any party who obtains the withdrawal signature (sent to every operator over the `Withdraw` RPC, and readable off the finalized on-chain payout tx once broadcast) can build and broadcast their own payout transaction that reuses the same signed input/output but swaps in an arbitrary `operator_xonly_pk` in the OP_RETURN.

### Finding Description
`create_payout_txhandler` constructs the payout tx with the withdrawal UTXO as the sole signed input (key-spend, `SinglePlusAnyoneCanPay`), the user's requested output, an anchor output, and an OP_RETURN output containing `operator_xonly_pk.serialize()` supplied as a plain function argument: [2](#0-1) . `SIGHASH_SINGLE|ANYONECANPAY` commits only to input 0 and output 0 (the user payout output); it does **not** commit to the anchor output or, critically, the OP_RETURN output that names which operator gets reimbursement credit. `parse_withdrawal_sig_params` only checks that the sighash flag is `SinglePlusAnyoneCanPay`; it does not, and cannot, prevent a party from re-assembling a different transaction around the same signed input [3](#0-2) .

Downstream, `update_finalized_payouts` in the verifier's block-processing pipeline scans the confirmed payout transaction for its first OP_RETURN output and unconditionally trusts its contents as the payer operator's xonly pubkey, storing it as `payout_payer_operator_xonly_pk`: [4](#0-3) . This value is what `get_first_unhandled_payout_by_operator_xonly_pk` later uses to decide which operator's `PayoutCheckerTask` treats the withdrawal as its own fronted payout and proceeds to build a kickoff / claim reimbursement for it [5](#0-4) [6](#0-5) .

Because the withdrawal UTXO, the signature, the user's requested output script/amount are all exposed to any of the operators (and become fully public the instant the real payout tx is broadcast/confirmed, since Bitcoin transactions are public), an unprivileged actor who is racing to relay the transaction can substitute their own OP_RETURN with a *different* operator's xonly pubkey (one who never funded anything) before the genuine payer's version confirms. Whichever payout transaction actually gets mined determines, via `update_finalized_payouts`, who is credited as payer — independent of who actually paid the fee/funded the transaction inputs added via ANYONECANPAY.

### Impact Explanation
This breaks the custody binding "operator credited for reimbursement == party that funded the payout." If an attacker (or a malicious/careless operator racing another) confirms a payout tx crediting Operator B while Operator A (or the attacker itself, using ANYONECANPAY to add its own funding input) actually paid, Operator B's `PayoutCheckerTask` will treat the withdrawal as its own, proceed through `handle_finalized_payout`, and eventually attempt kickoff/reimbursement for a payout it never made — matching the report's "Critical: an operator reimbursed for a payout it never funded" impact class, and conversely the operator that did front the withdrawal is left without a path to reimbursement, matching "an honest operator permanently unable to be reimbursed."

### Likelihood Explanation
Exploitation requires only network-level access to the broadcast withdrawal signature (visible to all operators via the `Withdraw`/`InternalWithdraw` RPC surface, and to anyone monitoring the mempool/chain once any operator submits a candidate payout tx) plus the ability to add funding inputs to a `SIGHASH_SINGLE|ANYONECANPAY` transaction and relay it faster than the legitimate payer — no privileged role, key, or verifier/aggregator cooperation is needed, only unprivileged tooling and mempool relay timing.

### Recommendation
Commit the operator-attribution data to the signed portion of the payout transaction (e.g., have the user or aggregator co-sign/commit to the intended operator, or move the OP_RETURN before the signed output/commit it via `SIGHASH_ALL` for at least that output), or otherwise cryptographically bind `payout_payer_operator_xonly_pk` to the actual funding operator rather than trusting an unsigned OP_RETURN field parsed after confirmation.

### Proof of Concept
1. Operator A prepares and broadcasts a real payout tx for withdrawal `idx` using the leaked `SinglePlusAnyoneCanPay` signature, adding its own funding input(s) and its own xonly pubkey in the OP_RETURN (per `create_payout_txhandler`).
2. Before Operator A's transaction confirms, an attacker (or any other party, including a colluding/careless Operator B) observes the mempool, copies the same signed input/output pair, appends different funding inputs (their own coins), and rebuilds the OP_RETURN with Operator B's `xonly_pk` instead of Operator A's — this is valid because `SIGHASH_SINGLE|ANYONECANPAY` does not cover the OP_RETURN output or any inputs beyond input 0.
3. If the attacker's variant confirms first, `update_finalized_payouts` records `payout_payer_operator_xonly_pk = Operator B` [7](#0-6) , and `PayoutCheckerTask` for Operator B picks up the withdrawal as its own via `get_first_unhandled_payout_by_operator_xonly_pk` and proceeds to seek reimbursement despite never having funded the payout output [8](#0-7) .

### Citations

**File:** core/src/rpc/parser/operator.rs (L161-203)
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

    let input_outpoint: OutPoint = params
        .input_outpoint
        .ok_or_else(error::input_ended_prematurely)?
        .try_into()?;

    let users_intent_script_pubkey = ScriptBuf::from_bytes(params.output_script_pubkey);

    Ok((
        params.withdrawal_id,
        input_signature,
        input_outpoint,
        users_intent_script_pubkey,
        Amount::from_sat(params.output_amount),
    ))
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

**File:** core/src/verifier.rs (L2311-2335)
```rust
            let payout_tx = &block.txdata[*payout_tx_idx];
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

**File:** core/src/task/payout_checker.rs (L39-80)
```rust
    async fn run_once(&mut self) -> Result<Self::Output, BridgeError> {
        let mut dbtx = self.db.begin_transaction().await?;
        let unhandled_payout = self
            .db
            .get_first_unhandled_payout_by_operator_xonly_pk(
                Some(&mut dbtx),
                self.operator.signer.xonly_public_key,
            )
            .await?;

        if unhandled_payout.is_none() {
            return Ok(false);
        }

        let (citrea_idx, move_to_vault_txid, payout_tx_blockhash) =
            unhandled_payout.expect("Must be Some");

        tracing::info!(
            "Unhandled payout found for withdrawal {}, move_txid: {}",
            citrea_idx,
            move_to_vault_txid
        );

        let deposit_data = self
            .db
            .get_deposit_data_with_move_tx(Some(&mut dbtx), move_to_vault_txid)
            .await?;
        if deposit_data.is_none() {
            return Err(eyre::eyre!("Fronted withdrawal for move tx {move_to_vault_txid} found, but the signatures for the deposit are not found in the db.").into());
        }

        let deposit_data = deposit_data.expect("Must be Some");

        let kickoff_txid = self
            .operator
            .handle_finalized_payout(
                &mut dbtx,
                deposit_data.get_deposit_outpoint(),
                payout_tx_blockhash,
            )
            .await?;

```
