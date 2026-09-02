### Title
Payout attribution (OP_RETURN operator pubkey) is not covered by the user's `SinglePlusAnyoneCanPay` signature, allowing anyone to hijack reimbursement credit for a withdrawal they did not fund - (File: `core/src/builder/transaction/operator_reimburse.rs`)

### Summary

### Finding Description
The `payout_tx` built by `create_payout_txhandler` has three outputs: the user's payout, an anchor, and an `OP_RETURN` output carrying the fronting operator's x-only pubkey [1](#0-0) . The single taproot key-spend input is authorized by the withdrawing user's signature, and that signature's sighash type is enforced to be `SinglePlusAnyoneCanPay` [2](#0-1) .

`SIGHASH_SINGLE | ANYONECANPAY` only commits the signer's input and the output at the *same index* as that input (index 0, the user payout output). Neither the anchor output nor the `OP_RETURN` output (index 2, carrying the operator attribution) is covered by the signature. Because of this, anyone who observes the signed input witness (the signature is explicitly described as given to operators "off-chain", and the aggregator broadcasts the identical `WithdrawParamsWithSig` to every matching operator via `Aggregator::withdraw` [3](#0-2) , and any in-flight payout tx is visible in the mempool once broadcast) can construct an alternative, equally valid `payout_tx` that reuses the same witness/input and the same output[0], but substitutes a different `OP_RETURN` pubkey, then get that alternative transaction confirmed instead (via a fee-bumped competing broadcast).

Downstream, the protocol's reimbursement-attribution logic trusts whatever `OP_RETURN` pubkey ends up in the *confirmed* payout tx as the "party that paid": `update_finalized_payouts` extracts the pubkey directly from the confirmed transaction's `OP_RETURN` and stores it as `payout_payer_operator_xonly_pk` [4](#0-3) , and `PayoutCheckerTask`/`get_first_unhandled_payout_by_operator_xonly_pk` later drives the reimbursement kickoff/round-tx flow for whichever operator's pubkey matches that stored value [5](#0-4) [6](#0-5) . `is_kickoff_malicious` also compares the kickoff's operator against this same DB-stored attribution field [7](#0-6) .

This breaks the binding: `operator credited via payout OP_RETURN == operator that actually funded/broadcast the confirmed payout`. Because the attribution field is unauthenticated by the only signature present on the transaction, an attacker can rewrite it before confirmation.

### Impact Explanation
Two concrete outcomes map to listed Critical impacts:
- An attacker points the `OP_RETURN` to a genuine operator's pubkey who did not broadcast/fund this payout: that operator is later credited/reimbursed via the round/kickoff/reimburse flow for a payout it never funded ("an operator reimbursed for a payout it never funded").
- An attacker points the `OP_RETURN` to an invalid/unknown pubkey (or the actual fronting operator's competing tx loses the race): the real fronting operator's `payout_payer_operator_xonly_pk` never matches their own key, so `get_first_unhandled_payout_by_operator_xonly_pk` never returns this payout to them, and they can never be reimbursed for BTC they actually paid out ("an honest operator permanently unable to be reimbursed").

### Likelihood Explanation
No privileged role is required — only visibility of a broadcast/pending `payout_tx` (mempool) or of the off-chain-distributed withdrawal signature, plus the ability to broadcast a competing transaction with a modified `OP_RETURN` output and win the confirmation race (e.g., via a higher fee). The withdrawal signature is by design shared with (potentially) multiple operators simultaneously (`Aggregator::withdraw` fans out to all operators), so multiple parties already legitimately possess the reusable, malleable witness, making exploitation straightforward.

### Recommendation
Bind the operator attribution to the signature. Options: have the operator additionally sign (or have the aggregator/verifiers co-sign) a commitment covering the `OP_RETURN` output, or change the payout transaction structure/sighash so that all outputs (including anchor and `OP_RETURN`) are covered by an `ALL`-type signature or a separate covenant, so the attribution cannot be altered without invalidating the required signature(s).

### Proof of Concept
1. Aggregator forwards a `WithdrawParamsWithSig` (with a valid `SinglePlusAnyoneCanPay` signature over `input_outpoint` → `output_txout`) to operators; Operator A broadcasts `payout_tx_A` with `OP_RETURN(operator_A_pubkey)`.
2. Before `payout_tx_A` confirms, an attacker builds `payout_tx_B` reusing the same input `OutPoint`/witness and identical output[0] (user payout), but with `OP_RETURN(operator_B_pubkey)` (or any other pubkey) — this remains a valid witness under `SinglePlusAnyoneCanPay` semantics since only input+output[0] are committed, per `create_payout_txhandler` and the enforced sighash type in `parse_withdrawal_sig_params` [8](#0-7) [9](#0-8) .
3. Attacker broadcasts `payout_tx_B` with a competitive fee; it confirms instead of `payout_tx_A`.
4. `update_finalized_payouts` records `operator_B_pubkey` as `payout_payer_operator_xonly_pk` for this withdrawal [10](#0-9) , so Operator B's reimbursement flow (`PayoutCheckerTask` / `get_reimbursement_txs`) later drives kickoff/reimburse for a payout it never funded, while Operator A — who actually paid the user — can never claim reimbursement for that withdrawal.

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

**File:** core/src/rpc/aggregator.rs (L1870-1886)
```rust
        let operators = self
            .get_operator_clients()
            .iter()
            .zip(current_operator_xonly_pks.into_iter());
        let withdraw_futures = operators
            .filter(|(_, xonly_pk)| {
                // check if operator_xonly_pks is empty or contains the operator's xonly public key
                operator_xonly_pks_from_rpc.is_empty()
                    || operator_xonly_pks_from_rpc.contains(xonly_pk)
            })
            .map(|(operator, operator_xonly_pk)| {
                let mut operator = operator.clone();
                let params = withdraw_params_with_sig.clone();
                let mut request = Request::new(params);
                request.set_timeout(WITHDRAWAL_TIMEOUT);
                async move { (operator.withdraw(request).await, operator_xonly_pk) }
            });
```

**File:** core/src/verifier.rs (L1882-1890)
```rust
        let Some(operator_xonly_pk) = operator_xonly_pk_opt else {
            tracing::warn!("No operator xonly pk found in payout tx OP_RETURN, assuming malicious");
            return Ok(true);
        };

        if operator_xonly_pk != kickoff_data.operator_xonly_pk {
            tracing::warn!("Operator xonly pk for the payout does not match with the kickoff_data");
            return Ok(true);
        }
```

**File:** core/src/verifier.rs (L2312-2350)
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
        }

        self.db
            .update_payout_txs_and_payer_operator_xonly_pk(
                Some(dbtx),
                payout_txs_and_payer_operator_idx,
            )
            .await?;
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
