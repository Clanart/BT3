### Title
Unauthenticated OP_RETURN operator attribution in `update_finalized_payouts` lets a withdrawer credit an uninvolved operator's reimbursement - ([File: core/src/verifier.rs])

### Summary
`update_finalized_payouts` derives `payout_payer_operator_xonly_pk` purely by parsing the raw OP_RETURN bytes of the on-chain payout transaction with `parse_op_return_data`/`XOnlyPublicKey::from_slice`, with no signature or funding proof binding that pubkey to the party who actually constructed/broadcast the transaction. Because the payout tx has a single input (the user's own already-funded withdrawal UTXO, `create_payout_txhandler`, [1](#0-0) ) signed off-chain by the withdrawer, the withdrawer can choose the sighash flag on their own signature (`in_signature`), broadcast the spend themselves with an arbitrary OP_RETURN naming any real operator's public xonly key, and that operator's own `PayoutCheckerTask` will pick it up as its own unhandled payout and reimburse itself for work it never did.

### Finding Description
The intended binding is: `operator_xonly_pk in OP_RETURN == the operator whose signature/funds actually produced payout_tx`. Tracing the code shows this binding is never checked:

- The payout tx as constructed by an honest operator (`create_payout_txhandler`) has exactly one input - the registered `withdrawal_utxo` - and the withdrawer's own `taproot::Signature` (`user_sig`) is placed as the sole key-spend witness: [1](#0-0) . The sighash type on that signature is chosen by the withdrawer (`in_signature`/`taproot::Signature::sighash_type`) when calling `Operator::withdraw`, which only checks that the input outpoint and value match the Citrea-registered withdrawal, and that the output amount is "profitable" - it never constrains or checks the signature's sighash flag: [2](#0-1) .
- Because the withdrawer holds the private key for the withdrawal input and can pick `SIGHASH_SINGLE|ANYONECANPAY`, only the specific output paired with that input index (the withdrawer's own payout output) is committed by the signature; the anchor output and the OP_RETURN output are completely unconstrained. The withdrawer can therefore construct and broadcast the spending transaction themselves, without ever giving the signature to any operator, and place any 32-byte value in the OP_RETURN.
- On chain sync, `update_finalized_payouts` finds the payout tx for the withdrawal (matched purely by spent-outpoint, not by any operator identity), extracts the OP_RETURN payload, and parses it as `operator_xonly_pk` with zero cryptographic check that this key had anything to do with the transaction: [3](#0-2) . `parse_op_return_data` is a pure script-instruction parser with no signature verification at all: [4](#0-3) .
- This poisoned `payout_payer_operator_xonly_pk` is persisted via `update_payout_txs_and_payer_operator_xonly_pk`: [5](#0-4) .
- The targeted (real, uninvolved) operator's own `PayoutCheckerTask` queries `get_first_unhandled_payout_by_operator_xonly_pk(self.operator.signer.xonly_public_key)` [6](#0-5) , finds the forged entry (since the poisoned column equals its own key), and unconditionally calls `handle_finalized_payout`, which allocates a kickoff connector and starts the kickoff/reimbursement process for a payout it never touched: [7](#0-6) .
- `Verifier::is_kickoff_malicious`, the only guard verifiers use to decide whether an operator's kickoff for a deposit is legitimate, merely compares the DB-stored `operator_xonly_pk` (the same poisoned value) against the kickoff sender's xonly pk - since both equal the targeted operator, the check passes and does not detect the forgery: [8](#0-7) .

No component ever verifies that the credited operator's own signature or wallet inputs produced the specific payout transaction; attribution is 100% derived from unauthenticated, attacker-controlled OP_RETURN bytes.

### Impact Explanation
A real operator, who never saw the withdrawer's signature and never broadcast anything, is autonomously driven by its own `PayoutCheckerTask` to start the kickoff/reimburse flow and eventually draw reimbursement value from its round/collateral structure for a payout it did not fund - this is a direct "operator reimbursed for a payout it never funded" (Critical). The attack is fully repeatable: any withdrawer can target any operator whose xonly public key is public knowledge (used everywhere in the protocol) for every withdrawal they register, across arbitrarily many deposits, spreading false reimbursement obligations across the operator set. It also degrades verifiers' `is_kickoff_malicious` safety check, since it operates over the same poisoned data.

### Likelihood Explanation
The attacker only needs to be a normal withdrawer: register a withdrawal on the Citrea Bridge contract, sign their own withdrawal input with a self-chosen sighash flag, and broadcast the spend directly to Bitcoin with a forged OP_RETURN - all actions explicitly within the unprivileged attacker capability set (no operator/verifier keys, no majority hashrate, no TLS interception needed). The only "cost" is normal Bitcoin transaction fees. This requires no cooperation from any operator and no race condition; it is deterministic and repeatable for every withdrawal the attacker controls.

### Recommendation
Do not trust the OP_RETURN payload alone for attribution. Require that the reconstructed payout transaction (input, all outputs including the OP_RETURN, using the operator's own key and the same withdrawal signature) matches byte-for-byte (or via a full-transaction signature/sighash commitment) what is observed on-chain before crediting the named operator - i.e., have the operator (or verifiers) recompute `create_payout_txhandler` for the claimed operator, `PartialEq` against the mined tx, or require the withdrawer's signature to use `SIGHASH_ALL`/`SIGHASH_DEFAULT` (or otherwise commit to the OP_RETURN output) so the operator's identity is protected under the signature, closing the malleability gap in `update_finalized_payouts`.

### Proof of Concept
```
cargo test --package clementine-core --lib core::test::payout_attribution_forgery -- --nocapture
```
Test plan:
1. Set up two operators A (target/victim) and B (nothing to do with the test) plus normal verifiers via the existing test harness (`create_actors`, `run_single_deposit`).
2. Perform a deposit and register a withdrawal (mirroring `generate_withdrawal_transaction_and_signature` used in `core/src/test/deposit_and_withdraw_e2e.rs`), but have the "withdrawer" sign the withdrawal input with `SIGHASH_SINGLE|SIGHASH_ANYONECANPAY` instead of the default flag used by `create_payout_txhandler`.
3. Instead of calling any operator's `Withdraw`/`InternalWithdraw` RPC, directly build a transaction with: input = the registered withdrawal UTXO + the forged signature; output[0] = withdrawer's own address; output[1] = anchor; output[2] = OP_RETURN containing operator A's `xonly_public_key.serialize()` bytes. Broadcast it via `rpc.send_raw_transaction`.
4. Mine blocks until finalized; let block sync run `update_finalized_payouts`.
5. Assert: `db.get_payout_info_from_move_txid(...)` on operator A's DB returns `Some(operator_A_xonly_pk, ...)` even though operator A never called `Withdraw`/`InternalWithdraw` (binding-before: operator_xonly_pk field is `NULL`/unset; binding-after: it equals `operator_A.signer.xonly_public_key`, which is the broken equality).
6. Assert that `PayoutCheckerTask::run_once` on operator A's node returns `Ok(true)` and calls `mark_payout_handled`, and that a Kickoff tx for operator A is queued/sent - i.e., operator A is being driven toward reimbursement for a payout it never funded, with operator B (and no operator at all) having actually paid the withdrawer.

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

**File:** core/src/verifier.rs (L2311-2343)
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
```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L609-617)
```rust
pub fn parse_op_return_data(script: &Script) -> Option<&[u8]> {
    let mut instructions = script.instructions();
    if let Some(Ok(Instruction::Op(opcodes::all::OP_RETURN))) = instructions.next() {
        if let Some(Ok(Instruction::PushBytes(data))) = instructions.next() {
            return Some(data.as_bytes());
        }
    }
    None
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
