### Title
Malleable OP_RETURN output in `payout_tx` under `SinglePlusAnyoneCanPay` permanently freezes the move-to-vault UTXO - ([File: core/src/builder/transaction/operator_reimburse.rs])

### Summary
`create_payout_txhandler` signs the payout transaction's sole input with the user's `TapSighashType::SinglePlusAnyoneCanPay` signature, which under BIP-341/BIP-143 semantics commits only to input 0 and the output at the *same index* (output 0). The anchor output (index 1) and the operator-attribution OP_RETURN (index 2) are unauthenticated and can be freely rewritten by anyone who observes the signed input in the mempool. An attacker can rebroadcast a variant of the tx that pays the withdrawal correctly (output 0 unchanged, still validly signed) but with a corrupted or missing OP_RETURN, causing `Verifier::update_finalized_payouts` to record `payout_payer_operator_xonly_pk = NULL` for that withdrawal.

### Finding Description
Binding claimed broken: `withdrawals.payout_payer_operator_xonly_pk == O` (the operator whose reimbursement path/duties should be tied to this move-to-vault UTXO), for the withdrawal actually paid out by output 0 of the mined payout tx.

- `create_payout_txhandler` builds output 0 (user payout), output 1 (anchor), output 2 (`OP_RETURN` push of `operator_xonly_pk.serialize()`), and signs only input 0 with the caller-supplied `user_sig`: [1](#0-0) .
- The signature's sighash type is enforced to be `SinglePlusAnyoneCanPay` both at the aggregator/gRPC parsing layer and at the operator: [2](#0-1)  and verified via `SECP.verify_schnorr` against the sighash computed for that specific tx template: [3](#0-2) .
- `calculate_script_spend_sighash`/taproot key-spend sighash computation for `SinglePlusAnyoneCanPay` only commits `Prevouts::One` (this input) and, per BIP-341 semantics, only the output at the same index as the input (output 0): [4](#0-3) . Outputs 1 and 2 are not covered by the signature and `ANYONECANPAY` also lets any party add additional funding inputs (as the operator itself does via `fund_raw_transaction`'s `add_inputs`): [5](#0-4) .
- Consequently, an attacker who observes O's broadcast/mempool payout tx can construct an alternative transaction that reuses O's signed input and preserves the committed output 0 (same payout amount/script), while substituting output 2's OP_RETURN with a push that is not exactly 32 bytes (or removing it). This new transaction is a fully valid spend of the withdrawal UTXO (the user is paid identically).
- When this attacker tx is mined first, `Verifier::update_finalized_payouts` finds the OP_RETURN, calls `parse_op_return_data` then `XOnlyPublicKey::from_slice`; a non-32-byte push causes the `and_then` chain to yield `None`, and the DB column is set NULL: [6](#0-5) , persisted via `update_payout_txs_and_payer_operator_xonly_pk`: [7](#0-6) .
- `is_kickoff_malicious` treats a `None` stored operator pk as automatically malicious for *any* operator's kickoff on this move_txid, since the `None` check occurs before any comparison to `kickoff_data.operator_xonly_pk`: [8](#0-7) .
- `get_first_unhandled_payout_by_operator_xonly_pk` filters `WHERE w.payout_payer_operator_xonly_pk = $1`, so a `NULL` value is never matched to O (or anyone), meaning `PayoutCheckerTask::run_once` never dispatches `handle_finalized_payout`/reimbursement duties for this withdrawal: [9](#0-8)  and [10](#0-9) .

No existing guard intercepts this: `is_deposit_valid`/`SPV::verify` only validate the deposit/move tx, not the payout tx's non-committed outputs; there is no on-chain or off-chain check that the OP_RETURN output actually matches what any specific operator constructed, and the mempool-capture/rebroadcast requires no privileged role, key, or majority hashrate — only observing a public mempool transaction and paying fees to get the substitute tx mined first.

### Impact Explanation
The move-to-vault UTXO for the affected deposit becomes permanently frozen: `is_kickoff_malicious` returns `true` for every operator's kickoff attempt on this `move_txid` (since the stored payer pk is `NULL` regardless of which operator's kickoff is being checked), and `PayoutCheckerTask` for the operator who actually intended/queued the payout never picks up the withdrawal because the DB row is not attributed to any `operator_xonly_pk`. This matches the Critical category "a move-to-vault UTXO permanently frozen" / "an honest operator permanently unable to be reimbursed." The blast radius is per-deposit: each occurrence permanently strands exactly one deposit's move-to-vault funds, and the attack is repeatable across every future withdrawal/payout that uses this transaction construction, independent of which operator processes it.

### Likelihood Explanation
Preconditions are minimal and match the unprivileged attacker model: the attacker only needs to observe a broadcast/mempool payout transaction (public), and construct+broadcast an alternative transaction reusing the same signed input and unchanged output 0, with a corrupted OP_RETURN at output 2, plus enough additional attacker-funded inputs to cover fees (and to replace the value the operator would have added via `add_inputs`, since output 0's amount is fixed by the committed sighash but any additional inputs are unauthenticated under `ANYONECANPAY`). This requires no verifier/operator/aggregator privileges, no key compromise, and no majority hashrate — merely fee competition to get mined first, which is standard, low-cost Bitcoin transaction relay behavior.

### Recommendation
Do not rely on an `ANYONECANPAY`/`SINGLE`-only signature for a transaction whose non-committed outputs carry security-critical data. Either (a) change the payout tx's sighash type to a mode that commits to all outputs (e.g. `SIGHASH_ALL`) for the operator-attribution OP_RETURN output, or (b) have the operator additionally co-sign/commit the OP_RETURN output content (e.g. via a second, operator-controlled input/signature covering the OP_RETURN), or (c) require the payout tx to be constructed and pre-registered by an operator in the verifier DB prior to attributing it, validating that the on-chain mined tx exactly matches the pre-registered template (including OP_RETURN bytes) rather than trusting whatever OP_RETURN happens to be present in the transaction that spends the withdrawal UTXO.

### Proof of Concept
```
cargo test in core/src/verifier.rs test module (extend existing update_finalized_payouts tests):
1. Create a valid withdrawal UTXO and user SinglePlusAnyoneCanPay signature via
   generate_withdrawal_transaction_and_signature (core/src/test/common/setup_utils.rs).
2. Build the honest operator O's payout_tx via create_payout_txhandler with a correct
   32-byte OP_RETURN (O's xonly pk).
3. Build an "attacker" variant transaction: reuse the same signed input (witness) and
   output 0, add an attacker-funded fee input, and set output 2's OP_RETURN push to 20
   or 40 bytes (not 32).
4. Mine the attacker variant instead of O's original tx.
5. Call Verifier::update_finalized_payouts (or the full sync path) on that block.
6. Assert: db.get_payout_info_from_move_txid(...).0 == None  (payout_payer_operator_xonly_pk is NULL)
   — the equality payout_payer_operator_xonly_pk == O is broken.
7. Assert: for O (and for every other configured operator), is_kickoff_malicious(...)
   returns true for a kickoff on this move_txid's KickoffData — i.e., the UTXO can never
   be honestly reimbursed by any operator.
8. Assert: db.get_first_unhandled_payout_by_operator_xonly_pk(O) returns None for this
   withdrawal idx, confirming PayoutCheckerTask never surfaces it to O.
```

### Citations

**File:** core/src/builder/transaction/operator_reimburse.rs (L414-436)
```rust
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

**File:** core/src/rpc/parser/operator.rs (L174-187)
```rust
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

**File:** core/src/operator.rs (L652-674)
```rust
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

**File:** core/src/builder/transaction/txhandler.rs (L315-322)
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

**File:** core/src/verifier.rs (L1871-1890)
```rust
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

**File:** core/src/task/payout_checker.rs (L39-54)
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
```
