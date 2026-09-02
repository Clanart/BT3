This confirms the vulnerability path fully. `handle_finalized_payout` never checks that operator D actually funded/broadcast the payout_tx — it purely relies on `deposit_id` (derived from `deposit_outpoint` looked up via `move_to_vault_txid`, which itself was matched by `PayoutCheckerTask` purely from `get_first_unhandled_payout_by_operator_xonly_pk`, which is populated purely from the OP_RETURN parse in `Verifier::update_finalized_payouts`). It grabs an unused/pre-signed kickoff connector for D and unconditionally queues Kickoff/Reimburse transactions for D, with no verification that D's own BTC funded output 0 of the payout_tx.

### Title
Attacker-controlled OP_RETURN in a self-funded `payout_tx` lets anyone assign operator reimbursement credit to an arbitrary, uninvolved operator - ([File: core/src/verifier.rs])

### Summary
`Verifier::update_finalized_payouts` attributes a withdrawal's payout to whichever operator's x-only pubkey appears in the payout_tx's OP_RETURN output, with no check that this operator's BTC actually funded output 0. Since a withdrawer registers their own `input_outpoint`/signature via Citrea's `withdraw` and signs only input0/output0 with `SIGHASH_SINGLE|ANYONECANPAY`, an attacker can construct and broadcast a fully self-funded `payout_tx` (adding their own extra input(s) to cover the output value) while placing an arbitrary honest operator D's xonly_pk in the OP_RETURN, causing D's `PayoutCheckerTask` to pick it up and unconditionally consume D's kickoff connector and queue Kickoff/Reimburse transactions via `Operator::handle_finalized_payout`.

### Finding Description
The broken binding: `payout_payer_operator_xonly_pk` (the operator credited for withdrawal `i`, as stored by `update_payout_txs_and_payer_operator_xonly_pk`) should equal the operator whose BTC actually paid output 0 of that withdrawal's `payout_tx`. In reality it only equals whatever x-only pubkey bytes appear in the payout_tx's OP_RETURN output, parsed with no further validation: [1](#0-0) 

This value is stored via `update_payout_txs_and_payer_operator_xonly_pk` and later read by `get_first_unhandled_payout_by_operator_xonly_pk`, filtered purely by `operator_xonly_pk = $1` with no cross-check against Bitcoin funding: [2](#0-1) 

`PayoutCheckerTask::run_once` fetches this attribution and calls `Operator::handle_finalized_payout` for the operator whose node is running the task: [3](#0-2) 

`handle_finalized_payout` looks up deposit data purely from `deposit_outpoint`, grabs an unused pre-signed kickoff connector, and unconditionally builds/queues the Kickoff/Reimburse transaction chain for that operator — with no check that the operator's BTC funded the payout: [4](#0-3) [5](#0-4) 

The withdrawer-controlled `payout_tx` input is only committed via `SIGHASH_SINGLE|ANYONECANPAY` over input 0/output 0, exactly as constructed in `create_payout_txhandler`, which leaves the transaction free to add attacker-controlled additional inputs/outputs (including the OP_RETURN operator pubkey), all under the withdrawer's own control since they are also the party who registered the withdrawal via Citrea's `withdraw`: [6](#0-5) [7](#0-6) 

Later, `Verifier::is_kickoff_malicious` only checks that the OP_RETURN pubkey matches the kickoff's own operator pubkey and that the committed payout blockhash matches — both trivially true here since D's own automation generated the kickoff using D's own key against the attacker-forged OP_RETURN: [8](#0-7) 

No code path anywhere (deposit validity checks, `Operator::is_profitable`, `SPV::verify`, `verify_storage_proofs`, or the BitVM/light-client circuits via `deposit_constant`) verifies that output 0 of `payout_tx` was actually funded by the credited operator's own UTXOs; `deposit_constant` only binds move_txid, watchtower digest, operator xonly pk, round txid/vout, and genesis state hash — never a funding-source check.

### Impact Explanation
This matches the Critical category "an operator reimbursed for a payout it never funded." An unprivileged attacker can, for any deposit/withdrawal slot they control end-to-end (self-registering via `withdraw` and self-funding their own payout), name an arbitrary operator in the OP_RETURN, forcing that operator's automated `PayoutCheckerTask` to consume one of its limited kickoff connectors and drive it through Kickoff→Reimburse, ultimately spending real Bitcoin fees from the named operator's wallet and claiming the deposit's move-to-vault BTC as reimbursement for a payout that operator never funded. This is repeatable per deposit/withdrawal slot and works against any operator whose xonly_pk is public (all operator pubkeys are public protocol data), with no consent or participation required from the targeted operator.

### Likelihood Explanation
Preconditions are minimal and match the stated unprivileged attacker capability set exactly: call Citrea's `withdraw` for a slot the attacker controls, sign input0/output0 with `SIGHASH_SINGLE|ANYONECANPAY` themselves, add attacker-owned extra funding inputs to cover the output value (paid from the attacker's own wallet), and append an OP_RETURN containing any target operator's public xonly_pk, then broadcast on Bitcoin. Cost is only the attacker's own BTC fronting + fees for their own payout — no cooperation from any operator, verifier, or aggregator is required. This is reliably reproducible in a `cargo test` using the existing `MockCitreaClient`/regtest test harness, since none of the existing e2e assertions verify operator-funding correspondence, only OP_RETURN/kickoff-pubkey and blockhash consistency (`is_kickoff_malicious`).

### Recommendation
Bind payout attribution to actual BTC funding, not just an OP_RETURN data push. Options: (1) require the payout transaction's non-withdrawal-UTXO inputs to be spendable outputs of a script/key uniquely tied to the claimed operator (e.g. requiring the operator's fronting input to be present and signed by the operator's own key, verified during `update_finalized_payouts`), or (2) require operators to only credit themselves via their own `Operator::withdraw` RPC call (which already performs `is_profitable` and signature checks) and have `update_finalized_payouts`/`PayoutCheckerTask` cross-reference that this specific txid was one the operator itself broadcast (e.g. compare against a locally-tracked set of self-sent payout txids) rather than trusting an arbitrary OP_RETURN payload observed on-chain.

### Proof of Concept
`cargo test` plan (extending `core/src/test/manual_reimbursement.rs`/`deposit_and_withdraw_e2e.rs` patterns with `MockCitreaClient`):
1. Run a single deposit as in `run_single_deposit`, register it in Citrea via `MockCitreaClient::insert_deposit_move_txid`.
2. As the "attacker," generate a withdrawal via `generate_withdrawal_transaction_and_signature` using a locally-owned key (not any operator's), register the withdrawal UTXO via `insert_withdrawal_utxo`.
3. Construct a `payout_tx` manually (not via `Operator::withdraw`): input 0 = attacker's dust UTXO with the attacker's own `SIGHASH_SINGLE|ANYONECANPAY` signature over output 0 (paid to attacker's own address); add an attacker-funded extra input to cover the output amount; add the anchor output; add an OP_RETURN output containing **operator D's** `xonly_public_key` (an operator that never called `withdraw`/never signed off). Broadcast this via `rpc.send_raw_transaction`.
4. Mine blocks to finality; let D's `PayoutCheckerTask` run (`run_once`/background task loop).
5. Assert: `db.get_payout_info_from_move_txid(...)` returns `Some(D_xonly_pk, ...)` (binding LHS = D) while asserting no gRPC call, signature, or transaction from D's signer ever authorized fronting this payout (binding RHS = attacker only) — i.e. `operator_xonly_pk_credited == D.signer.xonly_public_key` yet `D` never called `Operator::withdraw` nor signed the payout's funding input.
6. Assert `Operator::handle_finalized_payout` for D succeeds and returns a `kickoff_txid`, and that `db.get_handled_payout_kickoff_txid(payout_txid)` becomes `Some(kickoff_txid)` — confirming D was driven into the kickoff/reimburse flow for a payout D never funded, violating the credited-operator == funding-operator invariant.

### Citations

**File:** core/src/verifier.rs (L1882-1914)
```rust
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

**File:** core/src/verifier.rs (L2312-2335)
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

**File:** core/src/operator.rs (L560-626)
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
```

**File:** core/src/operator.rs (L839-860)
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
```

**File:** core/src/operator.rs (L925-967)
```rust
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
