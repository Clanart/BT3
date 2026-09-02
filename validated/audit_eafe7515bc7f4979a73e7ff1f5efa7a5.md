### Title
Payout attribution can be redirected via OP_RETURN output that is not covered by `in_signature`'s sighash commitment - ([File: core/src/builder/transaction/operator_reimburse.rs])

### Summary
`create_payout_txhandler` builds the `payout_tx` with a single signed input (the pre-committed withdrawal UTXO, spent with the externally supplied `in_signature`) and three outputs: the user payout, an anchor, and an OP_RETURN carrying `operator_xonly_pk`. Nothing in `Operator::withdraw` or `create_payout_txhandler` restricts or checks the sighash flag embedded in `in_signature`, so whoever supplies that signature (the withdrawing party, who is an unprivileged actor per the threat model) can choose a sighash type that does not commit to the OP_RETURN output, allowing anyone holding a copy of the input+output-0 pair and the signature to swap in an arbitrary `operator_xonly_pk` and race it into a block ahead of the honest variant.

### Finding Description
The binding this system relies on is:
`payout_payer_operator_xonly_pk` (stored by `Verifier::update_finalized_payouts`) == `self.signer.xonly_public_key` of the operator who actually funded/owns the withdrawal UTXO input that pays output 0.

`create_payout_txhandler` (`core/src/builder/transaction/operator_reimburse.rs:407-436`) constructs the payout transaction with exactly one input (the withdrawal UTXO, `SpendPath::KeySpend`) and adds the OP_RETURN as a completely independent, unauthenticated third output: [1](#0-0) 

The witness for that single input is set directly from the externally supplied `user_sig`/`in_signature` with no re-derivation or restriction of its sighash type: [2](#0-1) 

`Operator::withdraw` (`core/src/operator.rs:560-627`) only checks that `input_utxo.outpoint` equals the Citrea-committed `withdrawal_utxo` and that the payment is profitable; it performs no check restricting the sighash flag of `in_signature`: [3](#0-2) 

On the verifier side, `update_finalized_payouts` derives `operator_xonly_pk` purely from whatever OP_RETURN bytes happen to be present in the transaction that is found to spend the tracked withdrawal UTXO (identified independent of OP_RETURN content, purely via `bitcoin_syncer_spent_utxos`): [4](#0-3) [5](#0-4) 

This value is later trusted as-is by `get_first_unhandled_payout_by_operator_xonly_pk`, which is queried by each operator's own `PayoutCheckerTask` against its own `self.signer.xonly_public_key`: [6](#0-5) [7](#0-6) 

Because the OP_RETURN output is not part of the outputs the single input's key-spend signature is required to commit to when a non-`SIGHASH_ALL`/`SIGHASH_DEFAULT` flag is used, and because the codebase does not enforce a specific sighash type on `in_signature`, an attacker holding a valid signature and the withdrawal UTXO/output-0 pair (which, per the threat model, an unprivileged withdrawing party legitimately possesses) can construct a second, otherwise-identical payout transaction whose OP_RETURN names an arbitrary XOnlyPublicKey, and get it mined instead of/ahead of the honest operator's broadcast. The subsequent `is_kickoff_malicious` check (`core/src/verifier.rs:1857-1915`) only cross-checks the stored OP_RETURN pubkey against whichever operator later submits a matching kickoff — it does not verify that the OP_RETURN pubkey corresponds to whoever actually funded the input, so it does not catch this substitution.

### Impact Explanation
If the forged OP_RETURN names a real, distinct operator's public key, that operator's own `PayoutCheckerTask` will pick up the payout via `get_first_unhandled_payout_by_operator_xonly_pk`, proceed through `handle_finalized_payout`, submit a kickoff, pass `is_kickoff_malicious` (since the stored pubkey now matches the kickoff submitter), and ultimately be reimbursed from the round transaction — for a withdrawal it never funded. Simultaneously, the operator who genuinely fronted the funds is never attributed and can never satisfy `get_first_unhandled_payout_by_operator_xonly_pk` for its own key, permanently losing the ability to claim reimbursement for the BTC it fronted. This matches the "Critical: an operator reimbursed for a payout it never funded" / "an honest operator permanently unable to be reimbursed" categories, and is repeatable for every withdrawal where the withdrawing party is willing to sign with a non-output-2-covering sighash type.

### Likelihood Explanation
The precondition is that the signature supplied as `in_signature` uses a sighash flag that does not commit to the OP_RETURN output (e.g., `SIGHASH_SINGLE` or `SIGHASH_NONE`, possibly combined with `ANYONECANPAY`). Nothing in `Operator::withdraw` or `create_payout_txhandler` rejects such signatures, and the threat model explicitly grants the attacker control of "a Schnorr signature and its sighash flag." No additional capital beyond normal transaction fees is required beyond what is already needed for the legitimate payout, and the race is a standard "get my transaction mined instead of/ahead of the other one" contest available to anyone monitoring the mempool.

### Recommendation
Enforce that `in_signature` uses `SIGHASH_DEFAULT`/`SIGHASH_ALL` (rejecting any other sighash type) in `Operator::withdraw`/`create_payout_txhandler` before constructing or broadcasting the payout transaction, so the signature commits to every output of the transaction, including the OP_RETURN carrying `operator_xonly_pk`. Additionally, consider cryptographically binding the OP_RETURN operator identity to the actual funding input (e.g., by having the operator co-sign or by deriving attribution from which key/address funded the input) rather than trusting an unauthenticated OP_RETURN push.

### Proof of Concept
```
cargo test payout_op_return_attribution_forgery
```
Plan:
1. Set up a deposit and withdrawal so a `withdrawal_utxo` exists and two operators, A (true funder) and B (attacker-named), are registered with known `xonly_public_key`s.
2. Have the withdrawing party sign `in_signature` for the payout with `SIGHASH_SINGLE` (or `SIGHASH_NONE`) instead of `SIGHASH_ALL`.
3. Construct `payout_tx_v1` via `create_payout_txhandler` with `operator_xonly_pk = A`'s key and broadcast it (unconfirmed, in mempool).
4. Construct `payout_tx_v2` reusing the same input 0 + witness + output 0, but with OP_RETURN output 2 replaced with B's `xonly_public_key`; broadcast with a higher fee/RBF so it confirms instead.
5. Mine a block, run `Verifier::update_finalized_payouts`.
6. Assert:
   - `db.get_first_unhandled_payout_by_operator_xonly_pk(operator_A.xonly_public_key)` returns `None`.
   - `db.get_first_unhandled_payout_by_operator_xonly_pk(operator_B.xonly_public_key)` returns `Some(...)`.
   This demonstrates `payout_payer_operator_xonly_pk` no longer equals the true funder (A) and instead equals the attacker-chosen key (B), breaking the attribution binding.

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

**File:** core/src/operator.rs (L588-626)
```rust
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

**File:** core/src/database/verifier.rs (L170-196)
```rust
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
