Based on my research, I found a concrete analog of the reported bug class (a credited/attributed party diverging from the party that actually funded a payout) in Clementine's payout attribution logic.

### Title
Unauthenticated operator attribution in payout OP_RETURN allows reimbursement credit to be stolen or denied - (File: core/src/builder/transaction/operator_reimburse.rs, core/src/verifier.rs)

### Summary
The `payout_tx` created in `create_payout_txhandler` embeds the fronting operator's x-only public key in a plain OP_RETURN output that carries no signature or cryptographic proof of authorship [1](#0-0) . The only cryptographic commitment on the transaction is the user's `SinglePlusAnyoneCanPay` signature, which — as shown in the test helper that constructs an equivalent payout structure — covers only input 0 and output 0, explicitly permitting any party to add or rewrite other inputs/outputs (including the OP_RETURN operator-identity output) [2](#0-1) . The verifier later reads this unauthenticated OP_RETURN field as the sole source of truth for "who paid," storing it as `payout_payer_operator_xonly_pk` [3](#0-2) , which then drives reimbursement eligibility via `get_first_unhandled_payout_by_operator_xonly_pk` and `handle_finalized_payout` [4](#0-3) [5](#0-4) .

### Finding Description
The binding that must hold is: `operator credited for reimbursement == operator that actually fronted the withdrawal BTC`. The system enforces this binding using data (the OP_RETURN xonly pubkey) that is not signed by, or otherwise cryptographically tied to, the named operator, and is not covered by the ANYONECANPAY signature that authorizes the transaction. Concretely:

- `create_payout_txhandler` builds the OP_RETURN output from a caller-supplied `operator_xonly_pk` argument with no verification that this key belongs to whoever is actually funding the transaction's outputs [1](#0-0) .
- The only signature on the tx is the user's presigned `SinglePlusAnyoneCanPay` signature over the withdrawal input, which by design lets anyone complete the transaction with their own funding inputs and arbitrary additional outputs [6](#0-5) .
- `update_finalized_payouts` trusts this OP_RETURN content directly as the payer identity, with an explicit acknowledgment that "the operator constructed the payout tx wrong" is an anticipated case, i.e., that this field is not authenticated [7](#0-6) .
- This unauthenticated field is later used to select which operator is credited/reimbursed for the fronted withdrawal [4](#0-3) , and to drive the kickoff/reimbursement flow for that operator [5](#0-4) .

Because the withdrawal request is broadcast to (potentially) multiple operators simultaneously via the aggregator's `Withdraw` RPC [8](#0-7) , and any of them (or an attacker who observes the broadcast payout transaction in the mempool before confirmation) can construct a competing version of the same payout transaction — reusing the same signed input/output pair but substituting the OP_RETURN pubkey — the attacker breaks the credited-vs-funded binding.

### Impact Explanation
This maps to the Critical impact bucket "an operator reimbursed for a payout it never funded" and/or "an honest operator permanently unable to be reimbursed": an attacker (or a malicious operator) can complete and confirm a payout transaction while attributing the OP_RETURN operator credit to a different operator xonly pubkey than the one that actually supplied the funding inputs. `PayoutCheckerTask`/`handle_finalized_payout` will then treat that named (but non-funding) operator as the payer and mark the payout handled for it, permanently consuming the round/kickoff slot for the wrong operator while the operator who actually paid to complete the withdrawal has no recorded claim to reimbursement [9](#0-8) .

### Likelihood Explanation
The likelihood is nontrivial to fully confirm from the index alone: exploitation requires racing to be the party that completes/broadcasts the transaction with a substituted OP_RETURN before the legitimate operator's version confirms, and requires the withdrawal input/output signature (over index 0 only) to remain valid when other outputs are altered. I was not able to fully verify from available files whether some additional out-of-band check (e.g., in `TxSender` funding logic or additional signature requirements not surfaced in the indexed snippets) ties the funding inputs to the OP_RETURN pubkey before broadcast. This uncertainty should be resolved by inspecting the full `TxSender` funding path and any operator-side self-consistency check before submission, which is outside what the index exposed to me.

### Recommendation
Bind the OP_RETURN operator-identity output to the transaction's economic authorization: either (a) require the named operator's own signature/commitment over the OP_RETURN output and the funding inputs (not just the withdrawal input), or (b) derive payer attribution deterministically from which public key's funding inputs were actually spent in the confirmed transaction (e.g., by requiring the operator's registered kickoff/collateral key to sign the funding inputs and using that key, not the free-form OP_RETURN bytes, as the attribution source in `update_finalized_payouts`).

### Proof of Concept
1. Aggregator's `Withdraw` RPC dispatches the same withdrawal request to multiple operators [8](#0-7) .
2. Operator A calls `create_payout_txhandler` and broadcasts a payout tx embedding its own xonly pubkey in OP_RETURN, funding the large output itself [1](#0-0) .
3. Before A's transaction confirms, an attacker (or colluding Operator B) observes the mempool transaction, and — reusing the same signed withdrawal input/output (valid under `SinglePlusAnyoneCanPay`) — rebroadcasts a modified version with Operator B's xonly pubkey in OP_RETURN and B's own funding inputs replacing A's, with a higher fee to win the race.
4. `update_finalized_payouts` parses the confirmed tx's OP_RETURN and records `payout_payer_operator_xonly_pk = B` [3](#0-2) .
5. `PayoutCheckerTask`/`handle_finalized_payout` credits B for the reimbursement kickoff flow, while A (or the true funder) has no path to reimbursement for BTC it already spent [9](#0-8) , [5](#0-4) .

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

**File:** core/src/test/common/setup_utils.rs (L499-543)
```rust
fn sign_withdrawal_output(
    config: &BridgeConfig,
    dust_utxo: &UTXO,
    withdrawal_address: &bitcoin::Address,
    withdrawal_amount: bitcoin::Amount,
) -> (bitcoin::TxOut, taproot::Signature) {
    let signer = Actor::new(config.secret_key, config.protocol_paramset().network);
    let txin = builder::transaction::input::SpendableTxIn::new(
        dust_utxo.outpoint,
        dust_utxo.txout.clone(),
        vec![],
        None,
    );
    let txout = bitcoin::TxOut {
        value: withdrawal_amount,
        script_pubkey: withdrawal_address.script_pubkey(),
    };
    let unspent_txout = builder::transaction::output::UnspentTxOut::from_partial(txout.clone());

    let tx = builder::transaction::TxHandlerBuilder::new(TransactionType::Payout)
        .with_version(NON_STANDARD_V3)
        .add_input(
            NormalSignatureKind::NotStored,
            txin,
            SpendPath::KeySpend,
            builder::transaction::DEFAULT_SEQUENCE,
        )
        .add_output(unspent_txout.clone())
        .finalize();

    let sighash = tx
        .calculate_sighash_txin(0, sighash::TapSighashType::SinglePlusAnyoneCanPay)
        .expect("Failed to calculate sighash");

    let sig = signer
        .sign_with_tweak_data(sighash, builder::sighash::TapTweakData::KeyPath(None), None)
        .expect("Failed to sign");

    let sig = taproot::Signature {
        signature: sig,
        sighash_type: sighash::TapSighashType::SinglePlusAnyoneCanPay,
    };

    (txout, sig)
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

**File:** core/src/task/payout_checker.rs (L31-111)
```rust
#[async_trait]
impl<C> Task for PayoutCheckerTask<C>
where
    C: CitreaClientT,
{
    type Output = bool;
    const VARIANT: TaskVariant = TaskVariant::PayoutChecker;

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

        // fetch and save the LCP for if we get challenged and need to provide proof of payout later
        let (_, payout_block_height) = self
            .operator
            .db
            .get_block_info_from_hash(Some(&mut dbtx), payout_tx_blockhash)
            .await?
            .ok_or_eyre("Couldn't find payout blockhash in bitcoin sync")?;

        let _ = self
            .operator
            .citrea_client
            .fetch_validate_and_store_lcp(
                payout_block_height as u64,
                citrea_idx,
                &self.operator.db,
                Some(&mut dbtx),
                self.operator.config.protocol_paramset(),
            )
            .await?;

        #[cfg(feature = "automation")]
        self.operator.end_round(&mut dbtx).await?;

        self.db
            .mark_payout_handled(Some(&mut dbtx), citrea_idx, kickoff_txid)
            .await?;

        dbtx.commit().await?;

        Ok(true)
    }
```
