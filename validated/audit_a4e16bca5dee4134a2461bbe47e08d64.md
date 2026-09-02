### Title
Payout attribution relies on an unsigned OP_RETURN field, letting any party misattribute reimbursement credit to an operator who never funded the payout - (File: core/src/verifier.rs, core/src/builder/transaction/operator_reimburse.rs)

### Summary
The withdrawal/reimbursement flow attributes "who fronted a peg-out" purely from the plaintext `operator_xonly_pk` written into the payout transaction's OP_RETURN output. This field is not covered by the user's withdrawal signature (which uses `SIGHASH_SINGLE|SIGHASH_ANYONECANPAY`), so anyone in possession of the user's signature — which is shared with (potentially multiple) operators off-chain — can construct and broadcast their own payout transaction, fund it themselves, and stamp an arbitrary registered operator's x-only pubkey into the OP_RETURN. The chain-observer (`update_finalized_payouts`) blindly trusts this field to decide which operator is "the payer," and that operator's automation will subsequently claim the full bridge-amount reimbursement for a payout it never actually funded.

### Finding Description
The payout transaction is built in `create_payout_txhandler`, which signs only the user's withdrawal input with `SpendPath::KeySpend` and embeds the fronting operator's identity in an OP_RETURN output that is added to the transaction but is never part of what the user signs: [1](#0-0) 

The withdrawal signature type used throughout the protocol is `SinglePlusAnyoneCanPay` (`SIGHASH_SINGLE | SIGHASH_ANYONECANPAY`), confirmed by its pervasive use in the withdrawal-signing/verification code paths (`core/src/actor.rs`, `core/src/rpc/parser/operator.rs`, `core/src/builder/transaction/deposit_signature_owner.rs`, `core/src/builder/transaction/txhandler.rs`). Under `SIGHASH_SINGLE|ANYONECANPAY`, the signature commits only to the single input being spent and the output at the same index (output 0 — the user's payout amount). It does **not** commit to the anchor output (index 1) or the OP_RETURN output (index 2) that carries the operator's x-only pubkey. Any party who obtains this off-chain signature (which the docs and the aggregator's `withdraw` RPC show is fanned out to all operators, not kept secret to one) can therefore build a *different* payout transaction: same signed input + same output 0 (to satisfy the signature), but arbitrary extra funding inputs and an arbitrary OP_RETURN payload naming any operator.

The chain watcher extracts and persists this attribution with no additional cryptographic check tying it to the actual funder of the transaction: [2](#0-1) 

This value is written to the `withdrawals` table as `payout_payer_operator_xonly_pk`: [3](#0-2) 

An operator's own automation (`PayoutCheckerTask`) picks up "its" unhandled payouts purely by matching this DB column against its own xonly pubkey, with no verification that it actually broadcast/funded the corresponding payout transaction: [4](#0-3) [5](#0-4) 

`validate_payer_is_operator` only checks that the DB-recorded payer pubkey equals `self.signer.xonly_public_key` — it cannot detect that the operator itself did not create/fund this transaction: [6](#0-5) 

Finally, the anti-fraud check `is_kickoff_malicious`, which is supposed to prevent a bogus operator from claiming an unrelated payout, only checks that the OP_RETURN operator pubkey matches the kickoff sender's pubkey and that the committed payout blockhash matches — both of which trivially hold since the real, valid, on-chain payout transaction (built by the attacker) genuinely paid the user and genuinely contains the named operator's pubkey: [7](#0-6) 

None of these checks verify that the credited operator supplied the additional funding inputs of the payout transaction (i.e., that the credited party is the party that paid).

### Impact Explanation
This breaks the required binding "the operator credited versus the party that paid." An unprivileged party that intercepts a user's withdrawal authorization (broadcast by the aggregator to all/several operators via `withdraw`/`optimistic_payout` flows) can front the withdrawal using entirely their own funds while naming a different, arbitrary registered operator in the OP_RETURN. The named operator's automation will then legitimately proceed through the kickoff/round/reimburse flow (`create_reimburse_txhandler`) and receive the full bridge-amount reimbursement out of the deposit's move-to-vault UTXO for a payout it never funded: [8](#0-7) 

This matches the Critical impact category "an operator reimbursed for a payout it never funded" — the bridge's vault value leaves to reimburse a party that did not front the corresponding withdrawal, and the true funder receives nothing, with no cryptographic guarantee that credited-operator == actual-payer.

### Likelihood Explanation
The precondition is only that the attacker obtains a user's `SinglePlusAnyoneCanPay` withdrawal signature — which the protocol itself distributes to potentially multiple operators over the network (the aggregator's `Withdraw` RPC fans the same signed params out to a set of operator clients), and which is not bound to a single recipient by any additional authentication once it leaves the signer. No verifier, operator, watchtower, or privileged role is required to mount the misattribution — only the ability to observe/replay the already-broadcast signature and construct/broadcast a competing transaction with different extra inputs and a different OP_RETURN, which is a standard unprivileged Bitcoin transaction-construction capability. This raises it above a purely theoretical concern, though it does require timing (racing the legitimate payout broadcast) which affects likelihood without affecting validity.

### Recommendation
Bind the OP_RETURN operator-attribution data to the same signature domain that authorizes the payout, e.g., by having the fronting operator sign (with their own key) a commitment over the specific payout transaction's actual funding inputs/outputs, or by switching the withdrawal signature to `SIGHASH_ALL`/`SIGHASH_DEFAULT` (or including the OP_RETURN output in the SIGHASH_SINGLE-equivalent commitment) so the OP_RETURN payload cannot be swapped once the user has authorized a specific payout. Additionally, `is_kickoff_malicious` and `update_finalized_payouts` should require proof that the kickoff-sending operator's own signed inputs funded the payout transaction, not merely that the unauthenticated OP_RETURN names them.

### Proof of Concept
1. User signs a withdrawal request with `SinglePlusAnyoneCanPay`, which the aggregator relays to operator set `{A, B}` via `Withdraw`.
2. Attacker `M` (not necessarily A or B) observes this broadcast signature.
3. `M` builds a payout transaction reusing the user's signed input and output 0 (satisfying the signature), adds its own funding UTXO(s) to cover the payout amount, and sets the OP_RETURN to operator `B`'s x-only pubkey (per `create_payout_txhandler`, `core/src/builder/transaction/operator_reimburse.rs:407-436`).
4. `M` broadcasts this transaction, funded entirely by `M`.
5. The verifier's chain sync (`update_finalized_payouts`, `core/src/verifier.rs:2283-2353`) records `payout_payer_operator_xonly_pk = B`.
6. `B`'s `PayoutCheckerTask` (`core/src/task/payout_checker.rs:39-79`) detects the unhandled payout attributed to itself and proceeds through `handle_finalized_payout`/kickoff/reimburse, ultimately receiving the full bridge-amount reimbursement (`create_reimburse_txhandler`, `core/src/builder/transaction/operator_reimburse.rs:341-385`) for a payout it never funded, while `M` who actually paid the user receives nothing.

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

**File:** core/src/verifier.rs (L2312-2343)
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

**File:** core/src/operator.rs (L1686-1740)
```rust
    /// For a deposit_id checks that the payer for that deposit is the operator, and the payout blockhash and kickoff txid are set.
    async fn validate_payer_is_operator(
        &self,
        dbtx: Option<DatabaseTransaction<'_>>,
        deposit_id: u32,
    ) -> Result<(BlockHash, Txid), BridgeError> {
        let (payer_xonly_pk, payout_blockhash, kickoff_txid) = self
            .db
            .get_payer_xonly_pk_blockhash_and_kickoff_txid_from_deposit_id(dbtx, deposit_id)
            .await?;

        tracing::info!(
            "Payer xonly pk and kickoff txid found for the requested deposit, payer xonly pk: {:?}, kickoff txid: {:?}",
            payer_xonly_pk,
            kickoff_txid
        );

        // first check if the payer is the operator, and the kickoff is handled
        // by the PayoutCheckerTask, meaning kickoff_txid is set
        let (payout_blockhash, kickoff_txid) = match (
            payer_xonly_pk,
            payout_blockhash,
            kickoff_txid,
        ) {
            (Some(payer_xonly_pk), Some(payout_blockhash), Some(kickoff_txid)) => {
                if payer_xonly_pk != self.signer.xonly_public_key {
                    return Err(eyre::eyre!(
                        "Payer is not own operator for deposit, payer xonly pk: {:?}, operator xonly pk: {:?}",
                        payer_xonly_pk,
                        self.signer.xonly_public_key
                    )
                    .into());
                }
                (payout_blockhash, kickoff_txid)
            }
            _ => {
                return Err(eyre::eyre!(
                    "Payer info not found for deposit, payout blockhash: {:?}, kickoff txid: {:?}",
                    payout_blockhash,
                    kickoff_txid
                )
                .into());
            }
        };

        tracing::info!(
            "Payer xonly pk, payout blockhash and kickoff txid found and valid for own operator for the requested deposit id: {}, payer xonly pk: {:?}, payout blockhash: {:?}, kickoff txid: {:?}",
            deposit_id,
            payer_xonly_pk,
            payout_blockhash,
            kickoff_txid
        );

        Ok((payout_blockhash, kickoff_txid))
    }
```
