### Title
Payout attribution is taken from an unauthenticated OP_RETURN field, letting a non-payer operator be recorded (and later reimbursed) as the payer of a withdrawal it never funded - (File: core/src/verifier.rs, core/src/builder/transaction/operator_reimburse.rs)

### Summary
The protocol decides *which operator gets reimbursed* for a Citrea withdrawal purely by reading a plaintext `OP_RETURN` push in whichever Bitcoin transaction happens to spend the committed withdrawal UTXO. That push is never cryptographically bound to the party who actually funds the payout. Because the withdrawal input is signed with `SIGHASH_SINGLE | ANYONECANPAY`, any third party who observes the signed input (e.g. in the Bitcoin mempool once any operator broadcasts a payout attempt) can freely re-assemble a competing transaction that reuses that exact signed input/output, adds its own fee-paying inputs, and writes an *arbitrary* operator's x-only pubkey into the `OP_RETURN` output. Whichever transaction actually confirms on-chain is what the verifiers trust as "proof of payer," so an unprivileged party can force the protocol to credit an operator that never spent a satoshi.

### Finding Description
The payout transaction commits the "payer" identity as unauthenticated plaintext: [1](#0-0) 

`operator_xonly_pk` is embedded via a plain OP_RETURN push with **no signature over it** — it is not committed by the withdrawer's signature, nor by any N-of-N/verifier signature. Compare with the Astaria bug: `identifier` (attacker-controlled) was blindly trusted as `paymentToken` with no check that it matched the real settlement asset; here the OP_RETURN payload is blindly trusted as "the operator that funded this payout" with no check that it matches the actual funder of the transaction's other inputs.

The withdrawal input itself is signed `SinglePlusAnyoneCanPay`: [2](#0-1) 

and verified the same way when an operator prepares a payout: [3](#0-2) 

`SinglePlusAnyoneCanPay` commits *only* to (a) the specific withdrawal input, and (b) the output at the same index (the user's payout). It explicitly permits arbitrary other inputs and outputs to be attached by *anyone* holding the raw signature — that is the whole point of `ANYONECANPAY`. Once an operator broadcasts its payout transaction (to fund and confirm it), the signature is visible in the P2P network/mempool to any observer, unprivileged or not. An observer can therefore build a rival transaction that:
1. Reuses the exact same signed withdrawal input and matching user-payout output (mandatory, cannot be altered).
2. Adds its own funding inputs (attacker pays the fee/any shortfall) and outputs.
3. Sets the `OP_RETURN` to an arbitrary *different* operator's x-only pubkey (any registered operator), or omits/garbles it.
4. Gets mined first (e.g. by paying a higher fee), replacing the intended payer's transaction.

The verifier later scans the chain and attributes payer credit purely from whichever transaction happened to spend the recorded withdrawal UTXO, taking the OP_RETURN at face value: [4](#0-3) 

That attribution is persisted and later consumed by the automated reimbursement pipeline, which trusts it completely — it looks up "unhandled payouts" strictly by the recorded `payout_payer_operator_xonly_pk` and starts the kickoff/reimbursement flow for that operator, with no re-verification that the named operator actually funded the transaction: [5](#0-4) [6](#0-5) 

The binding that must hold is:
`operator credited as payer == operator whose own funds satisfied the withdrawal output`

Under this bug, an unprivileged network observer can break that equality in either direction:
- Credit an arbitrary *other* registered operator (who spent nothing) as the payer, so that operator is automatically walked through the kickoff/reimburse flow and paid the deposited move-to-vault funds it never fronted.
- Or blank/garble the OP_RETURN so the transaction is mistaken for an "optimistic payout" (no operator credited), permanently denying the operator that actually intended to front the withdrawal any path to reimbursement, since the withdrawal UTXO is already spent by the rival transaction and cannot be reused.

### Impact Explanation
This maps directly to the Critical impact categories:
- "an operator reimbursed for a payout it never funded" — a bystander operator can be named as payer by a third party who funds the payout themselves, and the automated `PayoutCheckerTask` will walk that operator through kickoff/reimbursement, paying out the deposited BTC to an operator that did not front it.
- "an honest operator permanently unable to be reimbursed" — the operator that genuinely intends to front the withdrawal loses the race for its own withdrawal UTXO (which can only be spent once); if the rival transaction is mined with a garbled/foreign OP_RETURN, the legitimate fronting operator has no route left to be credited or reimbursed for a withdrawal it may have already partially prepared/funded.

Either outcome breaks the core custody equality the bridge relies on: credited payer == actual payer.

### Likelihood Explanation
The only capability required is passively observing the Bitcoin mempool/network (or simply being faster to broadcast) — no verifier, operator, watchtower, or key-compromise role is needed to *perform* the attack; the attacker only needs to fund the replacement transaction's fee/output themselves, which is within reach of any unprivileged actor motivated to either steal reimbursement credit for a colluding operator or grief a specific honest operator. The precondition (observing a signed `SinglePlusAnyoneCanPay` witness in the wild) occurs naturally every time any operator broadcasts a normal payout, which is the expected steady-state operation of the bridge.

### Recommendation
Do not rely on an unauthenticated OP_RETURN field to determine payer credit. Instead:
- Require the payer identity to be cryptographically bound to the transaction, e.g. by having the operator sign a commitment (over the specific withdrawal outpoint and kickoff/round context) that verifiers check before recording `payout_payer_operator_xonly_pk`, rather than trusting plaintext OP_RETURN data.
- Alternatively, bind the OP_RETURN commitment into the same sighash that the operator signs when funding additional inputs (e.g., require the operator's own signed input(s) in the payout transaction to co-sign a digest that includes the OP_RETURN payload), so a third party cannot rewrite the payer field without invalidating the operator's own contribution.
- At minimum, require that the OP_RETURN-declared operator's key also appears as a signer of one of the *funding* inputs of the same transaction, so credit cannot be attributed to a party that did not sign for any value in that transaction.

### Proof of Concept
1. Operator `O` prepares and broadcasts a normal payout transaction for withdrawal `W` per `Operator::withdraw` (`core/src/operator.rs:560-675`), embedding its own xonly pubkey in the OP_RETURN via `create_payout_txhandler` (`core/src/builder/transaction/operator_reimburse.rs:407-435`). The signed input uses `SinglePlusAnyoneCanPay`.
2. An unprivileged observer monitors the Bitcoin P2P network/mempool and extracts the witness for `O`'s payout input as soon as it is broadcast (this is public data at that point).
3. The observer constructs a new transaction that:
   - Spends the same withdrawal outpoint with the copied witness (valid due to `ANYONECANPAY`).
   - Keeps the mandated user-payout output unchanged (forced by `SIGHASH_SINGLE`).
   - Adds its own fee-paying input(s).
   - Writes operator `O'`'s (a different, unrelated registered operator) xonly pubkey into the OP_RETURN output.
   - Pays a higher fee so it is mined before `O`'s transaction (classic RBF/fee-race replacement).
4. Once mined, `verifier.rs::update_finalized_payouts` (`core/src/verifier.rs:2283-2353`) scans the block, finds this transaction as the one that spent `W`, and records `payout_payer_operator_xonly_pk = O'`.
5. `PayoutCheckerTask::run_once` (`core/src/task/payout_checker.rs:31-113`) subsequently detects an "unhandled payout" for `O'` and drives `O'` through `handle_finalized_payout`/kickoff/reimbursement, resulting in `O'` receiving the deposited move-to-vault funds despite never funding the withdrawal — while `O`, who intended to front it, has no route left to claim credit for the same (now-spent) withdrawal UTXO.

### Citations

**File:** core/src/builder/transaction/operator_reimburse.rs (L407-435)
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

**File:** core/src/operator.rs (L614-637)
```rust
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
```

**File:** core/src/verifier.rs (L2298-2343)
```rust
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
