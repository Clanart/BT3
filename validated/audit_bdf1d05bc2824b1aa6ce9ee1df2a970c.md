## Analysis Result

### Title
Unauthenticated Payout OP_RETURN Allows Misattribution of Operator Reimbursement Credit - ([File: core/src/builder/transaction/operator_reimburse.rs])

### Summary
`create_payout_txhandler` embeds an `operator_xonly_pk` into an unsigned, unauthenticated `OP_RETURN` output of the payout transaction [1](#0-0) . Whoever assembles/broadcasts the final payout transaction chooses this value freely — it is not covered by the user's `SinglePlusAnyoneCanPay` signature (which only commits to output index 0, the payout itself) [2](#0-1) , and it does not require any signature from the named operator proving they authorized or funded the transaction.

### Finding Description
The verifier's block-sync logic (`update_finalized_payouts`) determines "who fronted a withdrawal" purely by reading the `OP_RETURN` xonly-pubkey out of whichever transaction happens to spend the registered withdrawal UTXO: [3](#0-2) . This value is then persisted as `payout_payer_operator_xonly_pk` via `update_payout_txs_and_payer_operator_xonly_pk` [4](#0-3) .

That stored attribution then directly drives reimbursement: `get_first_unhandled_payout_by_operator_xonly_pk` is queried by each operator's own node, filtered only by `payout_payer_operator_xonly_pk = self_pk` [5](#0-4) , and `PayoutCheckerTask::run_once` automatically calls `handle_finalized_payout` / triggers the kickoff+reimbursement flow for that operator once it observes itself credited [6](#0-5) . The kickoff transaction's OP_RETURN (embedding move-txid + `operator_xonly_pk`) is later re-checked against the payout's `operator_xonly_pk` by `is_kickoff_malicious`, but only for *consistency between the kickoff and the DB-recorded payout attribution* [7](#0-6)  — it never verifies that the credited operator actually supplied/funded the payout output.

Because the `OP_RETURN` push data is neither signed by the named operator nor covered by the sighash the user signed, any unprivileged third party who obtains the user's SinglePlusAnyoneCanPay signature and withdrawal parameters (needed to complete the payout output regardless) can construct and broadcast a payout transaction that fronts the withdrawal, while writing a *different, legitimate, registered operator's* `xonly_pk` into the `OP_RETURN` output. This breaks the equality that should hold: `payout_payer_operator_xonly_pk (recorded)` == `party that actually funded/broadcast the payout`. The framed/impersonated operator's own `PayoutCheckerTask` will then autonomously initiate the kickoff/reimbursement flow and claim BTC from the move-to-vault UTXO for a payout it never made — "an operator reimbursed for a payout it never funded," one of the explicitly listed Critical impacts.

### Impact Explanation
If exploited, an attacker fronts the withdrawal amount from their own funds but attributes the credit to a targeted honest operator by writing that operator's public key into the unauthenticated OP_RETURN output. The targeted operator's automated `PayoutCheckerTask` picks this up as its own unhandled payout and proceeds to submit a kickoff transaction and later a `reimburse_tx`, claiming the bridge's move-to-vault BTC [8](#0-7)  for money it never spent. This forces the targeted operator into round/kickoff/BitVM commitments (locking collateral, assert transactions, watchtower challenge exposure) it never intended to make, and diverts vault reimbursement to a payout the operator did not actually perform, breaking the custody binding between "who paid" and "who is reimbursed."

### Likelihood Explanation
Exploitation requires only the withdrawal parameters and the user's `SinglePlusAnyoneCanPay` signature for a specific withdrawal — data that, by design, must already be available to any operator processing a `withdraw()` call, and which is needed by anyone completing the payout regardless of role. No verifier/operator/watchtower privileged key material or role is required to perform the substitution; the attacker simply needs enough BTC to fund the payout output and construct/broadcast the transaction with a chosen OP_RETURN payload.

### Recommendation
Bind the `OP_RETURN` operator attestation to a value that only the named operator can produce, e.g., require an operator-issued signature (over the move txid + payout details) that can be independently verified during `update_finalized_payouts` / `is_kickoff_malicious` before crediting `payout_payer_operator_xonly_pk`, or require the operator's key to co-sign the payout transaction itself so that the attribution is cryptographically tied to actual authorization rather than free-form push data chosen by whoever funds/broadcasts the transaction.

### Proof of Concept
1. Observe a pending Citrea withdrawal's `WithdrawParams` (`withdrawal_id`, `input_signature`, `input_outpoint`, `output_script_pubkey`, `output_amount`) and the user's `SinglePlusAnyoneCanPay` signature — necessary information for anyone to complete the payout.
2. Using `create_payout_txhandler`-equivalent construction, build a payout transaction spending `input_outpoint`, with output[0] exactly matching the signed `output_script_pubkey`/`output_amount` (satisfying the user's signature), funded by the attacker's own wallet, and an `OP_RETURN` output containing the `xonly_pk` of a targeted honest operator (not the attacker) [1](#0-0) .
3. Broadcast the transaction directly to the Bitcoin network (bypassing the operator's own `withdraw()` gRPC entirely).
4. Once confirmed, the verifier's `update_finalized_payouts` records the targeted operator's `xonly_pk` as `payout_payer_operator_xonly_pk` for this withdrawal [3](#0-2) .
5. The targeted operator's `PayoutCheckerTask` detects this as its own unhandled payout and proceeds to kickoff/reimburse, claiming vault BTC for a payout it never funded [6](#0-5) .

### Citations

**File:** core/src/builder/transaction/operator_reimburse.rs (L318-385)
```rust
/// Creates a [`TxHandler`] for the `reimburse_tx`.
///
/// This transaction is sent by the operator if no challenge was sent, or a challenge was sent but no disprove was sent, to reimburse the operator for their payout.
///
/// # Inputs
/// 1. MoveToVaultTx: Utxo containing the deposit
/// 2. KickoffTx: Reimburse connector utxo in the kickoff
/// 3. RoundTx: Reimburse connector utxo in the round (for the given kickoff index)
///
/// # Outputs
/// 1. Reimbursement output to the operator
/// 2. Anchor output for CPFP
///
/// # Arguments
/// * `move_txhandler` - The move-to-vault transaction handler for the deposit.
/// * `round_txhandler` - The round transaction handler for the round.
/// * `kickoff_txhandler` - The kickoff transaction handler for the kickoff.
/// * `kickoff_idx` - The kickoff index of the operator's kickoff.
/// * `paramset` - Protocol parameter set.
/// * `operator_reimbursement_address` - The address to reimburse the operator.
///
/// # Returns
/// A [`TxHandler`] for the reimburse transaction, or a [`BridgeError`] if construction fails.
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

**File:** core/src/verifier.rs (L1857-1890)
```rust
    /// Checks if the operator who sent the kickoff matches the payout data saved in our db
    /// Payout data in db is updated during citrea sync.
    async fn is_kickoff_malicious(
        &self,
        kickoff_witness: Witness,
        deposit_data: &mut DepositData,
        kickoff_data: KickoffData,
        dbtx: DatabaseTransaction<'_>,
    ) -> Result<bool, BridgeError> {
        let move_txid =
            create_move_to_vault_txhandler(deposit_data, self.config.protocol_paramset())?
                .get_cached_tx()
                .compute_txid();

        let payout_info = self
            .db
            .get_payout_info_from_move_txid(Some(dbtx), move_txid)
            .await?;
        let Some((operator_xonly_pk_opt, payout_blockhash, _, _)) = payout_info else {
            tracing::warn!(
                "No payout info found in db for move txid {move_txid}, assuming malicious"
            );
            return Ok(true);
        };

        let Some(operator_xonly_pk) = operator_xonly_pk_opt else {
            tracing::warn!("No operator xonly pk found in payout tx OP_RETURN, assuming malicious");
            return Ok(true);
        };

        if operator_xonly_pk != kickoff_data.operator_xonly_pk {
            tracing::warn!("Operator xonly pk for the payout does not match with the kickoff_data");
            return Ok(true);
        }
```

**File:** core/src/verifier.rs (L2298-2343)
```rust
        let mut payout_txs_and_payer_operator_idx = vec![];
        for (idx, payout_txid) in payout_txids {
            let payout_tx_idx = block_cache.txids.get(&payout_txid);
            if payout_tx_idx.is_none() {
                tracing::error!(
                    "Payout tx not found in block cache: {:?} and in block: {:?}",
                    payout_txid,
                    block_id
                );
                tracing::error!("Block cache: {:?}", block_cache);
                return Err(eyre::eyre!("Payout tx not found in block cache").into());
            }
            let payout_tx_idx = payout_tx_idx.expect("Payout tx not found in block cache");
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

            payout_txs_and_payer_operator_idx.push((
                idx,
                payout_txid,
                operator_xonly_pk,
                block_hash,
            ));
        }
```

**File:** core/src/database/verifier.rs (L198-251)
```rust
    /// Sets the given payout txs' txid and operator index for the given index.
    pub async fn update_payout_txs_and_payer_operator_xonly_pk(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        payout_txs_and_payer_operator_xonly_pk: Vec<(
            u32,
            Txid,
            Option<XOnlyPublicKey>,
            bitcoin::BlockHash,
        )>,
    ) -> Result<(), BridgeError> {
        if payout_txs_and_payer_operator_xonly_pk.is_empty() {
            return Ok(());
        }
        // Convert all values first, propagating any errors
        let converted_values: Result<Vec<_>, BridgeError> = payout_txs_and_payer_operator_xonly_pk
            .iter()
            .map(|(idx, txid, operator_xonly_pk, block_hash)| {
                Ok((
                    i32::try_from(*idx).wrap_err("Failed to convert payout index to i32")?,
                    TxidDB(*txid),
                    operator_xonly_pk.map(XOnlyPublicKeyDB),
                    BlockHashDB(*block_hash),
                ))
            })
            .collect();
        let converted_values = converted_values?;

        let mut query_builder = QueryBuilder::new(
            "UPDATE withdrawals AS w SET
                payout_txid = c.payout_txid,
                payout_payer_operator_xonly_pk = c.payout_payer_operator_xonly_pk,
                payout_tx_blockhash = c.payout_tx_blockhash
                FROM (",
        );

        query_builder.push_values(
            converted_values.into_iter(),
            |mut b, (idx, txid, operator_xonly_pk, block_hash)| {
                b.push_bind(idx)
                    .push_bind(txid)
                    .push_bind(operator_xonly_pk)
                    .push_bind(block_hash);
            },
        );

        query_builder
            .push(") AS c(idx, payout_txid, payout_payer_operator_xonly_pk, payout_tx_blockhash) WHERE w.idx = c.idx");

        let query = query_builder.build();
        execute_query_with_tx!(self.connection, tx, query, execute)?;

        Ok(())
    }
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

**File:** core/src/task/payout_checker.rs (L39-106)
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
```
