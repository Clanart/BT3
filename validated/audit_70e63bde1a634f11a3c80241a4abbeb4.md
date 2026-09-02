### Title
Third party can hijack any payout by copying its unauthenticated OP_RETURN output, permanently freezing the operator's reimbursement path - ([File: core/src/builder/transaction/operator_reimburse.rs])

### Summary
`create_payout_txhandler` signs the payout transaction with a `SinglePlusAnyoneCanPay` sighash, which under BIP-341 only commits to input 0 and output 0 (the user's payout output). The CPFP anchor (output 1) and the operator-identifying OP_RETURN (output 2) are completely unauthenticated. Any third party who obtains the signed transaction (returned directly in `RawSignedTx` from `Operator::withdraw`/aggregator `Withdraw`, or observed in the mempool once the operator broadcasts it) can reuse the same witness in a new transaction that pays the identical output 0, funds fees from its own wallet, and strips/garbles/omits the OP_RETURN, then win the mining race with a higher fee.

### Finding Description
The binding claimed to hold is: `withdrawals.payout_payer_operator_xonly_pk == Operator.signer.xonly_public_key of whichever operator's funded/broadcast tx gets mined`. Tracing the code shows this binding is **not enforced on-chain** — it is derived purely from whatever bytes happen to be in the mined transaction's OP_RETURN, with no signature covering that output.

- `Operator::withdraw` [1](#0-0)  builds the payout tx via `create_payout_txhandler`, computes the sighash for input 0 with `in_signature.sighash_type`, verifies it, funds it via `fund_raw_transaction`, signs, and **broadcasts it itself** [2](#0-1) , then returns the fully signed tx to the RPC caller.
- `create_payout_txhandler` places the user's output at index 0, the anchor at index 1, and the operator's xonly-pk OP_RETURN at index 2, and signs only with the key-spend witness for input 0 [3](#0-2) .
- `parse_withdrawal_sig_params` enforces `SinglePlusAnyoneCanPay`, silently rewriting a `Default` (64-byte) sighash type to it for "backward compatibility" [4](#0-3) .
- Under BIP-341, `SIGHASH_SINGLE|ANYONECANPAY` commits **only** to the single signed input and the output at the same index (index 0). It says nothing about outputs 1 and 2, or about any other inputs added later. This means the exact same witness is valid in *any* transaction that has this UTXO as input 0 and reproduces output 0 byte-for-byte, regardless of what else is in the transaction.
- Downstream, `update_finalized_payouts` scans whichever transaction actually spent `withdrawal_utxo_txid`/`vout` (via `bitcoin_syncer_spent_utxos`, populated for whatever tx got mined, not necessarily the operator's) and extracts the operator xonly-pk purely from the first OP_RETURN output, storing `None` if it's missing or malformed [5](#0-4) . This value is persisted via `update_payout_txs_and_payer_operator_xonly_pk` [6](#0-5) .
- `PayoutCheckerTask::run_once` only picks up withdrawals whose `payout_payer_operator_xonly_pk` **exactly equals** its own operator's key via `get_first_unhandled_payout_by_operator_xonly_pk` [7](#0-6) [8](#0-7) . If the stored value is `NULL` (garbled/missing OP_RETURN), **no operator will ever process this withdrawal**, so no kickoff/reimburse is ever initiated for the deposit that funds it.
- `Verifier::is_kickoff_malicious` independently treats a `None` operator pubkey from the payout OP_RETURN as "assuming malicious" [9](#0-8) , and the bridge circuit itself panics if no valid OP_RETURN with a valid xonly pubkey exists on the payout tx [10](#0-9) , so even if the honest operator tried to kickoff anyway using the attacker-mined tx, the proof/verification path rejects it.

Exploit flow: attacker obtains the operator's fully signed payout tx (from the RPC response of `withdraw`/`internal_withdraw`, which is returned only after the operator has already funded and broadcast it, or by observing the tx in the mempool). Attacker constructs a new transaction: input 0 = same withdrawal outpoint with the copied witness, output 0 = byte-identical to the signed output, additional inputs/outputs paid from the attacker's own wallet for fees/change, and output 2 either omitted or containing garbage/another operator's arbitrary public key bytes (no signature is required to write this, since it's just data). Attacker broadcasts with a higher fee and wins the mining race.

### Impact Explanation
- If the OP_RETURN is stripped/garbled: `payout_payer_operator_xonly_pk` becomes `NULL` for that withdrawal. No operator ever claims it as "their" unhandled payout, so no kickoff is ever produced for the corresponding deposit, and the associated move-to-vault UTXO is **permanently frozen** — no operator can be reimbursed for a withdrawal it did fund the intent for but whose actual settlement transaction was hijacked. This matches the Critical category "a move-to-vault UTXO permanently frozen" / "an honest operator permanently unable to be reimbursed."
- If the attacker instead writes an arbitrary (real) operator's public xonly key into the forged OP_RETURN (no private key needed — it is unauthenticated data), that operator's `PayoutCheckerTask` will treat the withdrawal as its own and proceed through kickoff/reimburse for a payout it never actually funded — matching the Critical category "an operator reimbursed for a payout it never funded."
- This is repeatable per-withdrawal and applies to every deposit/operator pair in the system, since the vulnerability is structural (sighash type used for the payout tx never covers the operator-identifying output).

### Likelihood Explanation
No special privileges are required: the attacker just needs a Bitcoin wallet to fund a competing transaction with a higher fee and needs to obtain the already-signed payout transaction, which is trivially available either from the synchronous RPC response of `withdraw`/`internal_withdraw` (returned after the operator's own broadcast) or by watching the mempool. The cost is bounded to fee bumping plus (optionally) fronting the withdrawal amount if the attacker wants to guarantee the correct output 0 is present; if the attacker just wants to grief/freeze without fronting funds, they cannot reuse the signature to change output 0's amount/script (that IS committed), but they can still win the race with the exact same output 0 amount and just mutate output 2, at the cost of matching that output 0 amount from their own funds. This is fully reproducible with `regtest`/`cargo test` and requires no mainnet, no majority hashrate (only faster fee/relay), and no compromise of any keys.

### Recommendation
Sign the payout transaction with a sighash type that commits to all outputs relevant to correctness (e.g. `SIGHASH_ALL` for a fixed number/order of outputs, or explicitly include the anchor and OP_RETURN outputs in the committed message even under `SINGLE|ANYONECANPAY`, e.g. by hashing them into an OP_RETURN/output that IS covered by the single-output commitment, or by requiring the operator identity itself to be part of what's signed by the user or otherwise cryptographically bound instead of freely-writable, unauthenticated OP_RETURN data). At minimum, do not allow arbitrary third parties to spend the withdrawal UTXO and independently choose the OP_RETURN; consider using a script path (rather than key path with a malleable sighash) so all outputs the protocol cares about are covered, or add reconciliation logic so that if a payout tx is mined without a valid attributable OP_RETURN, the deposit's originally-assigned operator can still be credited (e.g., if the payout's output 0 exactly matches what was pre-signed for a specific `withdrawal_index`/operator, credit that operator regardless of the OP_RETURN content).

### Proof of Concept
```rust
// cargo test in core/src/test, regtest only, no live Citrea:
// 1. Set up e2e test harness (see deposit_and_withdraw_e2e.rs helpers) with one operator O.
// 2. Perform a deposit, then trigger a withdrawal for index i, obtaining
//    withdraw_params (signature, input_outpoint, output_script_pubkey, output_amount) as in
//    core/src/test/common/clementine_utils.rs.
// 3. Call operator.internal_withdraw(withdraw_params) and capture the returned signed tx
//    (RawSignedTx) WITHOUT letting it get mined yet (mine_blocks paused).
// 4. From the captured tx, extract input[0] (the withdrawal outpoint + witness) and output[0]
//    (unchanged payout output).
// 5. Craft attacker_tx: input[0] = same outpoint+witness, add attacker-funded fee input(s)/change
//    output(s), output[0] = byte-identical copy, output[2] (OP_RETURN) = garbled/omitted, with a
//    higher fee rate than the operator's tx.
// 6. Broadcast attacker_tx first and mine it (simulating winning the race); then attempt to mine
//    the operator's original tx (it will be rejected as a double-spend).
// 7. Advance blocks past finality depth so verifier/operator sync processes the block.
// 8. Assert:
//    - db.get_payout_info_from_move_txid(move_txid) returns operator_xonly_pk == None
//      (core/src/database/verifier.rs get_payout_info_from_move_txid).
//    - PayoutCheckerTask::run_once() for operator O never returns Ok(true) for this withdrawal
//      (poll for several cycles and assert db.get_handled_payout_kickoff_txid(payout_txid) stays None).
//    - Confirm the move-to-vault UTXO for this deposit is never spent by O's Reimburse tx.
```

### Citations

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

**File:** core/src/operator.rs (L630-689)
```rust
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
            .hex;

        let signed_tx: Transaction = bitcoin::consensus::deserialize(&signed_tx)
            .wrap_err("Failed to deserialize signed withdrawal transaction")?;

        self.rpc
            .send_raw_transaction(&signed_tx)
            .await
            .wrap_err("Failed to send withdrawal transaction")?;
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

**File:** core/src/rpc/parser/operator.rs (L170-187)
```rust
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

**File:** core/src/verifier.rs (L1875-1890)
```rust
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

**File:** core/src/verifier.rs (L2283-2350)
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

**File:** core/src/task/payout_checker.rs (L39-51)
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
```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L206-219)
```rust
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
```
