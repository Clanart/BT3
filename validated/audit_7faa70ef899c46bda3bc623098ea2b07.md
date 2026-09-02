This confirms the mechanism: `get_payout_txs_for_withdrawal_utxos` identifies the "payout tx" purely by whichever transaction actually spent the tracked `withdrawal_utxo_txid`/`withdrawal_utxo_vout` outpoint (via `bitcoin_syncer_spent_utxos`), not by matching the operator's originally-broadcast txid. This means whatever transaction ends up confirmed spending that specific outpoint is treated as "the" payout tx, and its OP_RETURN (if any) is parsed to determine the attributed operator.

Combined with the enforced `SinglePlusAnyoneCanPay` sighash type on the withdrawal input signature [1](#0-0) , an attacker who observes the honest operator's broadcast, unconfirmed `Payout` transaction can extract input 0's witness (schnorr sig + `SIGHASH_SINGLE|ANYONECANPAY`) and reuse it in a new transaction that keeps the same input 0 and output 0 (both are covered by the sighash) but drops the OP_RETURN output entirely and swaps in the attacker's own fee-paying inputs, then RBF-replace and get it mined.

### Title
Honest operator's payout attribution can be stripped via SIGHASH_SINGLE|ANYONECANPAY malleability of the Payout transaction, causing the operator to be flagged malicious and lose its Reimburse path/collateral - ([File: core/src/builder/transaction/operator_reimburse.rs], [core/src/verifier.rs])

### Summary
The `Payout` transaction's sole input is signed by the withdrawing user with `TapSighashType::SinglePlusAnyoneCanPay`, which only commits to input 0 and output 0 [2](#0-1) ; the OP_RETURN operator-attribution output (index 2) and the anchor output (index 1), along with any additional operator-added funding inputs, are unsigned and freely malleable. An unprivileged attacker can lift this witness from the operator's unconfirmed mempool transaction and rebroadcast a variant that keeps output 0 unchanged but removes the OP_RETURN, replacing the original via RBF with a higher fee. Since the withdrawal's "payout tx" is later identified purely by whichever transaction spends the tracked withdrawal outpoint [3](#0-2) , the mined attacker variant is treated as the payout and yields no operator attribution.

### Finding Description
Binding claimed: `operator_xonly_pk` recovered from the confirmed Payout transaction's first OP_RETURN output == the operator who actually funded output 0 of that withdrawal.

The Payout tx is built with input 0 = withdrawal UTXO (key spend, `SpendPath::KeySpend`), output 0 = user payout, output 1 = anchor, output 2 = OP_RETURN(operator xonly pk) [4](#0-3) . The user's signature for input 0 is required to use `SinglePlusAnyoneCanPay` [2](#0-1) , and this signature is verified only against the sighash computed for input 0 with that sighash type [5](#0-4) . `SIGHASH_SINGLE|ANYONECANPAY` commits only to input 0 and output 0 — it does not commit to any other input, to output 1 (anchor), or to output 2 (OP_RETURN). The operator itself already relies on this malleability when funding the tx via `fund_raw_transaction` with `add_inputs: true` and inserting a change output at position 1 [6](#0-5) .

Exploit flow: The attacker observes the operator's broadcast-but-unconfirmed Payout tx in the mempool, extracts the witness for input 0 (valid `SinglePlusAnyoneCanPay` Schnorr signature over input0+output0), and constructs a new transaction: input 0 = the same withdrawal outpoint with the stolen witness, plus the attacker's own confirmed UTXO(s) as additional ANYONECANPAY inputs to pay fee; output 0 = byte-for-byte identical to the original (required, since it's covered by the sighash); outputs 1/2 (anchor, OP_RETURN) dropped or replaced with arbitrary attacker outputs. This is a valid, higher-feerate transaction directly conflicting on input 0, so it satisfies RBF replacement rules and can be mined instead of the operator's original.

Once mined, `update_finalized_payouts` locates "the" payout tx for this withdrawal purely by which tx spent the tracked withdrawal UTXO outpoint, via `get_payout_txs_for_withdrawal_utxos` joining on `bitcoin_syncer_spent_utxos.spending_txid` [3](#0-2) , not by matching the operator's original txid. It then calls `get_first_op_return_output` on the confirmed (attacker) tx, finds no OP_RETURN, and sets `operator_xonly_pk` to `None` in the DB [7](#0-6) . Downstream, `is_kickoff_malicious` reads `operator_xonly_pk_opt = None` and immediately returns `Ok(true)` ("assuming malicious") [8](#0-7) , triggering a Challenge tx against the honest operator's kickoff [9](#0-8) . The operator's own `send_asserts` path independently fails because it also depends on `get_payout_info_from_move_txid` returning `Some(operator_xonly_pk)`, erroring out when it is `None` [10](#0-9) , meaning the operator cannot correctly complete the assert/disprove sequence, exposing it to having its collateral burned via Disprove.

No existing guard prevents this: `withdraw()` only checks that the *intended* outpoint/output match Citrea's recorded withdrawal [11](#0-10) , but nothing pins the eventually-confirmed spending transaction to the specific txid the operator broadcast, nor to the presence of its OP_RETURN output.

### Impact Explanation
The withdrawal still gets paid to the correct recipient (output 0 is protected), so no bridge funds are misdirected at the payment level. However, the honest operator who genuinely fronted the withdrawal loses its ability to be recognized as the payer: `is_kickoff_malicious` treats it as malicious, triggers a Challenge, and `send_asserts` cannot proceed correctly, putting the operator's kickoff at risk of a Disprove that burns its collateral — matching the "honest operator's collateral burned" / "honest operator permanently unable to be reimbursed" critical impact categories. This is repeatable against any operator, for any withdrawal, as long as the attacker can observe an unconfirmed Payout tx in the mempool and outbid it via RBF; it does not require any privileged role, key, or majority hashrate — only the ability to broadcast transactions and pay a fee.

### Likelihood Explanation
Preconditions: an operator's Payout tx must be visible unconfirmed in the mempool (normal operational flow, no special timing needed beyond the ordinary confirmation delay), and the attacker needs a confirmed BTC UTXO to fund the replacement's fee (trivial, standard RBF-eligible funding — no unconfirmed-input issues since the attacker's own coin is already confirmed). No verifier/operator/aggregator key or collateral is required. This is straightforward to demonstrate on regtest without needing majority hashrate, live Citrea, or mainnet.

### Recommendation
Do not rely on "whichever tx spends the withdrawal outpoint" as the source of truth for operator attribution. Instead, either: (1) commit the operator's xonly pubkey and/or the specific payout txid inside a signature-covered part of the transaction (e.g., have the operator additionally sign/commit to the OP_RETURN output using a covering sighash, or use `SIGHASH_ALL` semantics protected by an operator-controlled input rather than solely the user's `SinglePlusAnyoneCanPay` signature), so the OP_RETURN cannot be stripped without invalidating the whole transaction; or (2) require operators to pre-register the exact payout txid they intend to broadcast (with anti-malleable commitments) before it's accepted as fulfilling a given withdrawal, and reject/refuse attribution for any other transaction that spends the same outpoint. Any fix must ensure the attacker cannot construct a validly-signed alternate spend of the withdrawal outpoint that omits or alters the OP_RETURN.

### Proof of Concept
```
cargo test — regtest-based, in core/src/test (background agent to add near existing payout/withdraw e2e tests):
1. Set up operator + withdrawal exactly as in deposit_and_withdraw_e2e.rs: create deposit, get withdrawal_utxo, user signs withdrawal_params with SinglePlusAnyoneCanPay.
2. Call operator.withdraw(...) (or the gRPC `withdraw`) to construct+broadcast the honest Payout tx; capture it from the mempool via rpc.get_tx_of_txid before it confirms.
3. Extract the witness for input 0 of the honest tx. Build a new transaction:
   - input 0: same withdrawal_utxo outpoint, witness copied from step 2 (valid signature reused, only input0/output0 committed)
   - input 1: an attacker-controlled confirmed UTXO to pay higher fee
   - output 0: byte-identical TxOut to honest tx's output 0
   - no OP_RETURN output
   Mark version/sequence for RBF (already NON_STANDARD_V3, sequence < 0xfffffffe).
4. rpc.send_raw_transaction(attacker_tx) with a higher fee than the honest tx; assert it replaces the honest tx in mempool (assert honest txid no longer in mempool).
5. Mine blocks until finalized; run bitcoin_syncer / verifier sync so update_finalized_payouts processes the block.
6. Assert: db.get_payout_info_from_move_txid(None, move_txid).await.unwrap().unwrap().0 == None (operator_xonly_pk is None), even though the operator was the actual honest payer.
7. Continue the kickoff flow and assert verifier's is_kickoff_malicious(...) returns true for the honest operator's kickoff_data, and/or operator.send_asserts(...) returns an Err("Payout operator xonly pk not found...").
```

### Citations

**File:** core/src/rpc/parser/operator.rs (L174-187)
```rust
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

**File:** core/src/operator.rs (L588-612)
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

**File:** core/src/operator.rs (L651-674)
```rust
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

**File:** core/src/verifier.rs (L1882-1885)
```rust
        let Some(operator_xonly_pk) = operator_xonly_pk_opt else {
            tracing::warn!("No operator xonly pk found in payout tx OP_RETURN, assuming malicious");
            return Ok(true);
        };
```

**File:** core/src/verifier.rs (L1987-2017)
```rust
        if is_malicious {
            tracing::warn!(
                "Malicious {} detected. {} Challenge tx: {} for deposit {}",
                kickoff_data,
                match challenged_before {
                    false => "This is the first malicious kickoff in the current round.",
                    true => "This is not the first malicious kickoff in the current round.",
                },
                bitcoin::consensus::encode::serialize_hex(&challenge_tx),
                deposit_outpoint
            );
            // do not automatically send challenge txs on mainnet or testnet4
            if !challenged_before
                && !matches!(
                    self.config.protocol_paramset().network,
                    bitcoin::Network::Bitcoin | bitcoin::Network::Testnet4
                )
            {
                #[cfg(feature = "automation")]
                self.tx_sender
                    .add_tx_to_queue(
                        dbtx,
                        TransactionType::Challenge,
                        &challenge_tx,
                        &[],
                        Some(tx_metadata),
                        self.config.protocol_paramset(),
                        None,
                    )
                    .await?;
            }
```

**File:** core/src/verifier.rs (L2312-2328)
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
```
