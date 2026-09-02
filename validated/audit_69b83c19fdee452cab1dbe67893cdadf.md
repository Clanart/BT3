### Title
Payout attribution (`OP_RETURN` operator key) is not committed by the user's withdrawal signature, allowing a mismatch between the operator that actually funded a payout and the operator credited/reimbursed for it - (File: `core/src/builder/transaction/operator_reimburse.rs`)

### Summary
`create_payout_txhandler` builds the `payout_tx` with the withdrawal input signed only with `SIGHASH_SINGLE|ANYONECANPAY`, which commits solely to input 0 and output 0 (the user's payout). The anchor output and the `OP_RETURN` output that records which operator "fronted" the withdrawal are **not** covered by that signature. Whoever actually supplies the transaction's fee-paying/value-funding inputs decides the final `OP_RETURN` content, and it is this content — not the identity of the wallet that funded the tx — that the protocol treats as the source of truth for who gets reimbursed.

### Finding Description
`create_payout_txhandler` [1](#0-0)  constructs the payout transaction with:
- input 0: the Citrea-committed withdrawal UTXO, signed by the user off-chain with `TapSighashType::SinglePlusAnyoneCanPay` [2](#0-1) 
- output 0: the user's payout (covered by the `SINGLE` commitment)
- output 1: anchor output
- output 2: `OP_RETURN` containing `operator_xonly_pk`, i.e. the attribution of "who fronted this peg-out"

Because `SinglePlusAnyoneCanPay` only binds input 0 to output 0, any party who can add their own (fully self-signed) additional inputs is free to attach *any* set of remaining outputs — including a completely different `OP_RETURN` payload — while still satisfying the original signature. The operator that actually funds the payout via `fund_raw_transaction`/`tx_sign_and_fill_sigs` [3](#0-2)  normally signs their own added inputs together with whatever `OP_RETURN` value was present at that time, but nothing in the protocol prevents a different party from building an alternative transaction that reuses only the exposed `SinglePlusAnyoneCanPay` witness for the withdrawal input, adds their own funding, and stamps an arbitrary operator's x-only pubkey in the `OP_RETURN`.

Whichever version of the double-spending transactions gets confirmed on-chain determines attribution: `update_finalized_payouts` reads the operator key straight out of the mined tx's `OP_RETURN` with no cross-check against who actually supplied the value [4](#0-3) , and persists it as `payout_payer_operator_xonly_pk` [5](#0-4) . This value alone drives the entire reimbursement pipeline:
- `PayoutCheckerTask` picks up "my" unhandled payouts purely by matching this stored xonly pubkey [6](#0-5) .
- `validate_payer_is_operator` only checks that the stored payer key equals the operator's own signer key — not that the operator actually funded the transaction [7](#0-6) .
- The subsequent kickoff/reimbursement flow (`get_reimbursement_txs` → `create_reimburse_txhandler`) reimburses the credited operator out of the deposit's move-to-vault UTXO for the full bridge amount [8](#0-7) .
- The only later sanity check, `is_kickoff_malicious`, merely verifies that the operator sending the kickoff matches whatever key ended up in the mined `OP_RETURN` — it does not verify that this operator actually paid for the payout [9](#0-8) .

This is analogous to the referenced PoolTogether bug: an accounting/attribution field (there, the vault's `totalSupply`; here, `payout_payer_operator_xonly_pk`) can diverge from the actual value movement (there, the sum of individual balances; here, the wallet that actually funded the payout) because a boundary condition in the state-transition logic fails to keep the two bound together. Here the boundary is the sighash type chosen for the payout transaction: `SinglePlusAnyoneCanPay` deliberately leaves the `OP_RETURN` output unauthenticated so that anyone funding the tx can add it, but the protocol never re-validates that the funder and the credited party are the same entity once the transaction lands on-chain.

### Impact Explanation
This breaks the binding "the operator credited (in `withdrawals.payout_payer_operator_xonly_pk`) equals the party that actually paid the withdrawal." A party who is not the credited operator can end up funding the payout, or — more importantly — a party can get an uninvolved/colluding operator credited with a payout they never funded. The credited operator can then walk the full reimbursement path (`get_reimbursement_txs` → `Reimburse` tx) and receive BTC out of the deposit's move-to-vault UTXO equal to the full bridge amount, without that operator ever having spent their own capital to front the withdrawal. This matches the report's "Critical" bucket: "an operator reimbursed for a payout it never funded."

### Likelihood Explanation
Exploitation requires: (1) visibility of the withdrawal's `SinglePlusAnyoneCanPay` witness, which becomes public once any operator broadcasts/funds a `payout_tx` attempt into the mempool (or is otherwise observed off-chain, since it is distributed to *all* eligible operators via the aggregator's `withdraw` broadcast to every operator client [10](#0-9) ), and (2) the ability to construct and get mined an alternative transaction reusing that witness with a different `OP_RETURN`, funded by the attacker's/colluding operator's own inputs. No verifier, watchtower, or key-compromise capability is needed — only observing propagated transaction data and broadcasting a standard, self-funded competing transaction, which is within the unprivileged-attacker profile.

### Recommendation
Bind the operator attribution to the same commitment as the payout value, e.g. by having the withdrawal signature use `SIGHASH_ALL` (or otherwise cover all outputs including the `OP_RETURN`) so that the credited operator key cannot be altered by anyone who did not obtain a fresh signature from the user for that exact `OP_RETURN` content; alternatively, cross-check at reimbursement time that the operator named in `OP_RETURN` is the same entity whose UTXOs/signatures actually fund the transaction (e.g., by requiring the operator to sign a commitment over the `OP_RETURN` payload as part of funding, and having verifiers validate that commitment before marking a payout as attributable to that operator).

### Proof of Concept
1. Operator A begins the standard withdraw flow: builds `payout_tx` via `create_payout_txhandler` with input 0 = withdrawal dust UTXO (signed `SinglePlusAnyoneCanPay` by the user off-chain), and funds it (`fund_raw_transaction` + `tx_sign_and_fill_sigs`) with `OP_RETURN` = Operator A's xonly pk, then broadcasts it, entering the mempool.
2. Attacker (any party with mempool visibility, no privileged role) extracts the exposed witness for input 0 (valid because `SinglePlusAnyoneCanPay` does not commit to anything but input 0/output 0).
3. Attacker builds a competing transaction: input 0 = the same withdrawal UTXO + reused witness, output 0 = identical user payout (required to satisfy the signature), plus attacker's own funding inputs and outputs, and a new `OP_RETURN` naming Operator B's xonly pk instead of A's.
4. Attacker gets their version confirmed first (e.g., higher fee-rate / CPFP).
5. `update_finalized_payouts` parses the confirmed tx's `OP_RETURN`, sets `payout_payer_operator_xonly_pk = Operator B` in the `withdrawals` table.
6. `PayoutCheckerTask` for Operator B finds this as its own unhandled payout (`get_first_unhandled_payout_by_operator_xonly_pk`), and Operator B proceeds through kickoff/`get_reimbursement_txs`/`Reimburse` to collect the full bridge amount from the deposit's move-to-vault UTXO — despite never having funded this withdrawal.

Note: I was not able to fully trace the exact default `sighash_type` used by `tx_sign_and_fill_sigs` for auxiliary funding inputs added via `fund_raw_transaction` (the search for it in `core/src/actor.rs` returned matches but the effort budget ran out before I could inspect them in detail), so I cannot state with 100% certainty whether *every* funding path commits to all outputs by default; this does not change the core vulnerability, since the withdrawal input's `SinglePlusAnyoneCanPay` signature — the only signature guaranteed to exist for every payout — never covers the `OP_RETURN` attribution output.

### Citations

**File:** core/src/builder/transaction/operator_reimburse.rs (L341-384)
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

**File:** core/src/operator.rs (L630-637)
```rust
        let sighash = payout_txhandler.calculate_sighash_txin(0, in_signature.sighash_type)?;

        SECP.verify_schnorr(
            &in_signature.signature,
            &Message::from_digest(*sighash.as_byte_array()),
            user_xonly_pk,
        )
        .wrap_err("Failed to verify signature received from user for payout txin. Ensure the signature uses SinglePlusAnyoneCanPay sighash type.")?;
```

**File:** core/src/operator.rs (L639-674)
```rust
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

**File:** core/src/operator.rs (L1703-1729)
```rust
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

**File:** core/src/verifier.rs (L1857-1890)
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

**File:** core/src/task/payout_checker.rs (L39-47)
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
```

**File:** core/src/rpc/aggregator.rs (L1870-1886)
```rust
        let operators = self
            .get_operator_clients()
            .iter()
            .zip(current_operator_xonly_pks.into_iter());
        let withdraw_futures = operators
            .filter(|(_, xonly_pk)| {
                // check if operator_xonly_pks is empty or contains the operator's xonly public key
                operator_xonly_pks_from_rpc.is_empty()
                    || operator_xonly_pks_from_rpc.contains(xonly_pk)
            })
            .map(|(operator, operator_xonly_pk)| {
                let mut operator = operator.clone();
                let params = withdraw_params_with_sig.clone();
                let mut request = Request::new(params);
                request.set_timeout(WITHDRAWAL_TIMEOUT);
                async move { (operator.withdraw(request).await, operator_xonly_pk) }
            });
```
