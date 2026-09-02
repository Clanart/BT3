### Title
Operator reimbursed via Reimburse tx without verifying actual BTC delivered to withdrawer in wd-UTXO spend - ([File: core/src/verifier.rs])

### Summary
Verifiers and the operator's `PayoutCheckerTask` treat *any* transaction that spends the registered withdrawal (`wd`) UTXO and contains an OP_RETURN with a parseable x-only pubkey as proof that the named operator fronted the withdrawal, without ever checking that the amount actually paid to the withdrawer in that transaction corresponds to a legitimate payout. Since the withdrawal registrant controls the private key of the `wd` UTXO, they can broadcast their own spend (0 sats to themselves, arbitrary OP_RETURN naming any operator) and the honest operator's own automation will pick it up and drive Kickoff → Assert → ChallengeTimeout → Reimburse.

### Finding Description
The binding that should hold is:
`amount actually delivered to the withdrawer in the wd-UTXO-spending transaction == amount for which the named operator is later reimbursed via Reimburse`.

Tracing the code shows this equality is never checked:

- `Operator::withdraw` ( [1](#0-0) ) is the *intended* flow: the operator builds `create_payout_txhandler`, verifies the user's Schnorr signature against `sighash = payout_txhandler.calculate_sighash_txin(0, in_signature.sighash_type)`, and embeds `self.signer.xonly_public_key` in the OP_RETURN. This binds output 0 (amount/script) to the user's signature under whatever sighash flag they choose (docs suggest `SIGHASH_SINGLE|ANYONECANPAY`), but this path is not the only way to spend the UTXO on-chain.
- `create_payout_txhandler` ( [2](#0-1) ) shows the payout tx structure: output 0 = payout, output 1 = anchor, output 2 = OP_RETURN with an **arbitrary, unauthenticated** `operator_xonly_pk` chosen at construction time — nothing on-chain ties this pubkey to consent from that operator.
- Because the withdrawer/attacker owns the `wd` UTXO's private key (`user_xonly_pk` derived straight from the UTXO's own script pubkey, [3](#0-2) ), they can sign and broadcast **any** transaction spending it directly to Bitcoin, completely bypassing the operator's gRPC `withdraw` call — e.g. an output 0 paying the withdrawer 0 sats and an OP_RETURN naming an arbitrary target operator.
- `Verifier::update_finalized_payouts` ( [4](#0-3) ) scans every block for spends of registered withdrawal UTXOs and unconditionally records whichever `operator_xonly_pk` is parsed out of the first OP_RETURN output — no check on the amount or script paid to the withdrawer at output 0.
- `Verifier::is_kickoff_malicious` ( [5](#0-4) ) later only checks that the OP_RETURN operator matches the kickoff's claimed operator and that the committed payout blockhash matches — it never re-derives or checks the payout amount.
- `PayoutCheckerTask::run_once` ( [6](#0-5) ) blindly queries `get_first_unhandled_payout_by_operator_xonly_pk` for the operator's own key and calls `handle_finalized_payout`, driving Kickoff/Assert, again with no amount check.
- `bridge_circuit` ( [7](#0-6) ) only asserts that the `payout_spv.transaction`'s spent input txid/vout matches the storage-proved `user_wd_outpoint` and that an OP_RETURN xonly pubkey parses — it never asserts that the value of output 0 matches any committed withdrawal amount.

None of the guards mentioned in the audit checklist (`is_kickoff_malicious`, storage proof verification, SPV verify) validate the actual BTC amount delivered to the withdrawer against the amount later released to the operator via Reimburse. The only place amount matching is enforced is the *optimistic payout* path (`sign_optimistic_payout`, [8](#0-7) ), which is a different transaction type (co-signed N-of-N optimistic payout, not the operator-fronted Kickoff/Reimburse path).

### Impact Explanation
An operator can be driven through Kickoff → Assert → ChallengeTimeout → Reimburse and receive reimbursement (funded ultimately from bridge-side value tied to the deposit/move-to-vault chain) for a withdrawal it never funded, matching "Critical – an operator reimbursed for a payout it never funded." This is repeatable per withdrawal request: any withdrawal registrant can grief this way at the cost of forfeiting their own real payout (they receive 0 sats), and can target any operator's public key since the OP_RETURN field is unauthenticated attacker-controlled data. The blast radius spans every deposit/withdrawal cycle and every operator, since the detection logic in `update_finalized_payouts` and `PayoutCheckerTask` is shared code used for all operators.

### Likelihood Explanation
Preconditions are minimal and fully within the stated unprivileged attacker capabilities: the attacker must (1) call Citrea's `withdraw()` to register a `wd` UTXO of their choosing, (2) own/construct the corresponding Bitcoin UTXO and its spending key, and (3) broadcast a transaction spending it with a crafted OP_RETURN. No verifier/operator/aggregator privileges, collateral, or key shares are required. Cost is limited to Bitcoin transaction fees and the sacrifice of the attacker's own withdrawal proceeds (they get 0 BTC back). The attack is deterministic and reproducible on regtest since it only depends on standard Bitcoin script/signature mechanics and the documented detection code paths.

### Recommendation
Bind the operator's reimbursement eligibility to a value check on the payout transaction, not merely the presence of an OP_RETURN pubkey: `update_finalized_payouts` / `is_kickoff_malicious` should additionally verify that the amount paid to the withdrawer's output (output 0 of the payout tx) meets or exceeds the withdrawal amount registered/committed for that `deposit_id`/`withdrawal_index` on the Citrea side (or otherwise cryptographically bind the payout amount into data that the bridge circuit's `deposit_constant`/journal actually checks), rather than accepting any spend of the `wd` UTXO with a matching OP_RETURN as sufficient evidence of a legitimate fronted payout.

### Proof of Concept
```rust
// cargo test proof plan (regtest, no mainnet, no live Citrea):
// 1. Set up e2e harness (per core/src/test/deposit_and_withdraw_e2e.rs style) with a deposit and
//    a registered withdrawal (`withdrawal_utxo`) owned by a test keypair the "attacker" controls.
// 2. Instead of calling operator.withdraw()/aggregator.withdraw(), directly construct a Transaction:
//    - input: withdrawal_utxo
//    - output0: pay withdrawer 0 sats (or dust) to attacker's own address
//    - output1: anchor
//    - output2: OP_RETURN containing move_txid || target_operator_xonly_pk (any operator running in the harness)
//    Sign with SIGHASH_SINGLE|SIGHASH_ANYONECANPAY (or any valid flag) using attacker's own key
//    for the withdrawal_utxo's script pubkey, and broadcast via rpc.
// 3. Mine DEFAULT_FINALITY_DEPTH blocks, let verifier sync (update_finalized_payouts).
// 4. Assert (LHS of binding): amount delivered to withdrawer == 0 sats (query the mined tx's output0.value).
// 5. Wait for target operator's PayoutCheckerTask to mark payout handled
//    (operator_db.get_handled_payout_kickoff_txid) and drive Kickoff -> Assert -> ChallengeTimeout.
// 6. Mine through the ChallengeTimeout timelock, confirm Reimburse tx becomes spendable/confirmed
//    paying the target operator (assert reimburse_connector's spending tx exists and pays operator).
// 7. Assert (RHS of binding): operator reimbursement amount > 0 (non-trivial value) while LHS == 0,
//    proving the binding "amount delivered to withdrawer == amount reimbursed to operator" is broken.
```

### Citations

**File:** core/src/operator.rs (L560-637)
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

**File:** core/src/verifier.rs (L1634-1659)
```rust
        // amount in move_tx is exactly the bridge amount
        if output_amount
            > self.config.protocol_paramset().bridge_amount - NON_EPHEMERAL_ANCHOR_AMOUNT
        {
            return Err(eyre::eyre!(
                "Output amount is greater than the bridge amount: {} > {}",
                output_amount,
                self.config.protocol_paramset().bridge_amount - NON_EPHEMERAL_ANCHOR_AMOUNT
            )
            .into());
        }

        // check if withdrawal utxo is correct
        let withdrawal_utxo = self
            .db
            .get_withdrawal_utxo_from_citrea_withdrawal(None, deposit_id)
            .await?;

        if withdrawal_utxo != input_outpoint {
            return Err(eyre::eyre!(
                "Withdrawal utxo is not correct: {:?} != {:?}",
                withdrawal_utxo,
                input_outpoint
            )
            .into());
        }
```

**File:** core/src/verifier.rs (L1859-1915)
```rust
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
