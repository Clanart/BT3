### Title
Unauthenticated OP_RETURN operator attribution in `update_finalized_payouts` lets anyone credit an arbitrary operator for a payout they never funded - ([File: core/src/verifier.rs])

### Summary
`Verifier::update_finalized_payouts` derives `payout_payer_operator_xonly_pk` solely from the OP_RETURN bytes of whichever transaction is first observed spending the registered `withdrawal_utxo`, with no check that the named operator actually supplied the funds. Because the payout signature uses `SIGHASH_SINGLE|ANYONECANPAY`, only input 0 and output 0 are committed, so anyone controlling the withdrawal UTXO can add their own funding inputs and attach an arbitrary OP_RETURN naming a real operator, race-broadcast it, and have that operator's automated `PayoutCheckerTask` treat it as an unhandled payout eligible for reimbursement.

### Finding Description
The binding the codebase implicitly assumes is: `payout_payer_operator_xonly_pk == the xonly public key of the party whose funds paid output_txout of the payout transaction`.

`create_payout_txhandler` builds output 0 (user payout), output 1 (anchor), output 2 (`op_return_txout(operator_xonly_pk)`), and signs only input 0 with the user's key-path signature (`taproot::Signature` with `sighash_type`) [1](#0-0) . When the user's/attacker's signature uses `TapSighashType::SinglePlusAnyoneCanPay`, that signature only commits the digest of input 0 and output 0; the operator (`Operator::withdraw`) normally funds the transaction via `fund_raw_transaction` adding its own input(s), and puts its own `operator_xonly_pk` in the OP_RETURN [2](#0-1) . Nothing in the protocol restricts who can perform this "fund + append OP_RETURN + broadcast" step — anyone holding the withdrawal outpoint's signature can do it themselves, choosing any xonly public key for the OP_RETURN.

On the verifier side, `update_finalized_payouts` finds whichever transaction spent the registered `withdrawal_utxo` (via `get_payout_txs_for_withdrawal_utxos`, itself sourced from the generic `bitcoin_syncer_spent_utxos` table that records the spender of any tracked outpoint, not specifically an operator-authored transaction) [3](#0-2) , then blindly parses the OP_RETURN of that transaction and stores whatever 32-byte value looks like a valid `XOnlyPublicKey` as the payer [4](#0-3) . There is no signature check, no verification that this key funded the output, and no requirement that the party appending the OP_RETURN be the actual operator.

Downstream, `PayoutCheckerTask::run_once` polls `get_first_unhandled_payout_by_operator_xonly_pk` filtered only by `payout_payer_operator_xonly_pk = operator's own key` [5](#0-4) [6](#0-5) . If it matches, the operator automatically calls `handle_finalized_payout`, which drives kickoff creation and ultimately a `Reimburse` transaction paying the operator the full deposited amount from `move_txhandler`'s `DepositInMove` output [7](#0-6)  — with no manual step for the operator to verify they truly funded that specific payout.

Existing guards do not close this gap:
- `Operator::is_profitable` and `SECP.verify_schnorr` only execute inside `Operator::withdraw`'s own gRPC code path [8](#0-7) ; an attacker bypasses this entirely by broadcasting the transaction directly to Bitcoin.
- `validate_payer_is_operator` only compares the already-forged stored `payer_xonly_pk` against `self.signer.xonly_public_key` [9](#0-8)  — it cannot detect the forgery because it trusts the same unauthenticated column.
- No database uniqueness/constraint validates the OP_RETURN payer against any cryptographic proof of funding.

### Impact Explanation
An attacker who owns/controls a withdrawal UTXO (any user who deposited and withdrew via Citrea) can name any real operator's `XOnlyPublicKey` in the payout's OP_RETURN and broadcast the transaction with attacker-supplied funding before the legitimate operator acts. Because Bitcoin double-spend rules prevent the same withdrawal UTXO from being spent twice, the legitimate operator can never register a competing payout for that withdrawal. The named operator's automated `PayoutCheckerTask` will then kick off and eventually claim a `Reimburse` transaction paying the operator the deposited `bridge_amount` from the move-to-vault UTXO for a payout it never actually funded — matching the Critical category "an operator reimbursed for a payout it never funded." This is repeatable across every withdrawal and every operator xonly public key the attacker chooses to name, at the cost of only Bitcoin fees for the attacker's own withdrawal.

### Likelihood Explanation
No special privilege is required: the attacker only needs to be a normal bridge user who has deposited and can call Citrea's `withdraw`, choose a signature with `SinglePlusAnyoneCanPay`, and broadcast a self-funded, self-authored transaction to Bitcoin before any operator's own automated `withdraw` flow does. This requires no key compromise, no majority hashrate, and no insider access — it is directly reachable through the documented, intended payout-signing scheme (`SIGHASH_SINGLE|ANYONECANPAY` deliberately leaves other outputs/inputs unsigned to let operators add funding, but this same property lets anyone else add funding and forge attribution). The race is winnable by simply broadcasting with sufficient fee ahead of the legitimate operator.

### Recommendation
Bind the OP_RETURN operator attribution to a value that cannot be forged by a third party — e.g., require the operator's kickoff/round-tx signature to also cover or reference the payout transaction's actual outputs (not just claim an unauthenticated pubkey), or require the payout's funding input(s) to be provably linked to the named operator's collateral/round UTXO before `update_finalized_payouts` records `payout_payer_operator_xonly_pk`. At minimum, do not let `PayoutCheckerTask` proceed to kickoff/reimbursement purely from the OP_RETURN-derived value without an operator-side cross-check that the operator itself broadcast (or co-signed) that specific payout transaction.

### Proof of Concept
```
cargo test -p clementine-core update_finalized_payouts_forged_op_return_attribution
```
Plan:
1. Set up a deposit and a registered withdrawal (`withdrawal_utxo`, `output_script_pubkey`, `output_amount`) exactly as `update_withdrawal_utxo_from_citrea_withdrawal`/`upsert_move_to_vault_txid_from_citrea_deposit` require (per `core/src/database/verifier.rs::update_get_payout_txs_from_citrea_withdrawal` pattern).
2. Construct a user signature over the withdrawal's key-path spend with `TapSighashType::SinglePlusAnyoneCanPay`.
3. Using `create_payout_txhandler`, build the payout transaction, but instead of using the *real* operator's `operator_xonly_pk`, build it with an "attacker" party as input funder (add an extra funding input as a non-operator) and place a *different*, real operator's `XOnlyPublicKey` (`operator_B`) in the OP_RETURN, while the actual funds for output 0 come from the attacker's own added input, not from `operator_B`.
4. Broadcast this transaction, mine it, and run `Verifier::update_finalized_payouts` (or call it directly) against the block.
5. Assert: `db.get_payout_info_from_move_txid(move_txid)` returns `operator_xonly_pk == Some(operator_B)`, even though `operator_B` never funded or authored the transaction — proving `payout_payer_operator_xonly_pk` diverges from the actual funder.
6. Optionally continue: run `get_first_unhandled_payout_by_operator_xonly_pk(operator_B)` and confirm it surfaces this withdrawal, demonstrating `operator_B`'s automated flow would proceed to kickoff/reimbursement for a payout it never funded.

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

**File:** core/src/operator.rs (L605-674)
```rust
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
```

**File:** core/src/operator.rs (L1686-1729)
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
