### Title
Payout attribution (`payout_payer_operator_xonly_pk`) is malleable because `SIGHASH_SINGLE|AnyoneCanPay` only commits to the withdrawal input and the user output, letting anyone rewrite the OP_RETURN operator credit and add fee inputs to win the confirmation race - ([File: core/src/builder/transaction/operator_reimburse.rs])

### Summary
`create_payout_txhandler` puts the operator's identity in an OP_RETURN output that is index 2, while the only signature attached to the payout's single input is enforced to be `SinglePlusAnyoneCanPay` [1](#0-0) . Under BIP-341, `SIGHASH_SINGLE` only commits to the output whose index equals the signing input's index (index 0, the user payout), and `ANYONECANPAY` excludes all other inputs from commitment. This leaves the anchor output and the OP_RETURN (operator xonly pk) completely unauthenticated, and permits arbitrary extra fee-paying inputs to be added by anyone who has seen the signature.

### Finding Description
The binding claimed by attribution is:
`payout_payer_operator_xonly_pk (recorded in DB) == xonly pk of the operator who signed/broadcast/funded the confirmed payout tx`.

The payout transaction is built by: [2](#0-1) 

with the operator identity placed only in the third output (`op_return_txout`), and the sole authorizing signature required to be `SinglePlusAnyoneCanPay`: [3](#0-2) 

Because this sighash flag combination only commits to input 0 and output 0 (and to the current input's own outpoint/scriptPubKey/amount), an attacker who observes the honest operator's broadcast tx in the mempool can extract `(input_outpoint, input witness signature, output0)` unchanged, and construct a *different* transaction that:
- spends the identical withdrawal UTXO with the identical (still valid) Schnorr signature,
- keeps output 0 (user payout) identical (it is committed),
- swaps output 2 (OP_RETURN) to any operator xonly pk of the attacker's choosing (real operator pubkeys are public, obtainable via `fetch_operator_keys`),
- adds the attacker's own extra input to pay a higher absolute fee (permitted because `ANYONECANPAY` does not restrict additional inputs), enabling a valid BIP-125/V3 replacement of the honest operator's in-flight, not-yet-confirmed transaction.

Whichever variant confirms first is trusted blindly by the finalized-payout syncer, which parses the OP_RETURN with no signature/commitment check at all: [4](#0-3) 
and writes it straight into `withdrawals.payout_payer_operator_xonly_pk`: [5](#0-4) 

This DB column is later trusted as ground truth for automated reimbursement decisions. An operator's own automation looks up its unhandled payouts keyed on this exact column: [6](#0-5) 
and `validate_payer_is_operator` / `get_reimbursement_txs` will proceed to build and send Round/Kickoff/Reimburse transactions for any deposit where the DB says "you are the payer", with no independent verification that this operator actually broadcast or funded the confirmed payout tx: [7](#0-6) [8](#0-7) 

`is_kickoff_malicious` (verifier-side guard invoked only when *that* operator actually submits a kickoff) checks `operator_xonly_pk == kickoff_data.operator_xonly_pk` [9](#0-8) , but this only prevents a *different* operator's kickoff from being accepted for the corrupted deposit — it does nothing to stop the falsely-credited operator from believing (via its own automation) that it is entitled to reimbursement and initiating the kickoff/reimburse flow itself, nor does it restore correct credit to the honest fee-paying operator whose broadcast lost the replacement race.

### Impact Explanation
- The honest operator who actually funded/broadcast the payout permanently loses attribution for that withdrawal (their `get_first_unhandled_payout_by_operator_xonly_pk` query will never find it, since the row now points to a different pubkey), i.e. **an honest operator permanently unable to be reimbursed** for a payout it genuinely funded.
- The framed operator (any real operator whose public xonly pk the attacker copied into the OP_RETURN) has its automation pick up an unhandled payout it never funded and will attempt to claim reimbursement for a payout it did not pay — **an operator reimbursed for a payout it never funded**.
- This is repeatable for every withdrawal where a payout tx is still unconfirmed and observable in the mempool, and is not tied to any specific deposit/operator pair — any operator's pubkey can be substituted for any withdrawal, so the blast radius spans all withdrawals and all operators.
- Matches the Critical severity bucket explicitly listed ("an operator reimbursed for a payout it never funded", "an honest operator permanently unable to be reimbursed").

### Likelihood Explanation
- Requires no privileged role: the attacker only needs to observe transactions in the public Bitcoin mempool (or via the aggregator's public `withdraw` gRPC, which returns operator responses including the built transaction) and be able to fund a competing transaction's fee.
- The vulnerability is a direct, structural consequence of using `SinglePlusAnyoneCanPay` for a single-input payout tx whose only authenticity anchor for operator attribution is an unsigned OP_RETURN output — this is not an edge case, it is the normal shape of every payout transaction.
- Attacker cost is limited to normal Bitcoin transaction fees required to win an RBF replacement race against the honest operator's fee-bump; no special hashrate, no key compromise, no TLS interception needed.
- Feasible and repeatable per withdrawal; the only timing constraint is racing the honest operator's confirmation, which is inherent to any RBF/mempool-based fee bumping and does not require majority hashrate.

### Recommendation
Bind the operator identity to the signature that authorizes the payout. Concrete options:
- Require the withdrawer's signature to use `SIGHASH_ALL` (or `AllPlusAnyoneCanPay` if multiple inputs must remain addable) so all outputs, including the OP_RETURN, are committed by the signature, preventing any output substitution.
- Alternatively, have the aggregator/verifier require that the operator's own xonly pk be part of the data the withdrawer signs off-chain (e.g., include the intended operator pk in the pre-image that the user acknowledges before handing out the `SinglePlusAnyoneCanPay` signature), and validate at confirmation time that the confirmed OP_RETURN matches what was actually pre-committed for that withdrawal, rejecting/ignoring divergent OP_RETURN values instead of blindly trusting on-chain bytes in `update_finalized_payouts`.

### Proof of Concept
```rust
// cargo test race_payout_attribution_theft (regtest, no mainnet, no live Citrea)
// 1. Set up a withdrawal (withdrawal_id, in_outpoint, in_signature with SinglePlusAnyoneCanPay,
//    out_script_pubkey, out_amount) as in core/src/test/manual_reimbursement.rs.
// 2. Call operator0.withdraw(...) to get payout_tx_honest signed for operator0.xonly_pk,
//    broadcast it to regtest mempool but DO NOT mine it.
// 3. As "attacker" (no operator/verifier role), build payout_tx_attacker manually:
//    - same input (in_outpoint) and same witness (extracted from payout_tx_honest),
//    - identical output[0] (payout to user),
//    - modified output[2] OP_RETURN containing operator1.xonly_pk (a different, real operator),
//    - add an attacker-funded extra input + higher-fee anchor to win RBF.
// 4. Broadcast payout_tx_attacker; mine it (replacing payout_tx_honest).
// 5. Run the block syncer / update_finalized_payouts logic and assert:
//    assert_eq!(db.get_payout_info_from_move_txid(..).0, Some(operator1.xonly_pk));  // NOT operator0
//    assert_ne!(db.get_payout_info_from_move_txid(..).0, Some(operator0.xonly_pk));
// 6. Confirm operator1.get_first_unhandled_payout_by_operator_xonly_pk(operator1.xonly_pk)
//    now returns this withdrawal despite operator1 never funding/broadcasting it,
//    while operator0's own query returns nothing for this withdrawal.
```

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

**File:** core/src/operator.rs (L2098-2150)
```rust
    pub async fn get_reimbursement_txs(
        &self,
        deposit_outpoint: OutPoint,
    ) -> Result<Vec<(TransactionType, Transaction)>, BridgeError> {
        let mut dbtx = self.db.begin_transaction().await?;
        // first check if the deposit is in the database
        let (deposit_id, mut deposit_data) = self
            .db
            .get_deposit_data(Some(&mut dbtx), deposit_outpoint)
            .await?
            .ok_or_eyre(format!(
                "Deposit data not found for the requested deposit outpoint: {deposit_outpoint:?}, make sure you send the deposit outpoint, not the move txid."
            ))?;

        tracing::info!(
            "Deposit data found for the requested deposit outpoint: {deposit_outpoint:?}, deposit id: {deposit_id:?}",
        );

        // validate payer is operator and get payer xonly pk, payout blockhash and kickoff txid
        let (payout_blockhash, kickoff_txid) = self
            .validate_payer_is_operator(Some(&mut dbtx), deposit_id)
            .await?;

        let mut current_round_idx = self.db.get_current_round_index(Some(&mut dbtx)).await?;

        let mut txs_to_send: Vec<(TransactionType, Transaction)>;

        loop {
            txs_to_send = self
                .get_next_txs_to_send(
                    Some(&mut dbtx),
                    &mut deposit_data,
                    payout_blockhash,
                    kickoff_txid,
                    current_round_idx,
                )
                .await?;
            if txs_to_send.is_empty() {
                // if no txs were returned, and we advanced the round in the db, ask for the next txs again
                // with the new round index
                let round_idx_after_operations =
                    self.db.get_current_round_index(Some(&mut dbtx)).await?;
                if round_idx_after_operations != current_round_idx {
                    current_round_idx = round_idx_after_operations;
                    continue;
                }
            }
            break;
        }

        dbtx.commit().await?;
        Ok(txs_to_send)
    }
```
