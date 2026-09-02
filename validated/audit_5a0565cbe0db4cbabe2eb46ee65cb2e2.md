### Title
Unauthenticated OP_RETURN operator attribution combined with `SIGHASH_SINGLE|ANYONECANPAY` payout signatures lets anyone credit/kickoff a payout for an operator who never funded it - (`core/src/verifier.rs`)

### Summary
`update_finalized_payouts` attributes a payout to whichever xonly public key is embedded in the payout transaction's OP_RETURN output, with no cryptographic check that the named operator actually signed, funded, or even knows about that transaction. Because the withdrawal input signature is required to use `TapSighashType::SinglePlusAnyoneCanPay`, any party who obtains that signature (which is trivially the withdrawing user's own signature) can independently construct and broadcast a competing payout transaction that pays the user's committed output while attaching an arbitrary OP_RETURN naming a different operator, causing that operator's `PayoutCheckerTask` to pick it up and commit a kickoff on their behalf for a payout they never made.

### Finding Description
The broken binding: `operator_credited_and_reimbursed_for_withdrawal_i == party_whose_funds_actually_paid_that_payout`.

`update_finalized_payouts` in `core/src/verifier.rs` derives the "payer" solely from the payout tx's OP_RETURN bytes: [1](#0-0) 
There is no check binding this xonly pubkey to the identity that funded/signed the transaction — it is plain, unsigned data written by `op_return_txout` in `create_payout_txhandler`: [2](#0-1) 

The only cryptographic constraint on the payout transaction is the user's withdrawal signature, which the RPC layer enforces to be `TapSighashType::SinglePlusAnyoneCanPay`: [3](#0-2) 
`SIGHASH_SINGLE|ANYONECANPAY` only commits the signed input and the single corresponding output (index 0, the user's payout). It does **not** commit any other input or output. This means any party possessing that signature (which is the withdrawing user's own signature, created by the user themselves when registering a withdrawal) can freely construct a *different* transaction that: reuses the same signed input/output-0 pair, adds its own funding input(s) to cover fees/any extra value, and attaches any OP_RETURN output naming an arbitrary operator xonly pubkey — none of which invalidates the signature.

Once such a transaction confirms, `update_finalized_payouts` records the named (uninvolved) operator as the payer, and that operator's own `PayoutCheckerTask::run_once` — which filters strictly by `self.operator.signer.xonly_public_key` — will pick it up: [4](#0-3) 
and call `Operator::handle_finalized_payout`, which looks up an unused, pre-signed kickoff connector for that deposit/operator and (with automation) commits/broadcasts the kickoff: [5](#0-4) 

None of the existing guards intercept this: `Verifier::is_deposit_valid` and `Operator::is_profitable` are only invoked on the *operator's own* `withdraw` RPC path (`core/src/operator.rs:560-627`), not on transactions constructed and broadcast directly to Bitcoin by a third party; the kickoff itself is pre-signed N-of-N and requires no operator signature at trigger time, so no signature-forgery check applies to attribution; and `update_finalized_payouts`/`get_first_unhandled_payout_by_operator_xonly_pk` never re-derive the payer from anything other than the OP_RETURN bytes.

### Impact Explanation
The finding matches the listed Critical category "an operator reimbursed for a payout it never funded." The operator whose pubkey is written into the attacker-controlled OP_RETURN gets its `handle_finalized_payout` triggered, consuming one of its scarce pre-signed kickoff connectors and starting the kickoff/round/reimbursement flow for a withdrawal it never fronted funds for — this is reimbursement credited to the wrong party, exactly the invariant the question identifies as broken. This is repeatable per withdrawal and per targeted operator (any operator whose xonly pubkey is known — which is public information) and does not require compromising any key; it only needs possession of a `SinglePlusAnyoneCanPay` signature for the relevant withdrawal UTXO, which the withdrawing party (who can be the attacker itself, per the threat model's allowance to `deposit into the bridge` and `call withdraw`) inherently possesses for its own withdrawal.

### Likelihood Explanation
The attack requires no privileged access: an attacker acting as the withdrawing user creates their own withdrawal registration and signs it themselves (this signature is their own, not secret to obtain), then instead of routing it through an operator's `withdraw`/`WithdrawParamsWithSig` RPC, directly assembles a payout transaction with a self-funded extra input and an OP_RETURN naming an arbitrary target operator's public xonly key, and broadcasts it to the Bitcoin network ahead of the honest operator. Cost is limited to Bitcoin transaction fees. This is fully reproducible offline against a regtest/mocked Citrea client, requires no mainnet, no live Citrea beyond the existing `MockCitreaClient` test harness, and no majority-hashrate or TLS-interception capability.

### Recommendation
Bind payout attribution cryptographically to the crediting operator rather than to unauthenticated OP_RETURN bytes — e.g., require the OP_RETURN output (or an additional signature) to be committed by a signature under the named operator's key, or change the payout signature scheme so that the operator's identity output is covered by a commitment the operator itself controls (not spendable/reusable by third parties holding only the user's `SinglePlusAnyoneCanPay` signature). Alternatively, require `update_finalized_payouts` to verify that the same operator whose key is in the OP_RETURN actually broadcast/funded the transaction (e.g., by requiring the operator's own signature over the full transaction, not solely the user's partially-committing signature).

### Proof of Concept
`cargo test` plan (extend `core/src/task/payout_checker.rs`/`core/src/test/deposit_and_withdraw_e2e.rs` style E2E harness with `MockCitreaClient`):
1. Set up a deposit and register a Citrea withdrawal for user-controlled dust UTXO `withdrawal_utxo`, obtaining the user's `SinglePlusAnyoneCanPay` `sig` over `(withdrawal_utxo, payout_txout)`, per `generate_withdrawal_transaction_and_signature` in `core/src/test/common/setup_utils.rs`.
2. Instead of calling any operator's `withdraw`/`WithdrawParamsWithSig` RPC, directly build a payout transaction using `create_payout_txhandler` (or manually) with: input 0 = `withdrawal_utxo` + `sig`; output 0 = the exact committed `payout_txout`; an extra attacker-funded input to cover fees; an OP_RETURN output containing `operator0.signer.xonly_public_key` (the *honest* operator, distinct from the attacker).
3. Broadcast this transaction directly via `rpc.send_raw_transaction`, mine to finality.
4. Assert (before): `db.get_handled_payout_kickoff_txid(None, payout_txid).await.unwrap().is_none()`.
5. Let `operator0`'s `PayoutCheckerTask` run; assert (after) that `db.get_handled_payout_kickoff_txid` becomes `Some(kickoff_txid)` for `operator0`, even though `operator0` never called `withdraw` or funded the transaction — demonstrating the attribution equality `operator_credited == party_who_funded` is violated: the funder (attacker) is not `operator0`, yet `operator0` is credited and its kickoff is committed.

### Citations

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

**File:** core/src/task/payout_checker.rs (L39-79)
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

**File:** core/src/operator.rs (L839-885)
```rust
    pub async fn handle_finalized_payout<'a>(
        &'a self,
        dbtx: DatabaseTransaction<'a>,
        deposit_outpoint: OutPoint,
        payout_tx_blockhash: BlockHash,
    ) -> Result<bitcoin::Txid, BridgeError> {
        let (deposit_id, deposit_data) = self
            .db
            .get_deposit_data(Some(dbtx), deposit_outpoint)
            .await?
            .ok_or(BridgeError::DatabaseError(sqlx::Error::RowNotFound))?;

        // get unused kickoff connector
        let (round_idx, kickoff_idx) = self
            .db
            .get_unused_and_signed_kickoff_connector(
                Some(dbtx),
                deposit_id,
                self.signer.xonly_public_key,
            )
            .await?
            .ok_or(BridgeError::DatabaseError(sqlx::Error::RowNotFound))?;

        let current_round_index = self.db.get_current_round_index(Some(dbtx)).await?;
        tracing::info!(
            "Operator: Current round index: {}, round idx for kickoff: {}",
            current_round_index,
            round_idx
        );
        #[cfg(feature = "automation")]
        if current_round_index != round_idx {
            // we currently have no free kickoff connectors in the current round, so we need to end round first
            // if current_round_index should only be smaller than round_idx, and should not be smaller by more than 1
            // so sanity check:
            if current_round_index.next_round() != round_idx {
                return Err(eyre::eyre!(
                    "Internal error: Expected the current round ({:?}) to be equal to or 1 less than the round of the first available kickoff for deposit reimbursement ({:?}) for deposit {:?}. If the round is less than the current round, there is an issue with the logic of the fn that gets the first available kickoff. If the round is greater, that means the next round do not have any kickoff connectors available for reimbursement, which should not be possible.",
                    current_round_index, round_idx, deposit_outpoint
                ).into());
            }
            tracing::info!(
                "Operator: Starting next round to be able to get reimbursement for the payout"
            );
            // start the next round to be able to get reimbursement for the payout
            self.end_round(dbtx).await?;
        }

```
