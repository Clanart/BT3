### Title
Payout tx's OP_RETURN operator-attribution field is unauthenticated by the `SinglePlusAnyoneCanPay` signature, allowing a replacement transaction to falsify the recorded payer and permanently deny/burn the honest operator - ([File: core/src/builder/transaction/operator_reimburse.rs])

### Summary
`create_payout_txhandler` places `operator_xonly_pk.serialize()` in output index 2 (the OP_RETURN), but `Operator::withdraw`'s `SECP.verify_schnorr` check only authenticates input 0 against output 0 under `TapSighashType::SinglePlusAnyoneCanPay`. Because that sighash type structurally excludes every other input and every other output, an attacker who observes the honest operator's broadcast-but-unconfirmed Payout tx can extract the reusable witness and rebroadcast a higher-fee replacement with an identical input 0/output 0 but an arbitrary OP_RETURN payload, corrupting the on-chain record of "who paid this withdrawal."

### Finding Description
The broken binding: `recorded_operator_xonly_pk_for_withdrawal(i)` (the value `Verifier::update_finalized_payouts` stores in `withdrawals.payout_payer_operator_xonly_pk`) should equal `xonly_pk_of_party_whose_funds_paid_output_0(i)` (the honest operator who actually fronted the withdrawal by broadcasting the Payout tx with their own key baked into the OP_RETURN and their own wallet funding the tx via `fund_raw_transaction`). This equality is not enforced by any cryptographic binding.

- `create_payout_txhandler` (core/src/builder/transaction/operator_reimburse.rs:407-436) builds a Payout tx: input0 = withdrawal UTXO (`SpendPath::KeySpend`), output0 = user payout, output1 = anchor, output2 = `OP_RETURN(operator_xonly_pk.serialize())`. [1](#0-0) 
- `Operator::withdraw` computes the sighash strictly for `txin_index = 0` and verifies the user's signature against it: `payout_txhandler.calculate_sighash_txin(0, in_signature.sighash_type)` then `SECP.verify_schnorr(...)`. [2](#0-1) 
- `calculate_pubkey_spend_sighash` for `SinglePlusAnyoneCanPay` uses `Prevouts::One(txin_index, ...)`, i.e., BIP341 taproot key-path sighash with ANYONECANPAY (only the current input's own outpoint/amount/scriptPubKey/sequence is committed — no other inputs) and SINGLE (only the output at the *same index* as the signed input, i.e. output 0, is committed). [3](#0-2) 
- The sighash-serialization logic in the circuits crate independently confirms this: under `anyone_can_pay`, `sha_prevouts`, `sha_amounts`, `sha_scriptpubkeys`, and `sha_sequences` of every other input are simply omitted from the signed message. [4](#0-3) 
- After the user signature is embedded via `set_p2tr_key_spend_witness`, `Operator::withdraw` calls `fund_raw_transaction`/`sign_raw_transaction_with_wallet` to add fee-paying inputs and broadcast — explicitly commented "send payout tx using RBF" — without re-touching input 0's witness or output 0. [5](#0-4) 

Consequently, output1 (anchor) and output2 (OP_RETURN operator pubkey) are **not authenticated by anything** — anyone who has seen the honest operator's broadcast (mempool-visible witness data) can construct a rival transaction that reuses input0's exact outpoint/sequence/witness and output0's exact script/value (required for the SIGHASH_SINGLE check to still validate), while freely substituting new funding inputs (their own, at higher fee) and an arbitrary OP_RETURN payload — a different xonly pk, or bytes that fail `parse_op_return_data`/`XOnlyPublicKey::from_slice`.

Downstream, `Verifier::update_finalized_payouts` blindly trusts whichever version of the tx is mined: it reads `get_first_op_return_output` + `parse_op_return_data` from the confirmed block and stores the result (possibly a wrong pubkey, possibly `NULL`) as `payout_payer_operator_xonly_pk`, with no check correlating it to the actual funder or to a signature. [6](#0-5) 

The honest operator's own automation, `PayoutCheckerTask::run_once`, only finds payouts to process via `get_first_unhandled_payout_by_operator_xonly_pk(operator_xonly_pk)`, which filters strictly on the (now-corrupted) DB column. [7](#0-6) [8](#0-7)  If the attacker's mutated tx is the one mined, the honest operator never discovers "their" payout and can never call `handle_finalized_payout` for it.

Meanwhile, `Verifier::is_kickoff_malicious` treats the DB-recorded `operator_xonly_pk` as ground truth: if the honest operator nonetheless proceeds to submit a kickoff (self-declaring `kickoff_data.operator_xonly_pk`), the check `operator_xonly_pk != kickoff_data.operator_xonly_pk` (sourced from the corrupted/None DB value) fails and the kickoff is flagged malicious. [9](#0-8)  Similarly, `validate_payer_is_operator`, used by `get_reimbursement_txs` to gate the Reimburse tx path, requires `payer_xonly_pk == self.signer.xonly_public_key` from the same corrupted column and errors out otherwise, permanently blocking the honest operator's legitimate reimbursement path. [10](#0-9) 

No existing guard (`Verifier::is_deposit_valid`, `Operator::is_profitable`, `only_aggregator_and_self`, `verify_storage_proofs`, `SPV::verify`, or a DB uniqueness constraint) authenticates the OP_RETURN payload against the user's signature or the actual funder of the transaction — the design intentionally leaves it outside the SIGHASH_SINGLE|ANYONECANPAY commitment to allow fee bumping, but this also makes it fully malleable by any third party who observes the mempool transaction.

### Impact Explanation
- The honest operator, who fronted the withdrawal amount for the user, can be permanently blocked from ever calling a valid Reimburse (their own automation never detects the payout because the recorded payer key doesn't match them), and/or their kickoff can be judged malicious by every verifier, driving them into a Challenge/Disprove path where their collateral is burned.
- This is repeatable for every open withdrawal that any operator has broadcast but not yet had confirmed, across all operators and deposits — any unconfirmed Payout tx observed in the mempool is vulnerable to this OP_RETURN rewrite.
- This matches the "Critical" impact categories: "an honest operator permanently unable to be reimbursed" and "an honest operator's collateral burned."

### Likelihood Explanation
- Preconditions match exactly the unprivileged attacker capability set: ability to broadcast Bitcoin transactions, pay fees, and observe/reuse a signature and sighash flag from a public mempool transaction.
- The attack requires only watching the mempool for a Payout tx (a routine and visible bridge event), extracting its input-0 witness, and constructing a fee-bumped replacement with a different OP_RETURN. Cost is bounded by the fee delta needed to win the RBF/mempool race (feasible; the attacker fully controls the extra funding inputs and fee rate of their replacement).
- Repeatable indefinitely against any operator's future withdrawals, for as long as the design leaves the OP_RETURN output uncommitted by the user's signature.

### Recommendation
Bind the operator-attribution OP_RETURN to the user's authorization, or otherwise make it verifiable independent of on-chain OP_RETURN bytes:
- Change the withdrawal signature scheme so that the operator's xonly pubkey (or a commitment to it) is part of what the user signs (e.g., require `SIGHASH_ALL`/`SIGHASH_SINGLE` without `ANYONECANPAY` covering the OP_RETURN output, or have the aggregator/verifiers additionally require an operator-signed commitment binding the specific `payout_tx` outpoint to the operator's identity, verified independently of the raw OP_RETURN bytes at `update_finalized_payouts` time).
- Alternatively, require `Verifier::update_finalized_payouts`/`is_kickoff_malicious` to cross-check that the operator who broadcasts a kickoff also controls (e.g., signed) the actual funding inputs of the mined Payout tx (input index ≥ 1), rather than trusting the unauthenticated OP_RETURN alone.

### Proof of Concept
`cargo test` plan (regtest bitcoind, no mainnet, no live Citrea):
1. Set up a regtest bridge deposit and withdrawal as in existing e2e tests (`core/src/test/deposit_and_withdraw_e2e.rs` patterns), obtaining a valid withdrawal UTXO and user signature (`SinglePlusAnyoneCanPay`).
2. Call the honest `operator0.withdraw(...)`; capture the resulting signed `Payout` tx (input0 witness, output0) but do **not** mine it (leave in mempool).
3. Programmatically build a second transaction: reuse input0 (same outpoint, sequence, witness) and output0 (same script_pubkey/value) from the honest tx; add a distinct funding input/output (attacker's own coins) at higher fee; replace output index 2's OP_RETURN payload with a different xonly pk (or invalid bytes).
4. Broadcast the attacker tx via `rpc.send_raw_transaction`, mine 1 block via `rpc.mine_blocks(1)`, and assert it (not the honest tx) is the one confirmed.
5. Trigger/await `Verifier::update_finalized_payouts` processing this block; assert `db.get_payout_info_from_move_txid(...)` for this withdrawal now returns the attacker's substituted pubkey or `None` — i.e., `recorded_operator_xonly_pk_for_withdrawal(i) != honest_operator_xonly_pk`.
6. Assert `db.get_first_unhandled_payout_by_operator_xonly_pk(honest_operator_xonly_pk)` returns `None` (binding broken: the honest operator's own automation can never discover this payout).
7. Have `operator0` submit its kickoff for this deposit and assert `Verifier::is_kickoff_malicious(...)` returns `true`, and that the corresponding collateral-burning/challenge path is triggered — confirming the honest operator is falsely flagged and loses collateral for a withdrawal they actually funded.

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

**File:** core/src/operator.rs (L639-691)
```rust
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

**File:** core/src/operator.rs (L1705-1729)
```rust
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

**File:** circuits-lib/src/bridge_circuit/mod.rs (L750-799)
```rust
    if !anyone_can_pay {
        // Manually compute sha_prevouts
        let mut enc_prevouts = sha256::Hash::engine();
        for txin in tx.input.iter() {
            txin.previous_output
                .consensus_encode(&mut enc_prevouts)
                .expect(expect_msg);
        }
        sha256::Hash::from_engine(enc_prevouts)
            .consensus_encode(writer)
            .expect(expect_msg);

        // Manually compute sha_amounts
        let all_prevouts = unwrap_all_prevouts(prevouts);
        let mut enc_amounts = sha256::Hash::engine();
        for prevout in all_prevouts.iter() {
            prevout
                .borrow()
                .value
                .consensus_encode(&mut enc_amounts)
                .expect(expect_msg);
        }
        sha256::Hash::from_engine(enc_amounts)
            .consensus_encode(writer)
            .expect(expect_msg);

        // Manually compute sha_scriptpubkeys
        let mut enc_script_pubkeys = sha256::Hash::engine();
        for prevout in all_prevouts.iter() {
            prevout
                .borrow()
                .script_pubkey
                .consensus_encode(&mut enc_script_pubkeys)
                .expect(expect_msg);
        }
        sha256::Hash::from_engine(enc_script_pubkeys)
            .consensus_encode(writer)
            .expect(expect_msg);

        // Manually compute sha_sequences
        let mut enc_sequences = sha256::Hash::engine();
        for txin in tx.input.iter() {
            txin.sequence
                .consensus_encode(&mut enc_sequences)
                .expect(expect_msg);
        }
        sha256::Hash::from_engine(enc_sequences)
            .consensus_encode(writer)
            .expect(expect_msg);
    }
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

**File:** core/src/task/payout_checker.rs (L39-51)
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
