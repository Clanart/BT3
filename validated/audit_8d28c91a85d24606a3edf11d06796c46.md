### Title
Payout OP_RETURN operator attribution is unauthenticated and forgeable via SIGHASH_SINGLE|ANYONECANPAY malleability - ([File: core/src/verifier.rs])

### Summary
The payout transaction's signed portion only covers input 0 and output 0 (`SinglePlusAnyoneCanPay`), so the OP_RETURN output carrying `operator_xonly_pk` is never covered by any signature. Anyone who can reconstruct a valid payout transaction (same withdrawal input, same user-destination output) can attach an arbitrary OP_RETURN naming any operator as the "payer," and `update_finalized_payouts` blindly records that forged attribution as ground truth.

### Finding Description
The claimed binding is: `operator_xonly_pk recorded in withdrawals.payout_payer_operator_xonly_pk for withdrawal i == the xonly_pk of the party that actually funded/broadcast payout tx i`.

`parse_withdrawal_sig_params` enforces that the user's `input_signature` for the withdrawal UTXO must use `TapSighashType::SinglePlusAnyoneCanPay` [1](#0-0) . Under `SIGHASH_SINGLE | ANYONECANPAY`, the signature commits only to input 0 and the output at the *same index* (the user payout output); it does **not** cover any other input or output. `create_payout_txhandler` builds the payout with exactly this shape: input 0 = withdrawal UTXO (user-signed), output 0 = user payout, output 1 = ephemeral anchor, output 2 = OP_RETURN with `operator_xonly_pk` [2](#0-1) . Crucially, the OP_RETURN output (and the anchor) are unsigned/malleable — no operator signature, funding input, or any cryptographic commitment ties the OP_RETURN pubkey to whoever actually pays fees/broadcasts the transaction (fee payment is deferred entirely to CPFP off the anchor output).

`Verifier::update_finalized_payouts` then trusts this OP_RETURN unconditionally: it scans the confirmed payout tx, extracts the first OP_RETURN, and parses it as `operator_xonly_pk` with no check that it corresponds to a real operator, or to the party that funded the transaction: `let operator_xonly_pk = op_return_output.and_then(...).and_then(|bytes| XOnlyPublicKey::from_slice(bytes).ok());` [3](#0-2) . This value is persisted as ground truth via `update_payout_txs_and_payer_operator_xonly_pk` [4](#0-3) .

Downstream, this forged value is trusted absolutely: `PayoutCheckerTask` polls `get_first_unhandled_payout_by_operator_xonly_pk` for the operator's own key and, if a match is found, automatically drives `handle_finalized_payout`/kickoff to claim reimbursement [5](#0-4) ; and `validate_payer_is_operator` simply compares the stored `payer_xonly_pk` to `self.signer.xonly_public_key` with no independent proof of funding [6](#0-5) .

Exploit flow: an attacker (who is simply a withdrawing user or anyone who has seen the public withdrawal params, which include `input_signature`/`input_outpoint`/`output_script_pubkey`/`output_amount` submitted to Citrea's `withdraw()`) constructs their own valid payout transaction — same input 0, same output 0 (satisfying "correct destination/amount") — but appends an OP_RETURN naming an arbitrary, uninvolved operator's `xonly_pk`. If this version confirms (e.g., by outbidding a competing operator's version in a fee race, since only one spend of the withdrawal UTXO can ever confirm), `update_finalized_payouts` will record that operator as payer, and that operator's automation will proceed to claim collateral-backed reimbursement for a payout it never funded — while any honest operator who actually intended to front it is permanently locked out, since the withdrawal UTXO is now spent and the attribution slot for that index is already filled.

None of the existing guards catch this: `is_kickoff_malicious`, `send_asserts`, and `validate_payer_is_operator` all just compare the stored (forged) `operator_xonly_pk` against the kickoff's own `operator_xonly_pk` — they never verify that the credited operator's funds actually paid for the payout tx, because no such cryptographic binding exists anywhere in the protocol.

### Impact Explanation
This directly matches two Critical categories: "an operator reimbursed for a payout it never funded" and "an honest operator permanently unable to be reimbursed." The forged-pubkey operator receives BTC reimbursement from the move-to-vault UTXO for zero real expenditure, while the operator who actually intended (or even actually did) front the withdrawal can be denied credit entirely once the attacker's version confirms first. This is repeatable for every withdrawal, against every operator, since the OP_RETURN is never authenticated for any payout.

### Likelihood Explanation
The attacker capability set explicitly grants: choosing withdrawal UTXO bytes, the Schnorr signature and its sighash flag, and the OP_RETURN content, and calling `withdraw` on the Citrea Bridge contract — i.e., the withdrawal params (including the SIGHASH_SINGLE|ANYONECANPAY user signature) become attacker-visible/public once submitted. Constructing a competing payout transaction requires no operator key, no collateral, and only fee-bumping capital comparable to normal transaction fees. This is realistically feasible with any mempool race or first-broadcast advantage.

### Recommendation
Bind the OP_RETURN operator attribution cryptographically to the actual funder, e.g., require the operator to co-sign the payout transaction (covering all outputs, not just output 0) with their own key, or otherwise require an on-chain/verifiable proof (e.g., an operator-only presigned template or a signature over the full payout tx by `operator_xonly_pk`) before `update_finalized_payouts`/`validate_payer_is_operator` treat the OP_RETURN value as authoritative. At minimum, reject payout confirmations whose OP_RETURN pubkey cannot be shown to correspond to whoever funded/broadcast the anchor-spending CPFP transaction.

### Proof of Concept
```
cargo test -p clementine-core payout_op_return_forgery_attribution -- --nocapture
```
Test plan:
1. Set up two registered operators A and B with collateral (`insert_operator_if_not_exists` for both).
2. Complete a deposit and citrea withdrawal setup so a `withdrawal_utxo` and its `SinglePlusAnyoneCanPay` `input_signature` are available (as done in `core/src/test/deposit_and_withdraw_e2e.rs`).
3. Construct payout tx #1: input = withdrawal UTXO w/ user sig, output 0 = correct user payout, OP_RETURN = Operator A's xonly_pk (Operator A actually would fund/broadcast this).
4. As "attacker" with no relation to any operator, construct payout tx #2 with the *same* input/output 0 but OP_RETURN = Operator B's xonly_pk, and give it a higher fee via the anchor/CPFP so it confirms instead of tx #1.
5. Mine the block containing tx #2. Run `update_finalized_payouts`.
6. Assert on both sides of the binding: `db.get_payout_info_from_move_txid(...).0 == Some(B.xonly_pk)` (forged attribution accepted) while Operator A's `validate_payer_is_operator`/`handle_finalized_payout` for the same deposit id return an error or `None` (A permanently denied), and Operator B's `PayoutCheckerTask::run_once` proceeds to `handle_finalized_payout` and queues a real kickoff/reimbursement despite B never funding or broadcasting anything — demonstrating the credited-payer binding is broken.

### Citations

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
