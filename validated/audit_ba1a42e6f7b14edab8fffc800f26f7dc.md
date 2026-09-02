### Title
Operators are forced into unwanted, connector-consuming Kickoff/Reimburse cycles via forged payout OP_RETURN attribution, permanently starving them of kickoff connectors - (File: `core/src/operator.rs`, `core/src/verifier.rs`, `core/src/task/payout_checker.rs`)

### Summary
`handle_finalized_payout` and the automated `PayoutCheckerTask` attribute a Bitcoin payout transaction to an operator purely based on the operator xonly-pubkey embedded in the payout tx's OP_RETURN output, which is completely unauthenticated and attacker-controllable because the payout's spending signature uses `SIGHASH_SINGLE|ANYONECANPAY`. An unprivileged attacker who fully self-funds a withdrawal (paying themselves with their own BTC) can attach an arbitrary operator P's xonly-pubkey in the OP_RETURN, causing P's own automation to treat the payout as P's, spend one of P's scarce, per-round kickoff connectors, and broadcast a real Kickoff transaction on P's behalf and dime. Repeating this drains P's finite kickoff-connector supply, eventually making a genuine, honestly-fronted withdrawal by P unreimbursable.

### Finding Description
The broken binding: `payout_payer_operator_xonly_pk == operator who actually funded (fronted) the withdrawal`. In reality the code only enforces `payout_payer_operator_xonly_pk == whatever xonly-pubkey bytes appear in the payout tx's OP_RETURN output`.

- `create_payout_txhandler` (`core/src/builder/transaction/operator_reimburse.rs:407-436`) builds the payout tx with a single `KeySpend` input signed by the withdrawer's own signature, and appends an OP_RETURN naming the "fronting" operator — but nothing cryptographically ties the OP_RETURN's key to who actually supplied the transaction's funding.
- The withdrawer's authorization signature is required to use `TapSighashType::SinglePlusAnyoneCanPay` (`core/src/rpc/parser/operator.rs:161-187`), which by definition allows **anyone** to add arbitrary additional inputs/outputs (including funding inputs and the OP_RETURN) to the final broadcast transaction. `Operator::withdraw` (`core/src/operator.rs:560-680`) itself relies on this by calling `fund_raw_transaction` to add the operator's own funding inputs — i.e., the protocol's own design permits a third party to complete the transaction.
- `Verifier::update_finalized_payouts` (`core/src/verifier.rs:2283-2352`) blindly parses whatever xonly-pubkey is present in the broadcast payout tx's OP_RETURN and stores it as `payout_payer_operator_xonly_pk` with no check that this operator contributed any funds.
- `PayoutCheckerTask::run_once` (`core/src/task/payout_checker.rs:39-111`) polls `get_first_unhandled_payout_by_operator_xonly_pk(self.operator.signer.xonly_public_key)` (`core/src/database/verifier.rs:282-313`) — i.e., each operator's own automation node picks up any payout row whose OP_RETURN names *itself*, with no verification that it was that operator who broadcast/funded it.
- `Operator::handle_finalized_payout` (`core/src/operator.rs:839-970`) then calls `get_unused_and_signed_kickoff_connector` (`core/src/database/operator.rs:902-946`), advances the round via `end_round` if needed, creates and (under `#[cfg(feature = "automation")]`) actually broadcasts a real signed Kickoff transaction, and finally calls `mark_kickoff_connector_as_used` — permanently consuming one of the operator's finite, globally-shared `(round_idx, kickoff_idx)` connectors (`used_kickoff_connectors` has PK `(round_idx, kickoff_connector_idx)`, not scoped per-deposit, so consuming one for a forged claim removes it from the operator's pool for all other deposits too).
- `Verifier::is_kickoff_malicious` (`core/src/verifier.rs:1857-1915`), the guard meant to catch mismatched kickoffs, explicitly checks `operator_xonly_pk != kickoff_data.operator_xonly_pk` — but since the attacker forges the OP_RETURN to *equal* P's real pubkey, this check passes and does not flag the kickoff as malicious.

Exploit flow: attacker deposits BTC normally (any real deposit, which under this protocol's fixed operator set includes P as a co-signer of the deposit's kickoffs), then withdraws by fully self-funding the payout transaction (their own dust UTXO + SIGHASH_SINGLE|ANYONECANPAY signature, plus attacker-supplied additional inputs to cover the entire output amount), appending an OP_RETURN naming P instead of themselves, and broadcasting it directly to the Bitcoin network (bypassing operator RPCs entirely, since nothing requires the OP_RETURN writer to be the one who called `withdraw`). Once finalized, P's own `PayoutCheckerTask` consumes one of P's kickoff connectors and broadcasts a real Kickoff for a payout P never made or wanted.

### Impact Explanation
Each forged attribution permanently removes one physical kickoff connector from operator P's globally shared pool (`num_kickoffs_per_round × num_round_txs`, a value fixed at deposit/setup time). Repeating this across `num_kickoffs_per_round` (and eventually across all `num_round_txs`) withdrawals attributed to P exhausts P's entire connector supply. When P subsequently tries to claim reimbursement for a withdrawal it genuinely and honestly fronted with its own capital, `get_unused_and_signed_kickoff_connector` returns `None` and `handle_finalized_payout` fails with `BridgeError::DatabaseError(sqlx::Error::RowNotFound)`, permanently blocking P's legitimate reimbursement. This matches the Critical category "an honest operator permanently unable to be reimbursed." The attack is repeatable across any number of deposits/withdrawals and against any operator in the current operator set, since the OP_RETURN attribution mechanism has no per-operator authentication.

### Likelihood Explanation
The attacker needs no special privileges: only the ability to deposit into the bridge, obtain the withdrawal authorization data, construct a standard `SIGHASH_SINGLE|ANYONECANPAY`-signed Bitcoin transaction (self-funded, at their own BTC cost plus fees, with no net loss beyond fees since they pay themselves), and broadcast it with a crafted OP_RETURN. This requires deploying real capital temporarily for each round-trip deposit/withdrawal but no persistent loss of funds to the attacker; the cost is purely transaction fees and locked capital during the deposit/withdraw cycle. Given a finite `num_round_txs`/`num_kickoffs_per_round` paramset, exhausting a target operator's connectors is feasible with a bounded number of repetitions, especially against operators with small `num_kickoffs_per_round`.

### Recommendation
Do not treat the OP_RETURN pubkey as authoritative proof of who funded a payout. Either (a) require the payout's completing/funding party to also provide a signature or on-chain proof (e.g., an operator-controlled anchor input or musig-cosigned commitment) binding the named operator to the transaction's actual funding inputs, or (b) have `Verifier::is_kickoff_malicious` / `update_finalized_payouts` validate that the operator named in the OP_RETURN actually contributed sufficient value to the payout transaction (e.g., check that one of the additional inputs is provably owned/spent by that operator's known key), rejecting/flagging kickoffs whose funding cannot be attributed to the claimed operator.

### Proof of Concept
```rust
// cargo test conceptual outline (regtest, MockCitreaClient, no mainnet):
// 1. Set up bridge with operator P (index 0) with a small `num_kickoffs_per_round`.
// 2. For i in 0..num_kickoffs_per_round (repeat across num_round_txs if needed):
//      a. Perform a real deposit (attacker-controlled funds) and citrea withdraw registration.
//      b. Attacker builds a self-funded payout tx (own dust UTXO + SinglePlusAnyoneCanPay sig +
//         attacker-supplied inputs covering full output amount), attaches OP_RETURN with P's
//         real xonly_pk (not P's own broadcast, P's operator RPC never called), and broadcasts it directly via rpc.
//      c. Mine to finality; let P's PayoutCheckerTask process it automatically.
//      d. Assert `used_kickoff_connectors` gained a new row bound to P's round/kickoff idx
//         even though P never called `withdraw`/`internal_finalized_payout` itself.
// 3. After exhausting P's connectors, perform ONE real deposit+withdrawal that P actually fronts
//    itself via `operator0.withdraw(...)`.
// 4. Call P's PayoutCheckerTask (or `internal_finalized_payout`) for this genuine payout and assert
//    it fails with `BridgeError::DatabaseError(sqlx::Error::RowNotFound)` from
//    `get_unused_and_signed_kickoff_connector` returning `None`, proving P can no longer be
//    reimbursed for a withdrawal it genuinely funded.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

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

**File:** core/src/operator.rs (L560-680)
```rust
    pub async fn withdraw(
        &self,
        withdrawal_index: u32,
        in_signature: taproot::Signature,
        in_outpoint: OutPoint,
        out_script_pubkey: ScriptBuf,
        out_amount: Amount,
    ) -> Result<Transaction, BridgeError> {
        tracing::info!(
            "Withdrawing with index: {}, in_signature: {:?}, in_outpoint: {:?}, out_script_pubkey: {}, out_amount: {}",
            withdrawal_index,
            in_signature,
            in_outpoint,
            out_script_pubkey,
            out_amount
        );

        // Prepare input and output of the payout transaction.
        let input_prevout = self.rpc.get_txout_from_outpoint(&in_outpoint).await?;
        let input_utxo = UTXO {
            outpoint: in_outpoint,
            txout: input_prevout,
        };
        let output_txout = TxOut {
            value: out_amount,
            script_pubkey: out_script_pubkey,
        };

        // Check Citrea for the withdrawal state.
        let withdrawal_utxo = self
            .db
            .get_withdrawal_utxo_from_citrea_withdrawal(None, withdrawal_index)
            .await?;

        if withdrawal_utxo != input_utxo.outpoint {
            return Err(eyre::eyre!("Input UTXO does not match withdrawal UTXO from Citrea: Input Outpoint: {0}, Withdrawal Outpoint (from Citrea): {1}", input_utxo.outpoint, withdrawal_utxo).into());
        }

        let operator_withdrawal_fee_sats =
            self.config
                .operator_withdrawal_fee_sats
                .ok_or(BridgeError::ConfigError(
                    "Operator withdrawal fee sats is not specified in configuration file"
                        .to_string(),
                ))?;
        if !Self::is_profitable(
            input_utxo.txout.value,
            output_txout.value,
            self.config.protocol_paramset().bridge_amount,
            operator_withdrawal_fee_sats,
        ) {
            return Err(eyre::eyre!("Not enough fee for operator").into());
        }

        let user_xonly_pk = &input_utxo
            .txout
            .script_pubkey
            .try_get_taproot_pk()
            .wrap_err("Input utxo script pubkey is not a valid taproot script")?;

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

        let fee_rate = self
            .rpc
            .get_fee_rate_kvb(
                self.config.protocol_paramset.network,
                &self.config.mempool_api_host,
                &self.config.mempool_api_endpoint,
                self.config.tx_sender_limits.mempool_fee_rate_multiplier,
                self.config.tx_sender_limits.mempool_fee_rate_offset_sat_kvb,
                self.config.tx_sender_limits.fee_rate_hard_cap,
            )
            .await?;

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
                    fee_rate: Some(Amount::from_sat(fee_rate.to_sat_per_kvb())),
                    subtract_fee_from_outputs: None,
                    replaceable: None,
                    conf_target: None,
                    estimate_mode: None,
                }),
                None,
            )
            .await
            .wrap_err("Failed to fund raw transaction")?
            .hex;

        let signed_tx = self
            .rpc
            .sign_raw_transaction_with_wallet(&funded_tx, None, None)
            .await
            .wrap_err("Failed to sign withdrawal transaction")?
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

**File:** core/src/database/operator.rs (L902-946)
```rust
    pub async fn get_unused_and_signed_kickoff_connector(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        deposit_id: u32,
        operator_xonly_pk: XOnlyPublicKey,
    ) -> Result<Option<(RoundIndex, u32)>, BridgeError> {
        let query = sqlx::query_as::<_, (i32, i32)>(
            "WITH current_round AS (
                    SELECT round_idx
                    FROM current_round_index
                    WHERE id = 1
                )
                SELECT
                    ds.round_idx as round_idx,
                    ds.kickoff_idx as kickoff_connector_idx
                FROM deposit_signatures ds
                CROSS JOIN current_round cr
                WHERE ds.deposit_id = $1  -- Parameter for deposit_id
                    AND ds.operator_xonly_pk = $2
                    AND ds.round_idx >= cr.round_idx
                    AND NOT EXISTS (
                        SELECT 1
                        FROM used_kickoff_connectors ukc
                        WHERE ukc.round_idx = ds.round_idx
                        AND ukc.kickoff_connector_idx = ds.kickoff_idx
                    )
                ORDER BY ds.round_idx ASC
                LIMIT 1;",
        )
        .bind(i32::try_from(deposit_id).wrap_err("Failed to convert deposit id to i32")?)
        .bind(XOnlyPublicKeyDB(operator_xonly_pk));

        let result = execute_query_with_tx!(self.connection, tx, query, fetch_optional)?;

        match result {
            Some((round_idx, kickoff_connector_idx)) => Ok(Some((
                RoundIndex::from_index(
                    usize::try_from(round_idx).wrap_err("Failed to convert round idx to u32")?,
                ),
                u32::try_from(kickoff_connector_idx)
                    .wrap_err("Failed to convert kickoff connector idx to u32")?,
            ))),
            None => Ok(None),
        }
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
