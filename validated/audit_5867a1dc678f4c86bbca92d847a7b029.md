Confirmed: `handle_finalized_payout` (core/src/operator.rs:839-970) contains no check that the operator itself actually broadcast/funded the payout transaction — it only consumes `deposit_outpoint`/`payout_tx_blockhash` supplied by the caller (`PayoutCheckerTask`, which itself only reads the tainted OP_RETURN-derived DB column) and unconditionally signs and queues `Kickoff`/`Reimburse`/etc. This confirms there is no cross-check anywhere in the reachable path between the OP_RETURN-claimed `operator_xonly_pk` and any actual signature/fund source.

### Title
Unauthenticated OP_RETURN operator attribution in payout tx lets an attacker force an arbitrary honest operator to be credited with, and automatically claim reimbursement for, a payout it never funded - ([File: core/src/verifier.rs])

### Summary
`update_finalized_payouts` (core/src/verifier.rs:2283) attributes a payout purely from the plaintext OP_RETURN bytes of the on-chain payout transaction (`core/src/builder/transaction/mod.rs:282`, `operator_reimburse.rs:407-436`), with no signature or funding proof binding those bytes to the named operator. Because the payout tx's only real authorization is the withdrawing user's own key-spend signature over the withdrawal UTXO, any unprivileged user can self-fund their own withdrawal payout and stamp an arbitrary honest operator's `xonly_pk` into the OP_RETURN, causing that operator's automated `PayoutCheckerTask` to believe it fronted the withdrawal and to autonomously drive the kickoff/reimburse process, ultimately draining `bridge_amount` BTC from the move-to-vault UTXO to that operator via `Reimburse` (`operator_reimburse.rs:341-385`) without it ever having funded anything.

### Finding Description
The broken binding: `withdrawals.payout_payer_operator_xonly_pk` (as attributed by `update_finalized_payouts`) should equal `operator_xonly_pk`, the party whose funds/signature actually paid the withdrawal via `create_payout_txhandler`'s key-spend input. In reality:

- `create_payout_txhandler` (`core/src/builder/transaction/operator_reimburse.rs:407-436`) spends `input_utxo` via `SpendPath::KeySpend` authorized solely by `user_sig` over the withdrawal UTXO's own key (the withdrawing user's key, `try_get_taproot_pk()`), and appends an OP_RETURN with an `operator_xonly_pk` value that is **not committed to by any signature at all** - it is just plain data anyone constructing the transaction can set to any 32 bytes.
- `update_finalized_payouts` (`core/src/verifier.rs:2283-2354`) scans the payout tx on-chain, extracts the OP_RETURN via `get_first_op_return_output`/`parse_op_return_data`, and stores whatever `XOnlyPublicKey` decodes there as `payout_payer_operator_xonly_pk` (`core/src/database/verifier.rs:198-251`) with **no check that this key relates to who actually signed or funded the spending input**.
- `PayoutCheckerTask::run_once` (`core/src/task/payout_checker.rs:31-113`) queries `get_first_unhandled_payout_by_operator_xonly_pk(self.operator.signer.xonly_public_key)` and, on a hit, unconditionally calls `handle_finalized_payout` (`core/src/operator.rs:839-970`), which signs and queues the `Kickoff`/`Reimburse` chain - again with no cross-check that this operator ever broadcast a corresponding payout.
- `is_kickoff_malicious` (`core/src/verifier.rs:1859-1915`), the only guard verifiers use to flag a fraudulent kickoff, only compares the DB-recorded `operator_xonly_pk` (itself derived from the same untrusted OP_RETURN) against `kickoff_data.operator_xonly_pk` (the honest operator's real key). Since the attacker deliberately sets the OP_RETURN to the honest operator's real key, these two values trivially match, so the check passes and verifiers never challenge.
- `create_reimburse_txhandler` (`operator_reimburse.rs:341-385`) spends the `MoveToVaultTx` `DepositInMove` output (the real vault-held `bridge_amount`) using a signature verifiers pre-signed generically at deposit time for every operator/kickoff slot (`create_operator_sighash_stream`, `sighash.rs:308-376`), so the presigned N-of-N graph alone does not encode who actually paid - it only prevents unauthorized spends of the *specific* UTXO, not misattribution of *which* payout justified the spend.

Exploit flow: attacker deposits `bridge_amount` BTC (creating a real vault UTXO), calls Citrea's `withdraw`, then broadcasts their own payout transaction spending the registered withdrawal UTXO with their own valid signature (self-payment, no operator involvement), setting the OP_RETURN to an honest operator's `xonly_pk`. The honest operator's `PayoutCheckerTask` picks this up, drives `handle_finalized_payout`, and - since `is_kickoff_malicious` cannot detect the forgery - the kickoff proceeds unchallenged to `Reimburse`, paying `bridge_amount` from the vault to the (unwitting) operator. Net effect: the vault loses `bridge_amount` with no corresponding fronted payout by that operator - BTC leaves the move-to-vault UTXO without a matching fronted withdrawal.

### Impact Explanation
`bridge_amount` BTC leaves the move-to-vault UTXO (`core/src/builder/transaction/mod.rs:294-343`) via the `Reimburse` transaction credited to an operator who never funded the corresponding payout - directly matching the Critical category "an operator reimbursed for a payout it never funded" / "BTC leaving a move-to-vault UTXO without a matching fronted withdrawal." Any registered operator can be targeted by any attacker who merely deposits and withdraws normally; the attack is repeatable across every deposit/operator pair and does not require compromising any key, verifier, or operator.

### Likelihood Explanation
Preconditions are minimal: the attacker only needs to (1) deposit `bridge_amount` BTC to obtain L2 credit, (2) call `withdraw` on the Citrea bridge contract to register a withdrawal UTXO/output of their choosing, and (3) broadcast a self-signed payout transaction with a crafted OP_RETURN. No verifier, operator, or aggregator privilege is needed, and the attacker's net cost is only Bitcoin transaction fees (their deposit funds are effectively returned to them via the self-payment). This is fully repeatable against every operator and every deposit.

### Recommendation
Do not attribute payer identity from unauthenticated OP_RETURN bytes. Either (a) require the payout transaction's spending signature/witness to itself be verifiably tied to the claimed operator (e.g., require the input be an operator-controlled UTXO, or require an operator signature over the OP_RETURN payload), or (b) have `handle_finalized_payout`/`PayoutCheckerTask` cross-check against a local, operator-signed record that this specific operator itself broadcast a payout for that `citrea_idx` before initiating the kickoff/reimburse flow, and have `is_kickoff_malicious` independently verify authorship rather than re-deriving the comparison value from the same untrusted OP_RETURN source.

### Proof of Concept
```rust
// core/src/test/... (illustrative, not to be placed in out-of-scope test dirs for the fix, but for triage)
// 1. Perform a normal deposit for operator0 and verifier set, get move_txid, withdrawal_utxo via Citrea withdraw flow.
// 2. Instead of calling operator0.withdraw(...), construct create_payout_txhandler manually with:
//    - input_utxo = the registered withdrawal_utxo (owned by attacker's own key)
//    - operator_xonly_pk = HONEST_OPERATOR_XONLY_PK (some other operator's real key, e.g. operator1)
//    - user_sig = attacker's own valid signature over the tx sighash
// 3. Broadcast this tx directly via rpc.send_raw_transaction, mine blocks past finality.
// 4. Run the verifier's block sync (update_finalized_payouts) and assert:
//    let (payer_pk, _, payout_txid, idx) = db
//        .get_payout_info_from_move_txid(None, move_txid).await.unwrap().unwrap();
//    assert_eq!(payer_pk, Some(operator1_xonly_pk)); // attribution binding broken:
//    // operator1 never signed this tx (only attacker's key spent the input) and never
//    // broadcast/funded any payout, yet is recorded as payer.
// 5. Run operator1's PayoutCheckerTask::run_once and observe it calls handle_finalized_payout,
//    queuing Kickoff/Reimburse for operator1 for a withdrawal operator1 never funded -
//    demonstrating the false credit / vault drain path.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** core/src/verifier.rs (L1857-1915)
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
    }
```

**File:** core/src/verifier.rs (L2283-2354)
```rust
    async fn update_finalized_payouts(
        &self,
        dbtx: DatabaseTransaction<'_>,
        block_id: u32,
        block_cache: &block_cache::BlockCache,
    ) -> Result<(), BridgeError> {
        let payout_txids = self
            .db
            .get_payout_txs_for_withdrawal_utxos(Some(dbtx), block_id)
            .await?;

        let block = &block_cache.block;

        let block_hash = block.block_hash();

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

        self.db
            .update_payout_txs_and_payer_operator_xonly_pk(
                Some(dbtx),
                payout_txs_and_payer_operator_idx,
            )
            .await?;

        Ok(())
    }

```

**File:** core/src/database/verifier.rs (L253-313)
```rust
    pub async fn get_payout_info_from_move_txid(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        move_to_vault_txid: Txid,
    ) -> Result<Option<(Option<XOnlyPublicKey>, BlockHash, Txid, i32)>, BridgeError> {
        let query = sqlx::query_as::<_, (Option<XOnlyPublicKeyDB>, BlockHashDB, TxidDB, i32)>(
            "SELECT w.payout_payer_operator_xonly_pk, w.payout_tx_blockhash, w.payout_txid, w.idx
             FROM withdrawals w
             WHERE w.move_to_vault_txid = $1
               AND w.payout_txid IS NOT NULL
               AND w.payout_tx_blockhash IS NOT NULL",
        )
        .bind(TxidDB(move_to_vault_txid));

        let result: Option<(Option<XOnlyPublicKeyDB>, BlockHashDB, TxidDB, i32)> =
            execute_query_with_tx!(self.connection, tx, query, fetch_optional)?;

        result
            .map(|(operator_xonly_pk, block_hash, txid, deposit_idx)| {
                Ok((
                    operator_xonly_pk.map(|pk| pk.0),
                    block_hash.0,
                    txid.0,
                    deposit_idx,
                ))
            })
            .transpose()
    }

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

**File:** core/src/operator.rs (L839-970)
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

        let tx_metadata = Some(TxMetadata {
            tx_type: TransactionType::Dummy, // will be replaced in add_tx_to_queue
            operator_xonly_pk: Some(self.signer.xonly_public_key),
            round_idx: Some(round_idx),
            kickoff_idx: Some(kickoff_idx),
            deposit_outpoint: Some(deposit_outpoint),
        });

        // try to send them
        for (tx_type, signed_tx) in &signed_txs {
            match *tx_type {
                TransactionType::Kickoff
                | TransactionType::OperatorChallengeAck(_)
                | TransactionType::WatchtowerChallengeTimeout(_)
                | TransactionType::ChallengeTimeout
                | TransactionType::DisproveTimeout
                | TransactionType::Reimburse => {
                    #[cfg(feature = "automation")]
                    self.tx_sender
                        .add_tx_to_queue(
                            dbtx,
                            *tx_type,
                            signed_tx,
                            &signed_txs,
                            tx_metadata,
                            self.config.protocol_paramset(),
                            None,
                        )
                        .await?;
                }
                _ => {}
            }
        }

        let kickoff_txid = signed_txs
            .iter()
            .find_map(|(tx_type, tx)| {
                if let TransactionType::Kickoff = tx_type {
                    Some(tx.compute_txid())
                } else {
                    None
                }
            })
            .ok_or(eyre::eyre!(
                "Couldn't find kickoff tx in signed_txs".to_string(),
            ))?;

        // mark the kickoff connector as used
        self.db
            .mark_kickoff_connector_as_used(Some(dbtx), round_idx, kickoff_idx, Some(kickoff_txid))
            .await?;

        Ok(kickoff_txid)
    }
```

**File:** core/src/builder/sighash.rs (L308-376)
```rust
pub fn create_operator_sighash_stream(
    db: Database,
    operator_xonly_pk: XOnlyPublicKey,
    config: BridgeConfig,
    deposit_data: DepositData,
    deposit_blockhash: bitcoin::BlockHash,
) -> impl Stream<Item = Result<(TapSighash, SignatureInfo), BridgeError>> {
    try_stream! {
        let mut tx_db_data = ReimburseDbCache::new_for_deposit(db.clone(), operator_xonly_pk, deposit_data.get_deposit_outpoint(), config.protocol_paramset(), None);

        let operator = db.get_operator(None, operator_xonly_pk).await?;

        let operator = match operator {
            Some(operator) => operator,
            None => Err(BridgeError::OperatorNotFound(operator_xonly_pk))?,
        };

        let utxo_idxs = get_kickoff_utxos_to_sign(
            config.protocol_paramset(),
            operator.xonly_pk,
            deposit_blockhash,
            deposit_data.get_deposit_outpoint(),
        );

        let paramset = config.protocol_paramset();
        let mut txhandler_cache = TxHandlerCache::new();
        let operator_idx = deposit_data.get_operator_index(operator_xonly_pk)?;

        // For each round_tx, we have multiple kickoff_utxos as the connectors.
        for round_idx in RoundIndex::iter_rounds(paramset.num_round_txs) {
            for &kickoff_idx in &utxo_idxs {
                let partial = PartialSignatureInfo::new(operator_idx, round_idx, kickoff_idx);

                let context = ContractContext::new_context_for_kickoff(
                    KickoffData {
                        operator_xonly_pk,
                        round_idx,
                        kickoff_idx: kickoff_idx as u32,
                    },
                    deposit_data.clone(),
                    config.protocol_paramset(),
                );

                let mut txhandlers = create_txhandlers(
                    TransactionType::AllNeededForDeposit,
                    context,
                    &mut txhandler_cache,
                    &mut tx_db_data,
                ).await?;

                let mut sum = 0;
                for (_, txhandler) in txhandlers.iter() {
                    let sighashes = txhandler.calculate_shared_txins_sighash(EntityType::OperatorDeposit, partial)?;
                    sum += sighashes.len();
                    for sighash in sighashes {
                        yield sighash;
                    }
                }
                if sum != config.get_num_required_operator_sigs_per_kickoff(&deposit_data) {
                    Err(eyre::eyre!("Operator sighash count does not match: expected {0}, got {1}", config.get_num_required_operator_sigs_per_kickoff(&deposit_data), sum))?;
                }
                // recollect round_tx, ready_to_reimburse_tx, and move_to_vault_tx for the next kickoff_utxo
                txhandler_cache.store_for_next_kickoff(&mut txhandlers)?;
            }
            // collect the last ready_to_reimburse txhandler for the next round
            txhandler_cache.store_for_next_round()?;
        }
    }
}
```
