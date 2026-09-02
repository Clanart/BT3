### Title
Payout attribution in `update_finalized_payouts` trusts unauthenticated OP_RETURN bytes, letting an attacker frame any registered operator as the payer - (`core/src/verifier.rs`)

### Summary
`Verifier::update_finalized_payouts` identifies the operator to credit for a payout purely by decoding whatever bytes sit in the first OP_RETURN output of *whatever transaction happens to spend* the registered withdrawal UTXO, with no cryptographic proof that the named operator actually funded that spend. Since the withdrawal UTXO is a low-value "dust" UTXO whose private key the withdrawing user (the attacker) controls, and whose `SinglePlusAnyoneCanPay` signature covers only that input and output[0], the attacker can add arbitrary extra outputs — including an OP_RETURN naming any registered operator's x-only pubkey — while self-funding the withdrawal, causing that unrelated operator to be credited as payer.

### Finding Description
The broken binding, stated explicitly: `payout_payer_operator_xonly_pk` (written to the `withdrawals` table by `update_finalized_payouts`) should equal `operator_who_funded_the_payout_tx_inputs` (the entity whose BTC actually produced the payout output that satisfied the user's withdrawal). The code never establishes this equality; it only checks that 32 bytes in an OP_RETURN parse as *some* valid `XOnlyPublicKey`.

Code path:
- `get_payout_txs_for_withdrawal_utxos` [1](#0-0)  finds the payout tx **solely by which transaction spent the registered `withdrawal_utxo`** — no check on inputs, funder, or signer.
- `update_finalized_payouts` then extracts the operator purely from OP_RETURN bytes: `get_first_op_return_output` -> `parse_op_return_data` -> `XOnlyPublicKey::from_slice` [2](#0-1)  with no signature or proof-of-funding check, and unconditionally writes this to the DB via `update_payout_txs_and_payer_operator_xonly_pk` [3](#0-2) .
- `create_payout_txhandler` shows the withdrawal UTXO is spent with `SpendPath::KeySpend` using only the *user's* signature over input 0 / output 0 [4](#0-3) ; the OP_RETURN output (index 2) carrying the operator pubkey is **not covered by any signature** — it's plain, unauthenticated data appended by whoever constructs/broadcasts the final transaction.
- The withdrawal UTXO itself is a small dust UTXO signed with `SinglePlusAnyoneCanPay`, explicitly designed to let a third party (normally the operator) add extra funding inputs [5](#0-4) . Because it's `AnyoneCanPay`, the attacker (who holds the dust UTXO's key, being the withdrawing user themselves) can also add the funding inputs and additional outputs themselves.
- The credited operator's own node then treats this as a legitimate unhandled payout via `get_first_unhandled_payout_by_operator_xonly_pk` and proceeds through `PayoutCheckerTask` -> `Operator::handle_finalized_payout` [6](#0-5) [7](#0-6) , allocating a pre-reserved kickoff connector under that operator's own `signer.xonly_public_key` and beginning the reimbursement flow.
- The bridge/disprove circuit does not catch the mismatch: it recomputes `deposit_constant` using the **same unauthenticated OP_RETURN bytes** from the payout tx [8](#0-7) , so it only checks self-consistency between the claimed operator and that operator's own round/kickoff — it can't detect that the named operator never actually supplied the payout funds.

Why existing guards fail: `Verifier::is_deposit_valid`, `SPV::verify`, `verify_storage_proofs`, and `lc_proof_verifier` all validate that the withdrawal UTXO was spent and that a Citrea withdrawal exists for the index — none of them verify who supplied the payout tx's funding inputs or who authored the OP_RETURN. There is no signature check binding the OP_RETURN to the named operator anywhere in this path.

### Impact Explanation
A named-but-uninvolved operator is credited in the DB as `payout_payer_operator_xonly_pk` and will autonomously kick off the reimbursement process (consuming a kickoff connector, later a round-tx reimbursement output) for a withdrawal it never funded — matching "an operator reimbursed for a payout it never funded." This is repeatable across any deposit/withdrawal where the attacker controls the withdrawal UTXO's key (true for every withdrawal, since it's the withdrawing user's own dust UTXO) and against any registered operator (attacker just needs their public x-only pubkey, which is public protocol data). The blast radius spans the whole operator set and every withdrawal.

### Likelihood Explanation
Preconditions are minimal and match the stated unprivileged attacker capabilities exactly: call `withdraw` on Citrea, choose the withdrawal UTXO bytes/signature/sighash flag, and craft the final Bitcoin spending transaction and its OP_RETURN — all things an ordinary withdrawing user does. Attacker cost is only their own withdrawal amount funding (which they'd pay anyway to receive their own withdrawal) plus a normal fee; no operator/verifier collusion or key compromise required. This is highly likely/feasible and trivially repeatable.

### Recommendation
Do not derive `payout_payer_operator_xonly_pk` from unauthenticated OP_RETURN bytes alone. Require that the payout tx's extra funding inputs be attributable to (e.g., signed by, or matched against pre-registered) the claimed operator's known keys/UTXO set, or otherwise cryptographically bind the OP_RETURN operator claim to the actual funding source before crediting a payout to any operator in `update_finalized_payouts`/`update_payout_txs_and_payer_operator_xonly_pk`.

### Proof of Concept
`cargo test` plan (regtest, no mainnet/live Citrea):
1. Register a withdrawal on the mocked Citrea client for a dust UTXO controlled by the test's "attacker" actor (as done in `generate_withdrawal_utxo`/`sign_withdrawal_output`).
2. Instead of calling any operator's `withdraw` RPC, have the attacker directly build and sign (with `SinglePlusAnyoneCanPay`) a transaction spending that dust UTXO to their own address, self-funding the payout amount from their own wallet, and append an OP_RETURN output containing the x-only pubkey of an operator (e.g. operator index 1) who never participated in constructing this transaction.
3. Broadcast and mine to finality; let the block syncer run `update_finalized_payouts`.
4. Assert that `Database::get_payout_txs_for_withdrawal_utxos`/DB row for that withdrawal index has `payout_payer_operator_xonly_pk == operator_1_xonly_pk` even though operator 1 never called `withdraw`, never signed, and never funded anything (assert operator 1's DB/state shows no participation prior to this).
5. Assert (negative expectation for a fixed implementation) that `PayoutCheckerTask` for operator 1 should NOT pick this up as `get_first_unhandled_payout_by_operator_xonly_pk` / should not proceed into `handle_finalized_payout` absent proof operator 1 funded the input — with the current code, this assertion fails, demonstrating the vulnerability.

### Citations

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

**File:** core/src/verifier.rs (L2312-2321)
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

**File:** core/src/test/common/setup_utils.rs (L499-543)
```rust
fn sign_withdrawal_output(
    config: &BridgeConfig,
    dust_utxo: &UTXO,
    withdrawal_address: &bitcoin::Address,
    withdrawal_amount: bitcoin::Amount,
) -> (bitcoin::TxOut, taproot::Signature) {
    let signer = Actor::new(config.secret_key, config.protocol_paramset().network);
    let txin = builder::transaction::input::SpendableTxIn::new(
        dust_utxo.outpoint,
        dust_utxo.txout.clone(),
        vec![],
        None,
    );
    let txout = bitcoin::TxOut {
        value: withdrawal_amount,
        script_pubkey: withdrawal_address.script_pubkey(),
    };
    let unspent_txout = builder::transaction::output::UnspentTxOut::from_partial(txout.clone());

    let tx = builder::transaction::TxHandlerBuilder::new(TransactionType::Payout)
        .with_version(NON_STANDARD_V3)
        .add_input(
            NormalSignatureKind::NotStored,
            txin,
            SpendPath::KeySpend,
            builder::transaction::DEFAULT_SEQUENCE,
        )
        .add_output(unspent_txout.clone())
        .finalize();

    let sighash = tx
        .calculate_sighash_txin(0, sighash::TapSighashType::SinglePlusAnyoneCanPay)
        .expect("Failed to calculate sighash");

    let sig = signer
        .sign_with_tweak_data(sighash, builder::sighash::TapTweakData::KeyPath(None), None)
        .expect("Failed to sign");

    let sig = taproot::Signature {
        signature: sig,
        sighash_type: sighash::TapSighashType::SinglePlusAnyoneCanPay,
    };

    (txout, sig)
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

**File:** core/src/operator.rs (L839-885)
```rust
    pub async fn handle_finalized_payout<'a>(
        &'a self,
        dbtx: DatabaseTransaction<'a>,
        deposit_outpoint: OutPoint,
        payout_tx_blockhash: BlockHash,
    ) -> Result<bitcoin::Txid, BridgeError> {
        let (deposit_id, deposit_data) = self
            .db
            .get_deposit_data(Some(dbtx), deposit_outpoint)
            .await?
            .ok_or(BridgeError::DatabaseError(sqlx::Error::RowNotFound))?;

        // get unused kickoff connector
        let (round_idx, kickoff_idx) = self
            .db
            .get_unused_and_signed_kickoff_connector(
                Some(dbtx),
                deposit_id,
                self.signer.xonly_public_key,
            )
            .await?
            .ok_or(BridgeError::DatabaseError(sqlx::Error::RowNotFound))?;

        let current_round_index = self.db.get_current_round_index(Some(dbtx)).await?;
        tracing::info!(
            "Operator: Current round index: {}, round idx for kickoff: {}",
            current_round_index,
            round_idx
        );
        #[cfg(feature = "automation")]
        if current_round_index != round_idx {
            // we currently have no free kickoff connectors in the current round, so we need to end round first
            // if current_round_index should only be smaller than round_idx, and should not be smaller by more than 1
            // so sanity check:
            if current_round_index.next_round() != round_idx {
                return Err(eyre::eyre!(
                    "Internal error: Expected the current round ({:?}) to be equal to or 1 less than the round of the first available kickoff for deposit reimbursement ({:?}) for deposit {:?}. If the round is less than the current round, there is an issue with the logic of the fn that gets the first available kickoff. If the round is greater, that means the next round do not have any kickoff connectors available for reimbursement, which should not be possible.",
                    current_round_index, round_idx, deposit_outpoint
                ).into());
            }
            tracing::info!(
                "Operator: Starting next round to be able to get reimbursement for the payout"
            );
            // start the next round to be able to get reimbursement for the payout
            self.end_round(dbtx).await?;
        }

```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L206-229)
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

    let deposit_constant = deposit_constant(
        operator_xonlypk,
        input.watchtower_challenge_connector_start_idx,
        &input.all_tweaked_watchtower_pubkeys,
        *move_txid,
        round_txid,
        kickoff_round_vout,
        input.hcp.genesis_state_hash,
    );
```
