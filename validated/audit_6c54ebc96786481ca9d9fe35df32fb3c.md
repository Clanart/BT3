### Title
Operator credited for a payout via unauthenticated OP_RETURN parsing, letting a self-funding withdrawer trigger a full Reimburse payout to an uninvolved operator - (core/src/verifier.rs)

### Summary
`Verifier::update_finalized_payouts` attributes a payout to whichever x-only pubkey appears in the payout transaction's OP_RETURN output, with no verification that the named operator actually signed, funded, or participated in constructing that transaction. Since `payout_tx`'s single input is spent purely with the withdrawer's own key-path signature over their own withdrawal UTXO, any withdrawer can construct and broadcast the entire payout_tx themselves, self-fund output 0, and write an arbitrary honest operator's xonly_pk into the OP_RETURN, causing that operator's automation to claim a full `Reimburse` payout for a withdrawal it never fronted.

### Finding Description
The broken binding: `payout_payer_operator_xonly_pk` (the operator credited in the DB and later reimbursed) should equal the identity of the party whose Bitcoin funded `payout_tx` output 0. In the code, this binding is never enforced.

`create_payout_txhandler` builds `payout_tx` with exactly one input — the withdrawal UTXO — spent via `SpendPath::KeySpend` using only the withdrawer's own signature (`user_sig`), and an OP_RETURN output containing an arbitrary `operator_xonly_pk` chosen by whoever builds the transaction: [1](#0-0) 

Nothing on Bitcoin's consensus layer, and nothing in `Verifier::update_finalized_payouts`, ties that OP_RETURN pubkey to the entity that actually signed/funded input 0. `update_finalized_payouts` simply parses whatever bytes are in the first OP_RETURN output and stores them as the "payer": [2](#0-1) 

The DB lookup that later drives operator automation (`get_first_unhandled_payout_by_operator_xonly_pk`) only requires that the stored `payout_payer_operator_xonly_pk` column matches the operator's own key — it performs no cross-check against the actual funding of output 0: [3](#0-2) 

`PayoutCheckerTask::run_once` picks this up purely from DB state and calls `Operator::handle_finalized_payout`, which fetches an unused kickoff connector, builds, signs (with the operator's own key), and — under automation — broadcasts a `Kickoff` transaction for the deposit: [4](#0-3) [5](#0-4) 

The only sanity check verifiers perform before honoring the kickoff, `Verifier::is_kickoff_malicious`, checks that the OP_RETURN pubkey equals the kickoff's operator and that the committed blockhash matches — it never checks who funded the payout: [6](#0-5) 

If the kickoff goes unchallenged, `create_reimburse_txhandler` pays the *entire* deposited bridge amount from the `MoveToVault` UTXO to the operator's own reimbursement address, regardless of what value (if any) the operator actually put into the payout: [7](#0-6) 

Exploit flow: the attacker (a legitimate withdrawer who deposited bridge_amount and later calls Citrea `withdraw()`) registers an outpoint they themselves control as `withdrawal_utxo`. They then build `payout_tx` off-protocol (bypassing the `Operator::withdraw` gRPC entirely, so none of that path's `is_profitable`/sighash-type checks apply), fund output 0 entirely from their own already-owned funds (so `input_amount == withdrawal_amount`, no operator fronting needed at all), sign input 0 themselves, put honest operator D's xonly_pk in OP_RETURN, and broadcast it to Bitcoin. `update_finalized_payouts` matches the spent `withdrawal_utxo_txid`/`vout` (registered on Citrea by the attacker) to this tx and stores D as payer. D's own `PayoutCheckerTask` then autonomously signs and broadcasts a `Kickoff` for a payout D never made, and — absent a challenge, which nothing here creates since the OP_RETURN/blockhash checks pass — D's `Reimburse` tx pays out the full deposit value from the `MoveToVault` UTXO to D.

Existing guards fail because: `Verifier::is_deposit_valid`/`is_profitable`/`SECP.verify_schnorr` are only invoked inside the `Operator::withdraw` and `sign_optimistic_payout` RPCs, which the attacker never has to call since they can broadcast the transaction directly to Bitcoin; `is_kickoff_malicious` checks only the OP_RETURN-to-kickoff pubkey match and blockhash commitment, both of which the attacker fully controls and satisfies trivially.

### Impact Explanation
BTC leaves the `MoveToVault` UTXO (the deposit's bridge_amount) as a `Reimburse` payment to an operator (D) that never fronted any funds for the corresponding withdrawal — matching the Critical category "an operator reimbursed for a payout it never funded" / "BTC leaving a move-to-vault UTXO without a matching fronted withdrawal." The attacker (withdrawer) recovers their own funds independent of any operator, while an uninvolved operator's automation is forced to consume one of its round's kickoff connectors and eventually receives a windfall paid from the vault that was never economically justified — effectively draining bridge collateral. This is repeatable per deposit/withdrawal registered by any attacker-controlled account, against any operator whose xonly_pk the attacker chooses to name.

### Likelihood Explanation
Preconditions are minimal and fully within the "unprivileged attacker" capability set: deposit into the bridge once (standard user flow), call `withdraw()` on the Citrea Bridge contract naming an outpoint the attacker already owns with sufficient value, sign that outpoint themselves, and broadcast a self-constructed `payout_tx` with an arbitrary operator's xonly_pk in the OP_RETURN. No operator, verifier, or aggregator cooperation is required. Cost is just Bitcoin transaction fees. This is deterministically repeatable for every withdrawal the attacker registers, against any operator.

### Recommendation
Do not attribute a payout to an operator based solely on OP_RETURN content. Require cryptographic proof that the credited operator actually contributed the fronted funds — e.g., require an operator signature over the payout transaction (or over a commitment naming itself) as part of the transaction's witness/signature set, and have `Verifier::update_finalized_payouts` / `is_kickoff_malicious` verify that signature rather than trusting an unauthenticated OP_RETURN field.

### Proof of Concept
`cargo test` plan (integration test under `core/src/test/`, e.g. extending `deposit_and_withdraw_e2e.rs` harness):
1. Run a single deposit for depositor/attacker A (`run_single_deposit`), and register a withdrawal on the (mock) Citrea client naming an outpoint fully owned by A with value equal to the withdrawal amount.
2. Instead of calling `operator.withdraw(...)`/`operator.optimistic_payout(...)`, directly build `create_payout_txhandler` with: `input_utxo` = A's own owned UTXO (sufficient value), `output_txout` paid to A's own address, `operator_xonly_pk` = honest operator D's xonly_pk (obtained from `deposit_info`/`actors`), and `user_sig` signed by A's own key over input 0 (`SinglePlusAnyoneCanPay` or any sighash A chooses since A owns the key).
3. Broadcast this `payout_tx` directly via the Bitcoin RPC (bypassing `Operator::withdraw`/`Aggregator::optimistic_payout` entirely), mine to finality.
4. Assert, on D's DB: `get_first_unhandled_payout_by_operator_xonly_pk(D)` returns `Some(...)` for this withdrawal — confirming `payout_payer_operator_xonly_pk == D` even though D signed nothing and fronted nothing (binding: credited operator == D; actual funder == A; assert these are unequal and yet D is still credited).
5. Let D's `PayoutCheckerTask::run_once()` run; assert it calls `handle_finalized_payout` successfully and produces/broadcasts a `Kickoff` txid for D, and that this kickoff eventually is not flagged by `is_kickoff_malicious` (op_return pubkey and blockhash match by construction), demonstrating D is committed to and will receive a `Reimburse` payment it never funded.

### Citations

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

**File:** core/src/verifier.rs (L1857-1914)
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

        let wt_derive_path = WinternitzDerivationPath::Kickoff(
            kickoff_data.round_idx,
            kickoff_data.kickoff_idx,
            self.config.protocol_paramset(),
        );
        let commits = extract_winternitz_commits(
            kickoff_witness,
            &[wt_derive_path],
            self.config.protocol_paramset(),
        )?;
        let blockhash_data = commits.first();
        // only last 20 bytes of the blockhash is committed
        let truncated_blockhash = &payout_blockhash[12..];
        if let Some(committed_blockhash) = blockhash_data {
            if committed_blockhash != truncated_blockhash {
                tracing::warn!("Payout blockhash does not match committed hash: committed: {:?}, truncated payout blockhash: {:?}",
                        blockhash_data, truncated_blockhash);
                return Ok(true);
            }
        } else {
            return Err(eyre::eyre!("Couldn't retrieve committed data from witness").into());
        }
        Ok(false)
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

**File:** core/src/operator.rs (L839-916)
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

        // get signed txs,
        let kickoff_data = KickoffData {
            operator_xonly_pk: self.signer.xonly_public_key,
            round_idx,
            kickoff_idx,
        };

        let payout_tx_blockhash = payout_tx_blockhash.as_byte_array().last_20_bytes();

        #[cfg(test)]
        let payout_tx_blockhash = self
            .config
            .test_params
            .maybe_disrupt_payout_tx_block_hash_commit(payout_tx_blockhash);

        let context = ContractContext::new_context_for_kickoff(
            kickoff_data,
            deposit_data,
            self.config.protocol_paramset(),
        );

        let signed_txs = create_and_sign_txs(
            self.db.clone(),
            &self.signer,
            self.config.clone(),
            context,
            Some(payout_tx_blockhash),
            Some(dbtx),
        )
        .await?;

```
