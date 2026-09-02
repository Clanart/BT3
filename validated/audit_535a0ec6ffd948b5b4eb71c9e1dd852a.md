This confirms the finding. The bridge circuit only checks: (1) the withdrawal outpoint/vout matches storage proof, (2) OP_RETURN parses to a valid xonly pubkey, and (3) `deposit_constant` self-consistency (round_txid/kickoff_vout/watchtower pubkeys belong to whichever operator is claiming). It never checks that the *output paying the user* has the amount/script committed in Citrea, nor that the operator whose key appears in the OP_RETURN is the one who actually broadcast/funded the spending transaction. Attribution is purely "whatever 32 bytes appear in the first OP_RETURN of whoever wins the race to spend the withdrawal UTXO."

### Title
Payout attribution forgeable via attacker-controlled OP_RETURN, letting an honest operator be credited/reimbursed for a withdrawal it never funded - ([File: core/src/verifier.rs])

### Summary
The withdrawal UTXO used to fulfill a Citrea withdrawal is an ordinary P2TR output whose private key is held by the withdrawer (the attacker) themselves, not by any operator or by an aggregator-controlled script. `Verifier::update_finalized_payouts` blindly trusts whichever transaction ends up spending that UTXO and reads the operator attribution from its OP_RETURN via `parse_op_return_data`/`XOnlyPublicKey::from_slice`, with no proof that the named operator actually produced or funded that spend. Because the withdrawer controls the UTXO, they can craft their own "payout" transaction naming any honest operator's public xonly key in the OP_RETURN, causing that operator's own `PayoutCheckerTask::run_once` to pick it up as "its" unhandled payout and call `Operator::handle_finalized_payout`, kicking off the reimbursement flow for a withdrawal it never fronted.

### Finding Description
The binding that should hold is: `payout_payer_operator_xonly_pk` for withdrawal *i* (the value written by `Verifier::update_finalized_payouts`) **==** the xonly public key of the operator whose own wallet/funds actually paid the withdrawal output to the user.

Trace:
1. A withdrawal UTXO is a plain user-owned P2TR dust output (`core/src/test/common/setup_utils.rs:480-497` shows the pattern: an ordinary address, not any bridge-controlled script). The withdrawer/attacker holds this key and can sign arbitrary spends of it at will, with any sighash flag and any output structure — Bitcoin enforces nothing about "who is allowed to pay this out."
2. `Verifier::update_citrea_deposit_and_withdrawals` (`core/src/verifier.rs:2204-2262`) and `get_payout_txs_for_withdrawal_utxos` (`core/src/database/verifier.rs:168-196`) simply record whichever transaction is observed on-chain spending `withdrawal_utxo_txid/vout` as "the" payout tx — there is no check that this spend was produced by any operator, nor that its output amount/script matches the Citrea-committed `output_script_pubkey`/`output_amount`.
3. `Verifier::update_finalized_payouts` (`core/src/verifier.rs:2283-2352`) then does: [1](#0-0) 
   parsing the first OP_RETURN's 32 bytes as an xonly public key and writing it straight into `payout_payer_operator_xonly_pk` with `update_payout_txs_and_payer_operator_xonly_pk`.
4. The framed, honest operator's own `PayoutCheckerTask::run_once` (`core/src/task/payout_checker.rs:39-111`) polls `get_first_unhandled_payout_by_operator_xonly_pk` filtered by *its own* `signer.xonly_public_key` [2](#0-1) , finds the attacker-poisoned row, and automatically calls `Operator::handle_finalized_payout` (`core/src/operator.rs:839-885`) with `deposit_data.get_deposit_outpoint()` and the attacker-chosen `payout_tx_blockhash` — exactly the path named in the question ("block_hash param sourced from PayoutCheckerTask::run_once's unhandled_payout.2").
5. `handle_finalized_payout` commits this blockhash into the operator's own Kickoff (WOTS commitment), starts the round/kickoff/reimbursement sequence, and marks the payout handled (`mark_payout_handled`).
6. Verifiers' only sanity check, `Verifier::is_kickoff_malicious` (`core/src/verifier.rs:1857-1915`), only re-derives self-consistency: it re-reads the *same* poisoned `payout_payer_operator_xonly_pk`/`payout_tx_blockhash` from the DB and checks that the kickoff's committed operator key and blockhash match what's stored there — trivially true, since both are attacker-controlled inputs that the framed operator faithfully committed. Nothing checks that the operator's own wallet produced the spend.
7. The eventual fraud-proof/disprove circuit (`circuits-lib/src/bridge_circuit/mod.rs:182-229`) only verifies: the SPV-proven payout tx really spends the correct withdrawal outpoint (from storage proof), and a `deposit_constant` self-consistency hash built from `round_txid`, `kickoff_round_vout`, watchtower pubkeys, and the OP_RETURN's `operator_xonlypk`. Since the framed operator's own kickoff genuinely has its own `round_txid`/`kickoff_round_vout`/watchtower pubkeys, and the attacker deliberately copied the framed operator's real, publicly known xonly pubkey into the OP_RETURN, this hash matches perfectly — the circuit has no mechanism to check that the operator's wallet, rather than an arbitrary third party, produced the spending transaction. Critically, neither this circuit nor `is_kickoff_malicious` verifies that the payout output's amount/script matches the Citrea-committed `output_amount`/`output_script_pubkey`, so the attacker's self-payment can be for an arbitrary/negligible amount while still carrying the honest operator's pubkey.

The attacker's exact transaction: input = the withdrawal UTXO they control (signed with their own key, any sighash flag), output 0 = payment to themselves (trivial/self-pay, no real value transfer to a third party required), output 1 = OP_RETURN containing exactly the 32-byte xonly public key of a targeted honest operator (format matches `create_payout_txhandler`, `core/src/builder/transaction/operator_reimburse.rs:387-436`, which pushes only `operator_xonly_pk.serialize()`). Broadcast this before (or instead of) any real operator's payout tx confirms.

### Impact Explanation
The framed operator, running unmodified standard automation, is driven to open a Reimburse path (Round → Kickoff → ReadyToReimburse → Reimburse) for a deposit it never paid out — matching Critical impact category "an operator reimbursed for a payout it never funded." The reimbursement claims BTC from the presigned collateral/round graph tied to that specific deposit's move-to-vault UTXO, without any corresponding real payout having been funded by that operator. This is repeatable per withdrawal and per operator: any withdrawer can frame any operator whose xonly public key is publicly discoverable (via `GetXOnlyPublicKey` RPC or on-chain data), for every withdrawal they themselves register, at negligible cost (one dust-UTXO self-spend transaction fee).

### Likelihood Explanation
Preconditions are minimal and match exactly the unprivileged attacker capabilities described: deposit into the bridge, call `withdraw` on the Citrea contract, own/control a Bitcoin UTXO, craft an OP_RETURN, and broadcast a transaction. No verifier/operator/aggregator collusion, no key compromise, and no majority hashrate are required — the attacker only needs to win the race to spend their own withdrawal UTXO before (or in place of) any operator, which they trivially can since they control that UTXO's key from the start and can broadcast immediately with a high fee. Cost is a single transaction fee; no bridge funds need to be risked by the attacker.

### Recommendation
Attribution of "which operator funded a withdrawal" must not rely solely on OP_RETURN bytes in an attacker-controllable UTXO spend. The protocol should cryptographically bind the payout to the claiming operator, e.g., by requiring the payout tx's committing signature/witness to be produced under a script path only the operator (or an operator-specific pre-signed construction) can satisfy, or by having verifiers/the disprove circuit additionally check that the payout output's amount and destination script exactly match the Citrea-committed `output_amount`/`output_script_pubkey`, and reject/ignore payout attribution when the spending transaction was not the operator's own known-funded transaction (e.g., cross-reference against a signature or funding source verifiable to originate from that operator's wallet).

### Proof of Concept
`cargo test` plan (new integration test near `core/src/database/verifier.rs` tests / `core/src/test/deposit_and_withdraw_e2e.rs`, no mainnet/live Citrea, using existing `create_test_config_with_thread_name`/regtest harness):
1. Create a deposit and register a withdrawal UTXO exactly as in `generate_withdrawal_transaction_and_signature` (`core/src/test/common/setup_utils.rs:439-497`), but do **not** let any operator's `withdraw` RPC construct the payout tx.
2. As the "attacker," build a transaction spending the same withdrawal UTXO with the attacker's own key, output 0 paying the attacker (self-pay, arbitrary value), output 1 = OP_RETURN of exactly `honest_operator_xonly_pk.serialize()` (a real, independently-running operator that never received/funded any payout for this withdrawal), and broadcast/mine it.
3. Run the bitcoin syncer / `update_citrea_deposit_and_withdrawals` + `update_finalized_payouts` path (or directly call `Verifier::update_finalized_payouts`) and then assert:
   - `db.get_payout_info_from_move_txid(None, move_txid).await.unwrap().unwrap().0 == Some(honest_operator_xonly_pk)` — i.e., attribution equals the framed operator.
   - Separately assert no transaction with `honest_operator_xonly_pk`'s wallet inputs exists spending the withdrawal UTXO on chain (proving the framed operator never funded it).
4. Start/allow the honest operator's `PayoutCheckerTask::run_once` (or call `Operator::handle_finalized_payout` directly with the deposit outpoint and the attacker's `payout_tx_blockhash`) and assert it returns `Ok(kickoff_txid)` — i.e., `handle_finalized_payout` succeeds and the framed operator proceeds into the reimbursement flow, despite never having funded any payout. [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

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

**File:** core/src/verifier.rs (L2283-2352)
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

**File:** core/src/task/payout_checker.rs (L39-111)
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

        dbtx.commit().await?;

        Ok(true)
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

**File:** circuits-lib/src/bridge_circuit/mod.rs (L182-229)
```rust
    // Storage proof verification for deposit tx index and withdrawal outpoint
    let (user_wd_outpoint, vout, move_txid) =
        verify_storage_proofs(&input.sp, light_client_circuit_output.l2_state_root);

    let user_wd_txid = bitcoin::Txid::from_byte_array(*user_wd_outpoint);

    let payout_input_index: usize = input.payout_input_index as usize;

    assert_eq!(
        user_wd_txid,
        input.payout_spv.transaction.input[payout_input_index]
            .previous_output
            .txid,
        "Invalid withdrawal transaction ID"
    );

    assert_eq!(
        vout,
        input.payout_spv.transaction.input[payout_input_index]
            .previous_output
            .vout,
        "Invalid withdrawal transaction output index"
    );

    let first_op_return_output = get_first_op_return_output(&input.payout_spv.transaction)
        .expect("Payout transaction must have an OP_RETURN output");

    let round_txid = input.kickoff_tx.input[0]
        .previous_output
        .txid
        .to_byte_array();

    let kickoff_round_vout = input.kickoff_tx.input[0].previous_output.vout;

    let operator_xonlypk: [u8; 32] = parse_op_return_data(&first_op_return_output.script_pubkey)
        .expect("Invalid operator xonlypk")
        .try_into()
        .expect("Invalid xonlypk");

    let deposit_constant = deposit_constant(
        operator_xonlypk,
        input.watchtower_challenge_connector_start_idx,
        &input.all_tweaked_watchtower_pubkeys,
        *move_txid,
        round_txid,
        kickoff_round_vout,
        input.hcp.genesis_state_hash,
    );
```
