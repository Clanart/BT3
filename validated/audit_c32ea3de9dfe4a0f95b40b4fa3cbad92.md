### Title
`get_first_op_return_output` misattributes payout to an attacker-forged operator xonly_pk, letting an unrelated operator be reimbursed for a withdrawal it never funded - (File: `circuits-lib/src/bridge_circuit/mod.rs`, `core/src/verifier.rs`)

### Summary
The payout attribution mechanism trusts the *first* `OP_RETURN` output found in whatever transaction happens to spend a tracked `withdrawal_utxo` on-chain, with no signature binding over that output. Because the user's payout signature uses `TapSighashType::SinglePlusAnyoneCanPay`, everything except input 0 and output 0 is unconstrained, so any holder of that signature (including the withdrawing user acting as attacker) can construct their own variant of the payout transaction with a forged/garbage `OP_RETURN` inserted before the real one, causing the bridge to record a false payer operator.

### Finding Description
The claimed binding is: `payout_payer_operator_xonly_pk` (stored in DB and later trusted by `PayoutCheckerTask`/`is_kickoff_malicious`) `== xonly_pk of the operator whose own funds actually financed the payout output`.

Root cause chain:
1. `create_payout_txhandler` builds the canonical structure output0=user payout, output1=anchor, output2=`OP_RETURN(operator_xonly_pk)` [1](#0-0) , but only input 0 is signed with `taproot::Signature` using `SinglePlusAnyoneCanPay`, enforced in `parse_withdrawal_sig_params` [2](#0-1) , and verified in `Operator::withdraw` via `calculate_sighash_txin(0, in_signature.sighash_type)` [3](#0-2) . `SIGHASH_SINGLE|ANYONECANPAY` only commits to input 0 and the output at index 0 — all other inputs/outputs (anchor, OP_RETURN, and any additional funding inputs added by `fund_raw_transaction`) are completely free to change.
2. Verifiers do not identify "the payout tx" by validating this signed structure or by tracking which entity broadcast it; they identify it purely by whichever on-chain transaction spends the tracked `withdrawal_utxo_txid`/`vout` outpoint, matched in `get_payout_txs_for_withdrawal_utxos` via a join on `bitcoin_syncer_spent_utxos` [4](#0-3) .
3. `Verifier::update_finalized_payouts` then takes that arbitrary matched transaction and blindly picks the *first* `OP_RETURN` output via `get_first_op_return_output`, parses whatever bytes are pushed as the payer's xonly pubkey, and persists it: `get_first_op_return_output(&circuit_payout_tx)` → `parse_op_return_data` → `XOnlyPublicKey::from_slice` → `update_payout_txs_and_payer_operator_xonly_pk` [5](#0-4) . `get_first_op_return_output` simply returns `tx.output.iter().find(|out| out.script_pubkey.is_op_return())` — the first match, with no uniqueness check [6](#0-5) .
4. `PayoutCheckerTask` for operator O polls `get_first_unhandled_payout_by_operator_xonly_pk(O)` [7](#0-6) , which returns any withdrawal whose stored `payout_payer_operator_xonly_pk` equals O's key, regardless of whether O ever broadcast anything. It then calls `handle_finalized_payout`, which creates and signs a kickoff transaction for O to claim reimbursement [8](#0-7) .

Exploit flow: the attacker is the withdrawing user itself. They call `withdraw()` on the Citrea Bridge contract, producing a `withdrawal_utxo` and a `SinglePlusAnyoneCanPay` Schnorr signature over input0/output0. Instead of waiting for a legitimate operator to front the payout via the `withdraw`/`internal_withdraw` RPC, the attacker constructs their own transaction: input0 = the signed withdrawal input, output0 = the exact signed payout output, plus any funding inputs the attacker supplies themselves, and inserts an `OP_RETURN` output *before* the position an honest operator would use, containing an honest operator O's real xonly pubkey (obtainable from any prior on-chain payout or from operator registration data) or a valid-but-arbitrary 32-byte value. The attacker broadcasts this transaction (paying their own fees). No check anywhere validates that the additional funding inputs of the confirmed payout tx were actually contributed by the operator whose key appears in the OP_RETURN, nor that there is exactly one `OP_RETURN` at the canonical index. `is_kickoff_malicious` only checks that the OP_RETURN pk in the DB matches the kickoff sender's pk, and the committed payout blockhash — it does not, and cannot, validate who actually funded the transaction [9](#0-8) .

### Impact Explanation
Operator O — who never funded this withdrawal — is misidentified by `PayoutCheckerTask` as the payer and automatically sends a kickoff transaction and eventually collects the `Reimburse` transaction, i.e., O is reimbursed from bridge collateral/anchor pools for a payout it never funded. This directly matches the Critical category "an operator reimbursed for a payout it never funded." It is repeatable across every withdrawal and every operator whose xonly pubkey is public (all operator keys are public/registered), and requires no privileged role, key compromise, or majority hashrate — only the ability to broadcast a Bitcoin transaction and knowledge of one's own withdrawal signature.

### Likelihood Explanation
Preconditions are met by the standard, unprivileged withdrawal flow: any user calling `withdraw()` obtains a `SinglePlusAnyoneCanPay`-signed input/output pair, which by design (for fee-bumping flexibility) leaves all outputs beyond index 0 unauthenticated. The attacker only needs to broadcast their own transaction before/instead of the legitimate operator, and pays their own transaction fees; no special deployment configuration is needed. Because the attribution logic (`get_first_op_return_output`, unconditioned matching by spent-outpoint) is fixed in the current codebase, this is directly reproducible via unit test.

### Recommendation
Do not rely on an unauthenticated `OP_RETURN` picked by "first match" to attribute payout funding. Options: (1) require a single, canonical `OP_RETURN` at the fixed expected output index and reject transactions with more than one `OP_RETURN` output before recording attribution; (2) cryptographically bind the operator identity into the signed part of the transaction (e.g., require the operator to co-sign or fund a distinguishing input that is verifiably theirs, rather than trusting a push in an unsigned output); (3) verify that the additional funding input(s) beyond input 0 belong to (are spendable/controlled by) the xonly pubkey claimed in the OP_RETURN before crediting that operator as payer.

### Proof of Concept
```rust
// cargo test in circuits-lib, extending existing tests around get_first_op_return_output / parse_op_return_data
#[test]
fn test_first_op_return_can_be_attacker_forged() {
    use bitcoin::{Transaction, TxOut, ScriptBuf, Amount, absolute::LockTime, transaction::Version, Witness, TxIn, OutPoint};
    use circuits_lib::bridge_circuit::{get_first_op_return_output, parse_op_return_data, CircuitTransaction};

    let real_operator_pk = [0xAAu8; 32]; // honest operator O's real xonly pk
    let forged_pk = [0xBBu8; 32];        // attacker-chosen / garbage pk

    // Legitimate structure: output0=payout, output1=anchor, output2=OP_RETURN(real_operator_pk)
    let legit_op_return = op_return_script(&real_operator_pk);

    // Attacker's copy: same input0/output0 (signature-bound), but with an EXTRA
    // OP_RETURN inserted earlier (output index 1), containing forged/garbage pk,
    // followed by the anchor and the real OP_RETURN at index 3.
    let attacker_op_return = op_return_script(&forged_pk);

    let attacker_tx = build_tx(vec![
        payout_output(),                 // index 0 - identical, signature-bound
        txout_op_return(&attacker_op_return), // index 1 - attacker-inserted, EARLIER
        anchor_output(),                 // index 2
        txout_op_return(&legit_op_return),    // index 3 - real operator's OP_RETURN
    ]);

    let ct = CircuitTransaction::from(attacker_tx);
    let picked = get_first_op_return_output(&ct).expect("must find an OP_RETURN");
    let picked_pk = parse_op_return_data(&picked.script_pubkey).unwrap();

    // Demonstrates the binding is broken: the FIRST OP_RETURN found is the
    // attacker's forged one, not the real operator's (at index 3).
    assert_eq!(picked_pk, forged_pk);
    assert_ne!(picked_pk, real_operator_pk);
}
```
Additionally, an integration-level test in `core/src/verifier.rs`'s test module can feed this `attacker_tx` through `update_finalized_payouts`'s logic path (mocking `block_cache`/`get_payout_txs_for_withdrawal_utxos` to report this tx as spending the tracked withdrawal outpoint) and assert that `payout_payer_operator_xonly_pk` recorded in the `withdrawals` table equals `forged_pk`/`real_operator_pk` even though operator O never broadcast or funded the transaction, confirming the downstream misattribution used by `PayoutCheckerTask`.

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

**File:** core/src/operator.rs (L628-637)
```rust
        // tracing::info!("Payout txhandler: {:?}", hex::encode(bitcoin::consensus::serialize(&payout_txhandler.get_cached_tx())));

        let sighash = payout_txhandler.calculate_sighash_txin(0, in_signature.sighash_type)?;

        SECP.verify_schnorr(
            &in_signature.signature,
            &Message::from_digest(*sighash.as_byte_array()),
            user_xonly_pk,
        )
        .wrap_err("Failed to verify signature received from user for payout txin. Ensure the signature uses SinglePlusAnyoneCanPay sighash type.")?;
```

**File:** core/src/operator.rs (L839-916)
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

        // get signed txs,
        let kickoff_data = KickoffData {
            operator_xonly_pk: self.signer.xonly_public_key,
            round_idx,
            kickoff_idx,
        };

        let payout_tx_blockhash = payout_tx_blockhash.as_byte_array().last_20_bytes();

        #[cfg(test)]
        let payout_tx_blockhash = self
            .config
            .test_params
            .maybe_disrupt_payout_tx_block_hash_commit(payout_tx_blockhash);

        let context = ContractContext::new_context_for_kickoff(
            kickoff_data,
            deposit_data,
            self.config.protocol_paramset(),
        );

        let signed_txs = create_and_sign_txs(
            self.db.clone(),
            &self.signer,
            self.config.clone(),
            context,
            Some(payout_tx_blockhash),
            Some(dbtx),
        )
        .await?;

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

**File:** core/src/verifier.rs (L2312-2350)
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

        self.db
            .update_payout_txs_and_payer_operator_xonly_pk(
                Some(dbtx),
                payout_txs_and_payer_operator_idx,
            )
            .await?;
```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L686-692)
```rust
/// Retrieves the first output of a transaction that is an OP_RETURN script. Used in various
/// contexts to extract metadata or constants from transactions.
pub fn get_first_op_return_output(tx: &CircuitTransaction) -> Option<&TxOut> {
    tx.output
        .iter()
        .find(|out| out.script_pubkey.is_op_return())
}
```

**File:** core/src/task/payout_checker.rs (L41-47)
```rust
        let unhandled_payout = self
            .db
            .get_first_unhandled_payout_by_operator_xonly_pk(
                Some(&mut dbtx),
                self.operator.signer.xonly_public_key,
            )
            .await?;
```
