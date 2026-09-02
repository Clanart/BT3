### Title
Unauthenticated OP_RETURN operator attribution lets an attacker credit reimbursement to an operator who never funded the payout - (File: core/src/verifier.rs, core/src/database/verifier.rs, core/src/task/payout_checker.rs)

### Summary
`Database::get_payout_txs_for_withdrawal_utxos` joins purely on outpoint identity (`bsu.txid = w.withdrawal_utxo_txid AND bsu.vout = w.withdrawal_utxo_vout`), so whichever transaction actually confirms as the spend of the withdrawal UTXO is accepted as "the" payout tx, with no check that it originated from the operator's `withdraw` flow. `Verifier::update_finalized_payouts` then reads that on-chain transaction's OP_RETURN output and blindly stores whatever x-only pubkey it finds as `payout_payer_operator_xonly_pk`, even though the OP_RETURN byte string is completely unauthenticated data that anyone spending the UTXO can set.

### Finding Description
Binding claimed: `payout_txid_recorded_for_withdrawal_idx == txid_of_the_transaction_that_actually_fronted_the_withdrawer's_funds`, and by extension `payout_payer_operator_xonly_pk == xonly_pk_of_the_party_whose_funds_actually_paid_the_withdrawer`.

The withdrawal UTXO is signed by the withdrawer with `TapSighashType::SinglePlusAnyoneCanPay` [1](#0-0) . This sighash flag only commits the withdrawer's input and the output at the same index (index 0, the user's payout output); it commits to nothing else in the transaction — not the fee-paying inputs, not any other output, and critically not the OP_RETURN output that `create_payout_txhandler` places at output index 2 to record the fronting operator's x-only pubkey [2](#0-1) .

Because the withdrawer created that signature themselves, they already possess it and can construct their own alternative transaction spending the same `withdrawal_utxo_txid:vout`, keeping the committed output 0 intact (so Citrea still sees the withdrawal as fulfilled), but freely choosing: their own fee-paying input(s), any other outputs, and — the key point — any OP_RETURN payload, including the real x-only pubkey of any legitimate operator who never touched this withdrawal. This matches the attacker capability explicitly listed in the rules: "choose the bytes of a withdrawal UTXO, a Schnorr signature and its sighash flag, an OP_RETURN...".

When this attacker-crafted transaction confirms (whether by winning a fee race against the operator's genuine payout tx, or simply because the operator hadn't even acted yet), the bitcoin syncer records it as the spending tx of the withdrawal UTXO. `get_payout_txs_for_withdrawal_utxos` returns it purely by outpoint match, with no signature, ownership, or provenance check [3](#0-2) . `update_finalized_payouts` then parses whatever OP_RETURN happens to be present and stores it verbatim as `payout_payer_operator_xonly_pk` [4](#0-3) .

Downstream, `PayoutCheckerTask` for the targeted operator polls `get_first_unhandled_payout_by_operator_xonly_pk` filtered only by that operator's own key [5](#0-4) , finds this fabricated payout attributed to them, and automatically calls `Operator::handle_finalized_payout`, which allocates a kickoff connector and drives the reimbursement flow to completion [6](#0-5) [7](#0-6) . The only cross-check performed by verifiers, `Verifier::is_kickoff_malicious`, merely confirms that the DB-recorded `payout_payer_operator_xonly_pk` equals the kickoff sender's key [8](#0-7)  — it never confirms that the recorded operator actually broadcast, funded, or signed the payout transaction. Since the DB value was itself forged by the attacker, this check passes and the kickoff is accepted as legitimate.

### Impact Explanation
This lets an unprivileged attacker (the withdrawer) forge which operator is credited with fronting a given withdrawal, entirely independent of who actually paid the withdrawer. Concretely: an operator can be made to enter the reimbursement pipeline (kickoff → assert/challenge window → reimburse from the deposit's move-to-vault UTXO) for a payout they never funded — "an operator reimbursed for a payout it never funded," a listed Critical impact. It is repeatable for every withdrawal for which the attacker holds the withdrawer's own `SinglePlusAnyoneCanPay` signature (i.e., every withdrawal, since the withdrawer always creates this signature themselves), and it can target any operator whose x-only pubkey is public (all registered operators). Separately, if the genuine operator's own payout transaction loses the race, that operator's real funding attempt is orphaned (their broadcast tx conflicts and never confirms), so no reimbursement claim ever gets attached to the txid that carried their actual money — an honest operator's legitimate spend becomes unattributable in the DB, permanently divorcing custody proof from the party that fronted funds.

### Likelihood Explanation
No privileged role is required. Preconditions: an unprivileged withdrawer performs a normal Citrea `withdraw` call (choosing the withdrawal UTXO and its dust value), obtains its own `SinglePlusAnyoneCanPay` signature (already required for any valid withdrawal), then races or preempts the operator by broadcasting their own transaction spending the same withdrawal UTXO with an attacker-chosen OP_RETURN before the operator's transaction confirms. Cost is a single dust-UTXO fee-bump, feasible for any Bitcoin fee-payer, and fully repeatable across withdrawals and operators. No verifier, aggregator, security-council, or Citrea-privileged role is needed.

### Recommendation
Do not treat OP_RETURN payload data in an arbitrary confirmed spend of the withdrawal UTXO as authoritative proof of who fronted the payout. Require cryptographic linkage between the recorded `payout_payer_operator_xonly_pk` and the actual payout transaction — e.g., require the payout transaction's additional (fee-funding) input(s) to be provably controlled/signed by the claimed operator (verifiable on-chain), or have the operator co-sign a commitment (outside the withdrawer's sighash) that binds their identity to the exact txid, and have `update_finalized_payouts`/`is_kickoff_malicious` verify that commitment rather than trusting unauthenticated OP_RETURN bytes.

### Proof of Concept
```
cargo test forged_payout_operator_attribution -- --nocapture
```
Plan:
1. Set up a deposit and a withdrawal as in `core/src/test/deposit_and_withdraw_e2e.rs`, obtaining the withdrawer's `SinglePlusAnyoneCanPay` signature over `withdrawal_utxo`.
2. Do NOT call operator `withdraw`; instead, as the withdrawer, construct a competing transaction spending the same `withdrawal_utxo` with (a) the same committed output 0, (b) an attacker-owned fee input, and (c) an OP_RETURN containing operator B's real x-only pubkey (operator B never involved). Broadcast and mine it to finality.
3. Run the verifier's finalized-block sync so `update_finalized_payouts` processes the block; assert `Database::get_payout_txs_for_withdrawal_utxos` returns the attacker's txid, and `Database::get_payout_info_from_move_txid` returns `payout_payer_operator_xonly_pk == Some(operator_B_xonly_pk)`.
4. Assert operator B's `PayoutCheckerTask` (or `get_first_unhandled_payout_by_operator_xonly_pk`) picks up this payout and drives `handle_finalized_payout`/kickoff, proving `payout_payer_operator_xonly_pk` (operator B) is inconsistent with the actual party whose funds moved to the withdrawer (the attacker), confirming the binding is broken.

### Citations

**File:** core/src/rpc/parser/operator.rs (L170-187)
```rust
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

**File:** core/src/database/verifier.rs (L170-196)
```rust
    pub async fn get_payout_txs_for_withdrawal_utxos(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        block_id: u32,
    ) -> Result<Vec<(u32, Txid)>, BridgeError> {
        let query = sqlx::query_as::<_, (i32, TxidDB)>(
            "SELECT w.idx, bsu.spending_txid
             FROM withdrawals w
             JOIN bitcoin_syncer_spent_utxos bsu
                ON bsu.txid = w.withdrawal_utxo_txid
                AND bsu.vout = w.withdrawal_utxo_vout
             WHERE bsu.block_id = $1",
        )
        .bind(i32::try_from(block_id).wrap_err("Failed to convert block id to i32")?);

        let results = execute_query_with_tx!(self.connection, tx, query, fetch_all)?;

        results
            .into_iter()
            .map(|(idx, txid)| {
                Ok((
                    u32::try_from(idx).wrap_err("Failed to convert withdrawal index to u32")?,
                    txid.0,
                ))
            })
            .collect()
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

**File:** core/src/verifier.rs (L2311-2350)
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
