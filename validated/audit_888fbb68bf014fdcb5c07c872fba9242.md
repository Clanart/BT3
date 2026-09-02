### Title
Unauthenticated OP_RETURN operator attribution in `update_finalized_payouts` lets an attacker (via SIGHASH_SINGLE|ANYONECANPAY payout construction) misattribute a payout to an arbitrary operator, causing wrongful reimbursement or permanent reimbursement denial - ([File: core/src/verifier.rs])

### Summary
`Verifier::update_finalized_payouts` attributes a payout's "payer operator" solely by parsing an unsigned OP_RETURN output of whichever transaction first confirms spending the withdrawal UTXO, with no check that the embedded xonly_pk corresponds to whoever actually funded/broadcast that transaction. Because the payout tx's user signature uses `SinglePlusAnyoneCanPay` (covering only the input and output index 0), the withdrawer/attacker who owns and signs the withdrawal UTXO can build and broadcast their own transaction that satisfies the same signed output but embeds any operator's public (non-secret) xonly_pk in the OP_RETURN, hijacking DB attribution.

### Finding Description
Binding claimed: `withdrawals.payout_payer_operator_xonly_pk == the operator whose own funds actually financed the payout output for that withdrawal idx`.

Trace:
1. `Operator::withdraw` builds the payout via `create_payout_txhandler` (`core/src/builder/transaction/operator_reimburse.rs:407-436`), whose inputs are: `[input_utxo (user's own withdrawal UTXO)] -> [user payout output(0), anchor(1), OP_RETURN(operator xonly_pk)(2)]`. The witness is `set_p2tr_key_spend_witness(&user_sig, 0)`. [1](#0-0) 
2. The user's signature type is `TapSighashType::SinglePlusAnyoneCanPay` (`WithdrawParams` doc comment and `Operator::withdraw` verify call), which commits only to input 0 and output at the same index (output 0). The anchor and OP_RETURN outputs, and any additional funding inputs, are outside the signed message. [2](#0-1) 
3. `Operator::withdraw` funds the transaction via bitcoind `fund_raw_transaction`/`sign_raw_transaction_with_wallet`/`send_raw_transaction` using its own wallet - i.e. whoever holds the exact `(in_signature, in_outpoint, output_txout)` triple can independently perform the same construction, substituting any `operator_xonly_pk` in the OP_RETURN and any funding source, since none of that is bound by the user's signature. [3](#0-2) 
4. `Verifier::update_finalized_payouts` determines the payer purely by parsing the first OP_RETURN of whichever transaction is recorded as spending the withdrawal UTXO, with no check on who funded it: [4](#0-3) 
5. That "whichever transaction spends the withdrawal UTXO" comes from `get_payout_txs_for_withdrawal_utxos`, a simple join on `bitcoin_syncer_spent_utxos`, i.e. a first-confirmed-wins race with no funder authentication: [5](#0-4) 
6. The result is written unconditionally to `payout_payer_operator_xonly_pk`: [6](#0-5) 
7. `PayoutCheckerTask::run_once` later looks up unhandled payouts strictly by matching `payout_payer_operator_xonly_pk` to the local operator's own key and, on a match, drives `handle_finalized_payout` -> kickoff -> reimbursement path: [7](#0-6) 
8. `Verifier::is_kickoff_malicious` only cross-checks the DB-recorded OP_RETURN pubkey against `kickoff_data.operator_xonly_pk` - since the DB itself was poisoned by the attacker's decoy tx, this check passes for the impersonated operator's genuine kickoff: [8](#0-7) 

Root cause: OP_RETURN operator attribution is treated as authenticated protocol data even though it lies entirely outside the `SinglePlusAnyoneCanPay` sighash coverage and is not tied to who supplied the funding inputs of the winning transaction. No guard (`is_deposit_valid`, `is_profitable`, `SECP.verify_schnorr` on the user signature, `is_kickoff_malicious`, or a DB uniqueness constraint) validates that the credited operator's own key/wallet actually funded the confirmed payout.

### Impact Explanation
The attacker (the withdrawer themselves, fully in control of the withdrawal UTXO, the Schnorr signature, and its sighash flag per the threat model) can, without any operator cooperation, construct and broadcast their own transaction spending their withdrawal UTXO that satisfies the signed output but stamps an arbitrary, uninvolved operator's public xonly_pk into the OP_RETURN. Consequences, matching listed Critical impacts:
- The impersonated (innocent) operator's `PayoutCheckerTask`/automation will treat this as its own fronted payout and drive a kickoff/Reimburse claim for money it never paid - "an operator reimbursed for a payout it never funded."
- The real, honest paying operator's later payout transaction becomes a losing double-spend (the withdrawal UTXO is already spent), so the withdrawal row is already permanently bound to the decoy txid/wrong operator - "an honest operator permanently unable to be reimbursed."
- This is repeatable per withdrawal and requires no majority hashrate, key compromise, or privileged role - only winning a one-block confirmation race against the legitimate operator's payout broadcast.

### Likelihood Explanation
The attacker needs only to be the party who calls `withdraw()` on the Citrea bridge contract (fully permitted per the threat model), own the dust withdrawal UTXO, sign it with `SinglePlusAnyoneCanPay`, and fund/broadcast a competing transaction with a forged OP_RETURN before the intended operator's transaction confirms. Cost is limited to the bridge withdrawal amount plus fees (which the attacker pays to themselves as the withdrawal output) and a fee-bump race; no special deployment configuration is required, and it is repeatable across every withdrawal.

### Recommendation
Bind the payer attribution to a fact that is actually signed/authenticated, e.g.: require the operator's OP_RETURN pubkey to be committed by the withdrawer's signature (extend the sighash to cover the OP_RETURN output, or require SIGHASH_ALL/`Default` instead of `SinglePlusAnyoneCanPay` for the payout tx), or independently verify that the winning payout transaction's *additional* funding inputs actually originate from the operator whose key is claimed in the OP_RETURN (e.g. checking against a registered operator wallet/reimbursement address) before writing `payout_payer_operator_xonly_pk`.

### Proof of Concept
```rust
#[tokio::test]
async fn forged_op_return_hijacks_payout_attribution() {
    // 1. Setup two operators A (intended payer) and B (innocent, uninvolved).
    // 2. Attacker creates a withdrawal dust UTXO signed with SinglePlusAnyoneCanPay
    //    (as in generate_withdrawal_transaction_and_signature), fixing output[0].
    // 3. Attacker (not operator B) constructs their own payout tx reusing the same
    //    signed input/output[0], with a new OP_RETURN embedding operator B's
    //    xonly_pk (copied from B's known public config), funds it themselves, and
    //    broadcasts it before operator A's real payout tx confirms.
    // 4. Mine blocks until finality; run verifier's block sync
    //    (update_finalized_payouts).
    // 5. Assert: db.get_payout_info_from_move_txid(...) returns operator B's
    //    xonly_pk as payout_payer_operator_xonly_pk, even though B never
    //    constructed/funded/broadcast any transaction.
    // 6. Assert: db.get_first_unhandled_payout_by_operator_xonly_pk(B) returns
    //    Some(row), proving B's PayoutCheckerTask would pick up and attempt to
    //    claim reimbursement for a payout it never made, while A's legitimate
    //    payout tx fails as a double-spend and can never be attributed.
}
```

### Citations

**File:** core/src/builder/transaction/operator_reimburse.rs (L413-436)
```rust
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

**File:** core/src/operator.rs (L620-691)
```rust
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

        Ok(signed_tx)
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

**File:** core/src/verifier.rs (L2311-2342)
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

**File:** core/src/database/verifier.rs (L226-251)
```rust
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
