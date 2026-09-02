### Title
Payout attribution forgeable via unsigned OP_RETURN under `SinglePlusAnyoneCanPay` sighash — arbitrary operator can be credited/blamed for a payout it never funded - ([File: core/src/verifier.rs], [File: core/src/task/payout_checker.rs])

### Summary
`Verifier::update_finalized_payouts` (`core/src/verifier.rs:2283-2353`) attributes a payout solely to whichever `XOnlyPublicKey` is found in the payout transaction's OP_RETURN output, without verifying that key against the actual signer/funder of the transaction's other inputs. Because the payout's only *cryptographically bound* input signature uses `TapSighashType::SinglePlusAnyoneCanPay`, which commits only to input 0 and output 0, the OP_RETURN output (a later output) and all other inputs are completely unsigned and mutable by anyone who can see or construct that transaction, allowing an attacker to bind the payout to an arbitrary operator's key.

### Finding Description
The binding this system needs to hold is:

`payout_payer_operator_xonly_pk` (DB field set by `update_finalized_payouts`, consumed by `get_first_unhandled_payout_by_operator_xonly_pk`) `==` the xonly public key of the operator who actually funded/broadcast the payout transaction (i.e., whose own wallet inputs paid the fee/covered the payout).

Tracing the code:
- `create_payout_txhandler` (`core/src/builder/transaction/operator_reimburse.rs:407-436`) builds a payout tx with input 0 = the withdrawal UTXO (spent via the user's `taproot::Signature`), output 0 = user payout, output 2 = an `OP_RETURN` push of `operator_xonly_pk.serialize()`.
- The user's signature is required to be `TapSighashType::SinglePlusAnyoneCanPay` (enforced in `core/src/rpc/parser/operator.rs:174-187` and verified in `core/src/operator.rs:630-637` / `core/src/rpc/aggregator.rs:1120-1126`).
- `calculate_pubkey_spend_sighash` (`core/src/builder/transaction/txhandler.rs:210-233`) shows that for `SinglePlusAnyoneCanPay`, `Prevouts::One(...)` is used — i.e. only the signed input's own prevout is committed (`AnyoneCanPay`), and per BIP-341/taproot sighash semantics, `SIGHASH_SINGLE` only commits the output at the *same index* as the input being signed (output 0). This is corroborated by the sighash-writer logic reproduced in `circuits-lib/src/bridge_circuit/mod.rs:801-810` (`sha_outputs` is only computed when `sighash != None && sighash != Single`).
- Consequently, **outputs 1 (anchor) and 2 (OP_RETURN with the operator's xonly_pk), and all inputs other than input 0, are not covered by any signature** in the payout transaction. Anyone possessing the withdrawal UTXO's `SinglePlusAnyoneCanPay` signature (visible once any operator broadcasts/relays it, or held directly by the withdrawing user themselves, who is by definition able to produce it) can reconstruct a variant transaction: same input 0 + same output 0, but with their own fee-paying input(s) and an arbitrary OP_RETURN value — including another operator's public xonly key — and get it confirmed on-chain (via fee/RBF race, or simply by being the one who submits/broadcasts first, since operators are never required to be the actual broadcaster).
- `update_finalized_payouts` (`core/src/verifier.rs:2312-2342`) then reads whatever OP_RETURN happens to be in the confirmed transaction via `parse_op_return_data`/`get_first_op_return_output` and blindly writes it as `payout_payer_operator_xonly_pk`, with **no check** that this key corresponds to any signer of the transaction's other inputs.
- `PayoutCheckerTask::run_once` (`core/src/task/payout_checker.rs:39-79`) for operator B calls `get_first_unhandled_payout_by_operator_xonly_pk(B)`, which matches this forged row, and calls `Operator::handle_finalized_payout` for B, which allocates a kickoff/reimbursement connector for B (`core/src/operator.rs:839-885`) — starting B down the path to being reimbursed from the bridge's collateral/round funds for a payout B never funded.

None of the existing guards catch this: `SECP.verify_schnorr` only checks the user's signature over input 0 and output 0 (not the OP_RETURN or other inputs); `update_finalized_payouts` has no signature-recovery or funding-verification step against the OP_RETURN pubkey; and later checks like the one in `send_asserts` (`core/src/operator.rs:1290-1295`) only compare the DB's payer field against the *kickoff*'s operator, not against the actual on-chain funder — they simply re-validate the already-corrupted attribution, not the original binding.

### Impact Explanation
This directly enables a Critical outcome: an operator can be reimbursed for a payout it never funded (if a colluding/dishonest operator B claims the forged row), or, if B is honest and never claims a payout it does not recognize funding, the true payer A's `withdrawals` row is permanently stuck attributed to B (`is_payout_handled` gating on `payout_payer_operator_xonly_pk = B`), so A can never be credited — a fronted-payout-never-reimbursed outcome. This is repeatable for every withdrawal processed on the bridge and works against any operator whose public xonly key is known (all operator keys are public), making the blast radius bridge-wide, not limited to a single deposit/operator pair.

### Likelihood Explanation
The attacker only needs: (1) their own withdrawal request's `SinglePlusAnyoneCanPay` signature (which a withdrawing user legitimately possesses/produces themselves, satisfying "unprivileged, can call `withdraw`... choose... a Schnorr signature and its sighash flag") and (2) enough BTC to fund their own fee-paying input(s) and broadcast a Bitcoin transaction — both explicitly listed as within the unprivileged attacker's capabilities. No operator key, verifier key, aggregator access, or majority hashrate is required. The only competition is winning the race to have the desired transaction confirmed instead of/along with any operator-submitted variant, which is a normal mempool/fee dynamic, not a privileged capability.

### Recommendation
Bind the OP_RETURN operator-attribution to the actual funder cryptographically, e.g., have the operator sign a fixed-format commitment over `(withdrawal_idx, operator_xonly_pk)` and require `update_finalized_payouts` to recover/verify that the fee-paying input(s) of the payout tx are spendable only by the same operator whose key is embedded in the OP_RETURN (e.g., require operator's own committed collateral/round-linked UTXO as an additional signed input, or use `SIGHASH_ALL`/`SIGHASH_DEFAULT` for a second input that also signs over the OP_RETURN output so it can't be altered without invalidating a signature bound to the claiming operator). Alternatively, require the OP_RETURN output to be covered by the sighash of an input that the operator must sign (not `AnyoneCanPay`/`Single`), so that only the operator whose funds are actually spent in the transaction can produce a valid signature attesting to their own OP_RETURN commitment.

### Proof of Concept
```rust
// core/src/verifier.rs (add near update_finalized_payouts tests)
#[tokio::test]
async fn payout_attribution_forgeable_via_unsigned_op_return() {
    // 1. Set up a withdrawal UTXO with a user-controlled key, sign the
    //    withdrawal input with TapSighashType::SinglePlusAnyoneCanPay
    //    (mirrors sign_withdrawal_output in core/src/test/common/setup_utils.rs).
    // 2. Construct payout_tx_A: create_payout_txhandler(input_utxo, output_txout,
    //    operator_a_xonly_pk, user_sig, network) -- this is what "operator A" would
    //    legitimately fund (fund with A's own extra input for fees, matching how
    //    Operator::withdraw does it via fund_raw_transaction).
    // 3. Construct payout_tx_B: same input 0 (same witness/signature reused,
    //    valid since SinglePlusAnyoneCanPay does not commit to other inputs/outputs),
    //    but funded with the ATTACKER's own fee input, and OP_RETURN set to
    //    operator_b_xonly_pk (operator B never signed or funded anything here).
    // 4. Assert both payout_tx_A and payout_tx_B pass bitcoind's
    //    `test_mempool_accept` / are individually valid (only one can confirm,
    //    demonstrating malleability of attribution, not of the payout amount).
    // 5. Mine payout_tx_B into a block, feed it through
    //    Verifier::update_finalized_payouts.
    // 6. assert_eq!(
    //        db.get_payout_info_from_move_txid(None, move_txid).await.unwrap().unwrap().0,
    //        Some(operator_b_xonly_pk)
    //    );
    // 7. assert!(
    //        db.get_first_unhandled_payout_by_operator_xonly_pk(None, operator_b_xonly_pk)
    //            .await.unwrap().is_some()
    //    ); // FAILS the intended binding: B never funded this payout.
    // 8. assert!(
    //        db.get_first_unhandled_payout_by_operator_xonly_pk(None, operator_a_xonly_pk)
    //            .await.unwrap().is_none()
    //    ); // A, the true would-be payer, can never be credited for this withdrawal.
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8) [10](#0-9)

### Citations

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

**File:** core/src/builder/transaction/txhandler.rs (L210-233)
```rust
    pub fn calculate_pubkey_spend_sighash(
        &self,
        txin_index: usize,
        sighash_type: TapSighashType,
    ) -> Result<TapSighash, BridgeError> {
        let prevouts_vec: Vec<&TxOut> = self
            .txins
            .iter()
            .map(|s| s.get_spendable().get_prevout())
            .collect();
        let mut sighash_cache: SighashCache<&bitcoin::Transaction> =
            SighashCache::new(&self.cached_tx);
        let prevouts = match sighash_type {
            TapSighashType::SinglePlusAnyoneCanPay
            | TapSighashType::AllPlusAnyoneCanPay
            | TapSighashType::NonePlusAnyoneCanPay => {
                bitcoin::sighash::Prevouts::One(txin_index, prevouts_vec[txin_index])
            }
            _ => bitcoin::sighash::Prevouts::All(&prevouts_vec),
        };

        let sig_hash = sighash_cache
            .taproot_key_spend_signature_hash(txin_index, &prevouts, sighash_type)
            .wrap_err("Failed to calculate taproot sighash for key spend")?;
```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L801-810)
```rust
    if sighash != TapSighashType::None && sighash != TapSighashType::Single {
        // Manually compute sha_outputs
        let mut enc_outputs = sha256::Hash::engine();
        for txout in tx.output.iter() {
            txout.consensus_encode(&mut enc_outputs).expect(expect_msg);
        }
        sha256::Hash::from_engine(enc_outputs)
            .consensus_encode(writer)
            .expect(expect_msg);
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

**File:** core/src/operator.rs (L839-860)
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
```

**File:** core/src/operator.rs (L1284-1295)
```rust
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
