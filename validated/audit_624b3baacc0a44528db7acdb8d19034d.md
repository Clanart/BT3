### Title
Payout OP_RETURN operator attribution is not covered by the input signature, allowing arbitrary operator credit for a payout it never funded - ([File: core/src/verifier.rs])

### Summary
`create_payout_txhandler` signs the payout transaction's input with a `TapSighashType::SinglePlusAnyoneCanPay` signature, which only commits to the input's own prevout and the output at the *same index* (output[0]) [1](#0-0) . It does not commit to output[2] (the OP_RETURN containing the operator's xonly pubkey) [2](#0-1) . Because `verifier::update_finalized_payouts` derives `payout_payer_operator_xonly_pk` solely from the unsigned OP_RETURN bytes of whatever transaction is found spending the registered withdrawal UTXO [3](#0-2) , anyone who obtains a valid witness for that input (e.g. by observing it in the mempool, since `TransactionType::Payout` uses `FeePayingType::RBF` and can be replaced) can rebroadcast/RBF-replace it with an arbitrary operator's pubkey in the OP_RETURN, crediting that operator for a payout it never authorized.

### Finding Description
The broken binding is: `payout_payer_operator_xonly_pk (DB) == operator who actually funded/authorized payout i`.

- `get_payout_txs_for_withdrawal_utxos` matches **any** transaction that spends `withdrawals.withdrawal_utxo_txid:vout`, with no restriction on which operator broadcast it or who signed it [4](#0-3) .
- `update_finalized_payouts` then reads the operator attribution purely from `parse_op_return_data` on that transaction's OP_RETURN output, with no signature check over that byte range [3](#0-2) .
- The payout transaction itself is built by `create_payout_txhandler`, whose single input is signed key-path with a user-provided Schnorr signature and `TapSighashType::SinglePlusAnyoneCanPay` [1](#0-0) , and `parse_withdrawal_sig_params` enforces that sighash type [2](#0-1) .
- `SIGHASH_SINGLE | ANYONECANPAY` only commits to the input's own prevout and the transaction output at the *same index* as the input (index 0, "user payout output"). It does **not** cover output[1] (anchor) or output[2] (OP_RETURN with operator pubkey), nor does it commit to any other transaction inputs.
- Because `TransactionType::Payout` is routed through `FeePayingType::RBF` [5](#0-4) , an operator's broadcast (unconfirmed) payout transaction is publicly visible in the mempool before confirmation, exposing the witness (signature) for that input. Anyone can lift that same witness, reuse it in a new transaction with the identical output[0] (script_pubkey/amount preserved so the signature stays valid), attach arbitrary other inputs/fees (permitted by ANYONECANPAY), and rewrite output[2]'s OP_RETURN to name a different, uninvolved operator's xonly pubkey, then RBF-replace the original transaction with higher fee.
- Once this replacement transaction confirms, `update_finalized_payouts` records the attacker-chosen operator as `payout_payer_operator_xonly_pk` for withdrawal `i`, even though that operator never called `Operator::withdraw` for `i` and never funded anything.
- None of the listed guards prevent this: `is_kickoff_malicious` only checks that the OP_RETURN operator matches the kickoff's claimed operator (i.e. it validates internal consistency between kickoff and payout attribution, not that the named operator actually authorized/funded the payout) [6](#0-5) . `SECP.verify_schnorr` is only invoked for optimistic payout signature verification against the input/output[0] sighash, never against the OP_RETURN bytes [7](#0-6) .

### Impact Explanation
An arbitrary, uninvolved operator can be attributed as the payer for a withdrawal it never funded. Per the send_asserts / reimbursement flow, the operator matching `payout_payer_operator_xonly_pk` is treated as eligible to claim reimbursement for that withdrawal [8](#0-7) . This is a Critical-severity issue in the given category ("an operator reimbursed for a payout it never funded"), since it lets a party (attacker or a colluding operator) redirect Bitcoin-network-observable attribution data to any operator's key without that operator's cooperation, potentially enabling a false reimbursement claim or, at minimum, corrupting the accounting the entire kickoff/reimburse graph depends on. This is repeatable per withdrawal and per operator (any registered operator xonly_pk can be targeted), since the exploit only requires observing a broadcast Payout tx in mempool and constructing a conflicting higher-fee replacement.

### Likelihood Explanation
The precondition is that a payout transaction for a given withdrawal is broadcast to the mempool before confirmation (an inherent property of the `RBF`-based fee-bumping flow used for `TransactionType::Payout`) and that the OP_RETURN operator field is not covered by the SinglePlusAnyoneCanPay sighash — both of which are structurally guaranteed by the current code (`operator_reimburse.rs`, `tx_sender_queue.rs`). The attacker cost is limited to the fee delta required to RBF-replace the pending transaction, well within reach of an unprivileged actor who can broadcast Bitcoin transactions and pay fees. No key compromise, majority hashrate, or privileged role is required — only observing public mempool/transaction data and reusing the unmodified witness signature.

### Recommendation
Bind the operator attribution to the input signature by covering the OP_RETURN output in the sighash used to authorize the payout input (e.g., use `SIGHASH_ALL` or otherwise commit the operator xonly_pk / full output set to the signed message), or require a `SIGHASH_ALL`-covered value that includes the OP_RETURN output index, so that changing the operator identity invalidates the existing signature and forces a fresh, explicitly-authorized signing round for that specific operator.

### Proof of Concept
```
cargo test:
1. In core/src/builder/transaction/operator_reimburse.rs tests (or a new test), construct a payout tx via create_payout_txhandler with:
   - input_utxo owned by a test user key
   - user_sig with TapSighashType::SinglePlusAnyoneCanPay, valid against output[0]
   - operator_xonly_pk = P (legitimate operator)
2. Compute txhandler.calculate_pubkey_spend_sighash(0, SinglePlusAnyoneCanPay) and assert it matches a sighash computed with operator_xonly_pk replaced by O (attacker's arbitrary key), same output[0]/anchor unchanged -- assert sighashes are IDENTICAL, proving OP_RETURN bytes are not covered.
3. Take the original signed tx, clone it, replace output[2]'s OP_RETURN pushbytes with O's serialized xonly_pk, keep the same witness/signature -> assert the modified tx's key-spend signature still verifies (SECP.verify_schnorr succeeds) against the same sighash.
4. Feed this modified transaction through a BlockCache/verifier::update_finalized_payouts-style flow (using db.update_payout_txs_and_payer_operator_xonly_pk equivalent logic) and assert db.get_payer_xonly_pk_blockhash_and_kickoff_txid_from_deposit_id returns O, despite O never having called Operator::withdraw for that index.
```

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

**File:** core/src/rpc/parser/operator.rs (L180-187)
```rust

    // enforce sighash type here
    if input_signature.sighash_type != TapSighashType::SinglePlusAnyoneCanPay {
        return Err(Status::invalid_argument(format!(
            "Input signature has wrong sighash type, SinglePlusAnyoneCanPay expected, got {}",
            input_signature.sighash_type
        )));
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

**File:** core/src/tx_sender_queue.rs (L92-105)
```rust
            TransactionType::Challenge | TransactionType::Payout => {
                self.insert_try_to_send(
                    dbtx,
                    tx_metadata,
                    signed_tx,
                    FeePayingType::RBF,
                    rbf_info,
                    &[],
                    &[],
                    &[],
                    &[],
                )
                .await
            }
```

**File:** core/src/rpc/aggregator.rs (L1120-1126)
```rust
            let sighash = opt_payout_txhandler
                .calculate_pubkey_spend_sighash(0, input_signature.sighash_type)?;

            let message = Message::from_digest(sighash.to_byte_array());

            SECP.verify_schnorr(&input_signature.signature, &message, &user_xonly_pk)
                .map_err(|_| Status::internal("Invalid signature for optimistic payout tx. Ensure the signature uses SinglePlusAnyoneCanPay sighash type."))?;
```

**File:** core/src/operator.rs (L1275-1295)
```rust
        let (payout_op_xonly_pk_opt, payout_block_hash, payout_txid, deposit_idx) = self
            .db
            .get_payout_info_from_move_txid(Some(&mut dbtx), move_txid)
            .await
            .wrap_err("Failed to get payout info from db during sending asserts.")?
            .ok_or_eyre(format!(
                "Payout info not found in db while sending asserts for move txid: {move_txid}"
            ))?;

        let payout_op_xonly_pk = payout_op_xonly_pk_opt.ok_or_eyre(format!(
            "Payout operator xonly pk not found in payout info DB while sending asserts for deposit move txid: {move_txid}"
        ))?;

        tracing::info!("Sending asserts for deposit_idx: {deposit_idx:?}");

        if payout_op_xonly_pk != kickoff_data.operator_xonly_pk {
            return Err(eyre::eyre!(
                "Payout operator xonly pk does not match kickoff operator xonly pk in send_asserts"
            )
            .into());
        }
```
