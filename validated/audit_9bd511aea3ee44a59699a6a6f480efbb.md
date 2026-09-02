### Title
Payout OP_RETURN operator-credit is unauthenticated by the withdrawal signature, letting an outsider redirect an already-fronted payout's reimbursement credit to an uninvolved operator - ([File: core/src/rpc/parser/operator.rs], [File: core/src/database/verifier.rs])

### Summary
The user's withdrawal signature on the `payout_tx` is required to use `TapSighashType::SinglePlusAnyoneCanPay` [1](#0-0) , which under BIP341 only commits to input 0 and the single output at the same index (output 0). Outputs 1 (anchor) and 2 (OP_RETURN operator credit) in `create_payout_txhandler` [2](#0-1)  are completely unsigned, and `ANYONECANPAY` means any additional funding inputs are unsigned too. The chain-sync logic that assigns reimbursement credit only looks at whatever transaction ultimately spends the recorded `withdrawal_utxo` outpoint and parses that confirmed transaction's OP_RETURN, with no cross-check against which operator's wallet/inputs actually paid for it [3](#0-2) [4](#0-3) .

### Finding Description
Binding claimed by the protocol: `payout_payer_operator_xonly_pk` (the xonly pk written into output 2's OP_RETURN of the tx that is recorded as spending `withdrawal_utxo`) == the xonly pk of the operator whose own funds/wallet actually paid for output 0 of the withdrawal. This binding is never enforced anywhere in the codebase.

Trace:
1. When an operator "fronts" a withdrawal via `Operator::withdraw`, it builds `create_payout_txhandler(input_utxo, output_txout, operator_xonly_pk, user_sig, ...)`, which places `operator_xonly_pk` in output 2's OP_RETURN, and inserts the *unmodified* user-supplied witness for input 0 via `set_p2tr_key_spend_witness` [2](#0-1) . The signature is verified against `in_signature.sighash_type`, which the aggregator/operator enforces to be `SinglePlusAnyoneCanPay` [5](#0-4) , and the sighash is computed with `Prevouts::One` for that flag [6](#0-5) . Under this sighash flag the signature commits *only* to input 0's prevout and to output 0 (the index-matched output) — outputs 1 and 2 and any additional inputs are unconstrained.
2. Because the operator funds the transaction via `fund_raw_transaction`/RBF using its own wallet inputs beyond input 0 [7](#0-6) , the resulting mempool transaction is visible to any observer before confirmation.
3. An attacker (unprivileged, can broadcast Bitcoin transactions and pay fees) copies input 0 with its exact witness, keeps output 0 unchanged (still valid under the signature), and constructs a new transaction that swaps output 2's OP_RETURN to name a different, uninvolved but valid operator B's xonly pk, funding it with the attacker's own fee input. Because SIGHASH_SINGLE|ANYONECANPAY does not cover output 2 or additional inputs, this modified transaction is cryptographically valid and spends the same withdrawal UTXO.
4. If this attacker transaction confirms first (race/RBF against the original broadcast), `update_finalized_payouts` scans the confirming block, finds whichever tx actually spent `withdrawal_utxo_txid`/`withdrawal_utxo_vout` [3](#0-2) , parses its OP_RETURN for an operator xonly pk with no ownership check [4](#0-3) , and writes `payout_payer_operator_xonly_pk = B` into `withdrawals` via `update_payout_txs_and_payer_operator_xonly_pk` [8](#0-7) .
5. Operator B's own `PayoutCheckerTask` polls `get_first_unhandled_payout_by_operator_xonly_pk(own_xonly_pk)`, which is a tautological check (it filters by exactly the value that was written from the attacker's own OP_RETURN, not verified against actual funding) [9](#0-8) [10](#0-9) . It then calls `Operator::handle_finalized_payout`, which drives kickoff and ultimately `create_reimburse_txhandler`, entitling operator B to `bridge_amount` from the vault [11](#0-10) .
6. The only guard that references the recorded payer, `is_kickoff_malicious`, checks `operator_xonly_pk != kickoff_data.operator_xonly_pk` [12](#0-11)  — but since `kickoff_data.operator_xonly_pk` is chosen by whichever operator (here B) subsequently creates the kickoff, and it will trivially equal the (attacker-forged) recorded payer B, this check passes and does not catch the forgery. No code anywhere verifies that the wallet/fee-paying inputs of the confirmed payout tx belong to the operator named in its own OP_RETURN.

### Impact Explanation
An uninvolved operator (or an operator maliciously targeted by a third party, or one who is simply named by an attacker without their knowledge) can be credited for a withdrawal it never funded, and can then run the normal, automated `Operator::handle_finalized_payout` → kickoff → `create_reimburse_txhandler` flow to withdraw `bridge_amount` from the move-to-vault UTXO. This is a Critical impact category: "an operator reimbursed for a payout it never funded." Simultaneously the honest operator A, who actually paid the withdrawal from their own wallet, has no recorded credit for that withdrawal (the DB row now shows payer = B), so A cannot pass `get_first_unhandled_payout_by_operator_xonly_pk` for their own key and is permanently unable to claim reimbursement for funds they legitimately fronted — a second Critical-category outcome ("an honest operator permanently unable to be reimbursed"). This is repeatable for every withdrawal an attacker observes in the mempool before confirmation and for any registered operator xonly pk the attacker chooses to name.

### Likelihood Explanation
Preconditions: an operator must broadcast a `payout_tx` to the Bitcoin mempool (normal operation of `Operator::withdraw`), giving the attacker visibility of input 0's witness and output 0 before confirmation. The attacker needs no privileged role — only the ability to construct and broadcast a competing Bitcoin transaction with its own fee input, consistent with the stated unprivileged threat model. Getting the modified transaction confirmed instead of the original requires either winning a block race or exploiting RBF policy (both explicitly listed as available attacker techniques in the question) — this is a Bitcoin-mempool/relay-level action, not reliant on hash-power majority. Because `create_payout_txhandler`'s only cryptographic binding is `SinglePlusAnyoneCanPay` over output 0, this is fully deterministic once the mempool race is won — there is no additional cryptographic barrier.

### Recommendation
Bind the operator credit to something the user's signature (or a verifier-checked artifact) actually commits to, instead of trusting the confirmed spending transaction's mutable OP_RETURN. Options: (a) require operators to pre-register/commit (e.g., via aggregator or DB write prior to broadcast) the exact payout txid they intend to get confirmed, and have `update_finalized_payouts`/`get_payout_txs_for_withdrawal_utxos` only credit that specific pre-committed txid rather than "whatever transaction spent the outpoint"; (b) change the signature scheme so the user's signature also commits to output 2 (e.g., use `AllPlusAnyoneCanPay` or otherwise sign over the OP_RETURN output), preventing free mutation of the credited operator; (c) require that the additional funding input(s) used to pay fees are provably owned/signed by the same operator named in the OP_RETURN (e.g., verify a signature from the OP_RETURN-named operator's key over the whole transaction) before crediting a payout in the DB.

### Proof of Concept
```rust
// core/src/test/... (new test, not part of excluded scopes since it demonstrates the vuln)
// 1. Set up e2e harness with two operators A and B (both valid, registered operators).
// 2. Operator A calls withdraw(): builds payout_tx via create_payout_txhandler with
//    operator_xonly_pk = A, broadcasts it (do NOT mine yet).
// 3. Capture the unconfirmed tx from the mempool (rpc.get_raw_transaction / get_mempool_entry).
// 4. Attacker (test code acting as unprivileged party) constructs a new transaction:
//    - input[0] = same outpoint, same witness (copy verbatim) as operator A's tx
//    - output[0] = identical to operator A's tx (user payout, untouched)
//    - output[1] = attacker's own anchor/fee-bump input added as an additional tx input
//    - output[2] = OP_RETURN rewritten to operator_xonly_pk = B
//    - fund with an attacker-controlled additional input (paying fees), sign it with the
//      attacker's own key (only needed for the ANYONECANPAY-added input, not input 0)
// 5. Broadcast the attacker's version and mine it (simulating a successful race/RBF win),
//    ensuring operator A's original broadcast tx does NOT confirm.
// 6. Assert: SECP.verify_schnorr still succeeds for input 0's witness against output[0]
//    of the attacker's tx (proving the signature never covered output 2).
// 7. Wait for verifier/operator B's PayoutCheckerTask to run; assert
//    db.get_first_unhandled_payout_by_operator_xonly_pk(B) returns Some(...) for this withdrawal.
// 8. Assert operator B's handle_finalized_payout succeeds and B's Reimburse tx
//    (via create_reimburse_txhandler) gets broadcast/confirmed, crediting B with bridge_amount.
// 9. Assert operator A's db.get_first_unhandled_payout_by_operator_xonly_pk(A) returns None,
//    i.e., A is permanently unable to claim reimbursement for the withdrawal it actually funded.
```

Both sides of the claimed binding (`payout_payer_operator_xonly_pk == funder of output 0`) diverge after the attack: the DB records B, but B fronted nothing and A (the real funder) is unrecorded — confirming the vulnerability.

### Citations

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

**File:** core/src/builder/transaction/txhandler.rs (L222-233)
```rust
        let prevouts = match sighash_type {
            TapSighashType::SinglePlusAnyoneCanPay
            | TapSighashType::AllPlusAnyoneCanPay
            | TapSighashType::NonePlusAnyoneCanPay => {
                bitcoin::sighash::Prevouts::One(txin_index, prevouts_vec[txin_index])
            }
            _ => bitcoin::sighash::Prevouts::All(&prevouts_vec),
        };

        let sig_hash = sighash_cache
            .taproot_key_spend_signature_hash(txin_index, &prevouts, sighash_type)
            .wrap_err("Failed to calculate taproot sighash for key spend")?;
```

**File:** core/src/operator.rs (L628-675)
```rust
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
