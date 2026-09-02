## #Vulnerability found for this question.

### Title
Unauthenticated OP_RETURN operator attribution in payout tx allows crediting an uninvolved operator for a withdrawal it never funded - (File: core/src/verifier.rs::update_finalized_payouts, core/src/database/verifier.rs::get_first_unhandled_payout_by_operator_xonly_pk, core/src/operator.rs::handle_finalized_payout/validate_payer_is_operator)

### Summary
The protocol determines which operator gets reimbursed for a withdrawal solely by parsing an unsigned OP_RETURN output from the mined payout transaction [1](#0-0) , while the only cryptographic commitment in that transaction (`SinglePlusAnyoneCanPay`) covers input 0 and output 0 only, leaving the OP_RETURN and any additional funding inputs completely unauthenticated and attacker-controllable. Anyone who possesses (or is themselves) the withdrawing user's S+AP signature can construct a payout tx that pays the withdrawal correctly but stamps an arbitrary, uninvolved operator's `XOnlyPublicKey` in the OP_RETURN, causing that operator's `PayoutCheckerTask` to pick up and reimburse a withdrawal it never funded or even saw.

### Finding Description
Binding claimed to hold: `withdrawals.payout_payer_operator_xonly_pk` (the operator credited/reimbursed for withdrawal `i`) == the party that actually funded output 0 of the mined payout tx for withdrawal `i`.

Trace:
1. A withdrawal's payout tx is built via `create_payout_txhandler` with three outputs: the user's payout output (index 0), an anchor, and an OP_RETURN with the fronting operator's xonly pubkey (index 2) [2](#0-1) .
2. The only signature present is the withdrawing user's, and it is required to use `TapSighashType::SinglePlusAnyoneCanPay` [3](#0-2) . `calculate_pubkey_spend_sighash`/`calculate_sighash_txin` show that for `*PlusAnyoneCanPay` types, only the single named input's prevout is committed (`Prevouts::One`) [4](#0-3) , and `SIGHASH_SINGLE` only binds output index 0. This means: the OP_RETURN (output index 2) and any additional inputs added to fund output 0's amount are outside the signed message and can be freely chosen by whoever finally assembles and broadcasts the transaction.
3. When the bitcoin syncer/verifier observes a tx spending the tracked `withdrawal_utxo_txid`/`withdrawal_utxo_vout` [5](#0-4) , `update_finalized_payouts` parses the first OP_RETURN output and blindly trusts its contents as the "payer operator xonly pk," with no check that this key's owner authorized, signed, or funded anything [6](#0-5) . This is persisted via `update_payout_txs_and_payer_operator_xonly_pk` [7](#0-6) .
4. Operator B's `PayoutCheckerTask::run_once` polls `get_first_unhandled_payout_by_operator_xonly_pk(B's key)`, which matches purely on the DB column set from the OP_RETURN [8](#0-7) [9](#0-8) .
5. This drives `Operator::handle_finalized_payout`, which allocates a kickoff connector and proceeds toward a `Reimburse` tx for B [10](#0-9) . The only "authorization" check anywhere in this flow, `validate_payer_is_operator`, merely re-reads the same DB column and compares it to `self.signer.xonly_public_key` [11](#0-10)  - i.e., it checks internal DB consistency, not that B actually broadcast, signed, or funded the payout.
6. `Verifier::is_kickoff_malicious` also only cross-checks that the kickoff's `operator_xonly_pk` matches the OP_RETURN-derived DB value [12](#0-11)  - again consistency between two attacker-influenced/derived values, not authenticity of B's participation.

Exploit flow: An unprivileged party (the withdrawing user themselves, or anyone who obtains their S+AP signature, which per the threat model the attacker fully controls since they can call `withdraw` on Citrea and choose the signature/outpoint/OP_RETURN) constructs a payout tx spending withdrawal `i`'s outpoint, keeps output 0 unchanged (required for signature validity), funds any additional required amount from their own wallet via extra `ANYONECANPAY` inputs, and writes Operator B's `XOnlyPublicKey` (a real, uninvolved operator) into the OP_RETURN. If this tx is mined before/instead of any tx Operator B would have constructed, the DB permanently records B as the payer for withdrawal `i`, and B's own automation autonomously drives a kickoff/Reimburse claim for a payout B never made.

### Impact Explanation
This directly matches the Critical category "an operator reimbursed for a payout it never funded." Operator B's kickoff connector and eventual collateral/reimbursement flow are consumed for a withdrawal B never authorized, never funded, and never called `Operator::withdraw` for. This is repeatable per-withdrawal and can target any registered operator whose xonly pubkey is public (all operators' keys are discoverable via `fetch_operator_keys`/aggregator config). It can be used to grief an arbitrary operator (forcing consumption of its finite per-round kickoff connectors and collateral cycle) or, if the attacker's OP_RETURN choice benefits a colluding operator, to fraudulently extract bridge collateral for a withdrawal actually funded by someone else.

### Likelihood Explanation
No special privileges, verifier/operator/aggregator role, or key compromise are required - only the ability to call Citrea's `withdraw`, choose the S+AP signature/outpoint, and broadcast a Bitcoin transaction with the desired OP_RETURN, all of which are explicitly in-scope for an unprivileged attacker. Cost is limited to normal Bitcoin network fees plus funding the withdrawal's own output amount (which the attacker/withdrawing party would pay for the withdrawal regardless). The race only requires the attacker's transaction to confirm/be observed before or in lieu of the intended operator's own payout tx, which is straightforward since the attacker controls broadcast timing and can watch the mempool.

### Recommendation
Bind operator attribution cryptographically rather than trusting an unauthenticated OP_RETURN: require the fronting operator to co-sign the payout tx (e.g., via a MuSig2/aggregate signature covering all inputs and the OP_RETURN, or an operator-specific signature over the OP_RETURN payload) so that the recorded `payout_payer_operator_xonly_pk` can only be set by the corresponding operator's own signature, and reject/ignore payout txs whose OP_RETURN key cannot be verified against a signature from that key.

### Proof of Concept
```rust
// cargo test in core/src/test (integration-style, using regtest + real bitcoin_syncer)
// 1. Set up two real operators, Operator A (intended) and Operator B (uninvolved), plus a bridge deposit.
// 2. Perform a Citrea withdrawal as the attacker (or reuse the test's user role):
//    build the dust UTXO input + S+AP signature over (input0, output0) exactly as
//    `generate_withdrawal_transaction_and_signature` does (core/src/test/common/setup_utils.rs:439-449).
// 3. Instead of calling Operator A's or Operator B's `withdraw()` RPC, manually construct
//    the payout transaction as the attacker:
//    - input0 = withdrawal dust UTXO with the S+AP signature (unchanged, required for validity)
//    - output0 = the exact withdrawal TxOut (unchanged, required for validity)
//    - additional input(s) funded from the attacker's own wallet to cover output0's amount
//    - OP_RETURN output = Operator B's xonly_pk (NOT Operator A's, NOT the attacker's own)
//    Broadcast and mine this transaction directly via rpc, bypassing both operators' `withdraw()`.
// 4. Assert Operator B's signer never called `Operator::withdraw` (Operator B never touched this outpoint).
// 5. Wait for finality and poll Operator B's db:
//    assert_eq!(
//        operator_b_db.get_payout_info_from_move_txid(None, move_txid).await.unwrap().unwrap().0,
//        Some(operator_b_xonly_pk)
//    );
// 6. Wait for Operator B's PayoutCheckerTask (already running in background) to mark it handled:
//    poll until operator_b_db.get_handled_payout_kickoff_txid(None, payout_txid).await.unwrap().is_some();
// 7. Assert a kickoff/Reimburse flow was queued for Operator B despite B never funding/broadcasting
//    the payout: e.g. assert Operator B's tx_sender queue contains a Kickoff tx with
//    operator_xonly_pk == operator_b_xonly_pk for this deposit_outpoint, proving B was driven into
//    reimbursement for a payout it never funded (binding violated: payer recorded = B,
//    actual funder of output0's extra input = attacker's wallet, not B).
```

### Citations

**File:** core/src/verifier.rs (L1882-1890)
```rust
        let Some(operator_xonly_pk) = operator_xonly_pk_opt else {
            tracing::warn!("No operator xonly pk found in payout tx OP_RETURN, assuming malicious");
            return Ok(true);
        };

        if operator_xonly_pk != kickoff_data.operator_xonly_pk {
            tracing::warn!("Operator xonly pk for the payout does not match with the kickoff_data");
            return Ok(true);
        }
```

**File:** core/src/verifier.rs (L2312-2342)
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

**File:** core/src/rpc/parser/operator.rs (L181-187)
```rust
    // enforce sighash type here
    if input_signature.sighash_type != TapSighashType::SinglePlusAnyoneCanPay {
        return Err(Status::invalid_argument(format!(
            "Input signature has wrong sighash type, SinglePlusAnyoneCanPay expected, got {}",
            input_signature.sighash_type
        )));
    }
```

**File:** core/src/builder/transaction/txhandler.rs (L222-229)
```rust
        let prevouts = match sighash_type {
            TapSighashType::SinglePlusAnyoneCanPay
            | TapSighashType::AllPlusAnyoneCanPay
            | TapSighashType::NonePlusAnyoneCanPay => {
                bitcoin::sighash::Prevouts::One(txin_index, prevouts_vec[txin_index])
            }
            _ => bitcoin::sighash::Prevouts::All(&prevouts_vec),
        };
```

**File:** core/src/database/verifier.rs (L168-196)
```rust
    /// Returns the withdrawal indexes and their spending txid for the given
    /// block id.
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

**File:** core/src/database/verifier.rs (L199-251)
```rust
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

**File:** core/src/operator.rs (L1687-1719)
```rust
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
```
