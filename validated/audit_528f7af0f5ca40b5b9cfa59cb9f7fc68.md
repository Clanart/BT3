### Title
Payout attribution relies solely on an unauthenticated OP_RETURN output, letting anyone with the withdrawer's own SinglePlusAnyoneCanPay signature front-run/replace the operator's payout and permanently orphan its reimbursement - ([File: core/src/builder/transaction/operator_reimburse.rs])

### Summary
`create_payout_txhandler` signs the payout input with `SIGHASH_SINGLE|ANYONECANPAY`, which binds the signature only to input0's outpoint/amount/script and to output0 (the user payout); output1 (anchor) and, critically, output2 (the OP_RETURN carrying the operator's x-only pubkey) are completely unauthenticated. Because the withdrawer (an unprivileged caller of `withdraw`/`internal_withdraw`) always possesses a valid signature for input0+output0 the moment they craft the withdrawal request, they can independently build and broadcast a competing transaction that reuses that exact signature/outpoint/output0 but supplies their own funding input(s) and a stripped or garbled OP_RETURN, and win the confirmation race against the operator's identical, properly-attributed payout tx.

### Finding Description
The binding this exploit breaks is: `withdrawals.payout_payer_operator_xonly_pk == operator.signer.xonly_public_key` for the withdrawal that the honest operator actually funded via `fund_raw_transaction`/broadcast in `Operator::withdraw` [1](#0-0) .

- `parse_withdrawal_sig_params` accepts a 64-byte "Default" sighash signature and silently rewrites it to `SinglePlusAnyoneCanPay` for backward compatibility, then only enforces that final type [2](#0-1) .
- `Operator::withdraw` checks `withdrawal_utxo == input_utxo.outpoint`, builds `create_payout_txhandler`, and verifies the user's signature over `payout_txhandler.calculate_sighash_txin(0, in_signature.sighash_type)` [3](#0-2) .
- `create_payout_txhandler` places output0 = user payout, output1 = anchor, output2 = OP_RETURN with the operator's x-only pubkey, and only sets the taproot key-spend witness on input 0 [4](#0-3) . Under `SIGHASH_SINGLE|ANYONECANPAY`, the signature commits to output0 only and to no other inputs — outputs 1 and 2 and any additional inputs are unauthenticated by design.
- Downstream, `update_finalized_payouts` derives the credited operator purely from whichever transaction actually confirms spending the withdrawal outpoint, parsing its OP_RETURN; if the OP_RETURN is missing or invalid it stores `operator_xonly_pk = NULL` [5](#0-4) .
- `get_first_unhandled_payout_by_operator_xonly_pk` (used by `PayoutCheckerTask`) only matches rows whose `payout_payer_operator_xonly_pk` equals the operator's own key [6](#0-5) , and `validate_payer_is_operator` / `is_kickoff_malicious` both hard-fail (or flag as malicious) when that attribution is `None` or mismatched [7](#0-6) [8](#0-7) .

Because the withdrawing party controls `in_signature`, `in_outpoint`, `out_script_pubkey`, and `out_amount` before ever contacting the operator, they already hold a valid `SinglePlusAnyoneCanPay` signature for input0+output0. They can independently assemble a second transaction spending the same `input_outpoint`, reusing that signature and output0 unchanged (required for signature validity), but supplying their own funding input(s) and a garbled/omitted OP_RETURN at output2, then win the mining race with a higher fee. None of the existing guards (`withdrawal_utxo == input_utxo.outpoint`, `is_profitable`, `SECP.verify_schnorr`) inspect or constrain output2, since it is outside the signed message.

### Impact Explanation
If the attacker's unattributed transaction confirms first, the operator's identical, properly-formed payout transaction is permanently orphaned (its input is already spent), so `payout_payer_operator_xonly_pk` for that withdrawal becomes `NULL` (or a wrong value) in the database. `PayoutCheckerTask::run_once` never selects this withdrawal for the real operator, so `handle_finalized_payout`/kickoff is never triggered for that operator, and `validate_payer_is_operator` errors out ("Payer info not found") if attempted. If the operator nonetheless attempts a kickoff, `Verifier::is_kickoff_malicious` will flag it as malicious (no matching operator xonly pk / no payout info), which can lead to collateral burning. The user still receives their payout (output0 is unchanged), so the harm falls entirely on the honest operator who fronted funds for this specific withdrawal and is left with no path to reimbursement — matching the Critical category "an honest operator permanently unable to be reimbursed" (and potentially "an honest operator's collateral burned"). This is repeatable per withdrawal/operator, since the OP_RETURN is unauthenticated for every payout tx built by `create_payout_txhandler`.

### Likelihood Explanation
The attack requires the attacker to win a fee-based mining race and to independently fund output0's amount (or the portion not already covered by `input_utxo`'s own value) plus fees, since `SIGHASH_SINGLE|ANYONECANPAY` lets them swap in their own funding inputs freely. When the withdrawer is the attacker (most natural case, since they control the signature and outpoint from the start), their real added cost is limited to the differential the operator would otherwise have fronted plus a competitive fee bump — this cost is deployment/parameter dependent (`is_profitable`'s `withdrawal_diff`/`bridge_amount_sats`/`operator_withdrawal_fee_sats` thresholds) and can be small when `input_utxo` already covers most of the payout amount. No mainnet, verifier majority, or key compromise is required; only mempool visibility/fee competition and BTC to cover the funding gap and fees, which is realistic for a determined party wanting to specifically deny a target operator's reimbursement. I was unable to fully confirm within this session what sighash scheme the operator's own additional `fund_raw_transaction`-added inputs use when signed (this only affects whether a literal "copy the operator's broadcast tx and mutate output2" variant is blocked by `SIGHASH_ALL`; it does not affect the independently-constructed competing-transaction variant, which needs no dependency on the operator's tx internals at all).

### Recommendation
Bind the operator attribution cryptographically instead of relying on an unauthenticated OP_RETURN: e.g., require the withdrawer's signature to cover the OP_RETURN output too (use `SIGHASH_ALL` or a custom sighash covering all outputs once the operator's xonly pk is embedded, or have the aggregator co-sign a commitment that ties `withdrawal_index` to a specific operator before the payout is broadcast), and/or have `update_finalized_payouts`/`is_kickoff_malicious` fall back to a database-level "first valid payout wins" registration made atomically with an operator-authenticated pre-commitment (e.g., verification signature from the aggregator recorded before broadcast) rather than trusting whatever unauthenticated OP_RETURN happens to confirm on-chain.

### Proof of Concept
```
cargo test devin_op_return_race -- --nocapture
```
Plan for the test (to be executed by an engineer with regtest access):
1. Set up a regtest deposit and a withdrawal (`withdrawal_index`, `in_outpoint`, `in_signature` with `SinglePlusAnyoneCanPay`, `out_script_pubkey`, `out_amount`) such that `input_utxo` covers most of `out_amount` (minimal operator funding needed).
2. Call `Operator::withdraw` (via `ClementineOperator::withdraw` gRPC) to get the operator's real, correctly-attributed payout tx (`operator_tx`) built and broadcast; capture it from the operator's `fund_raw_transaction`/broadcast call before it confirms.
3. Independently build `attacker_tx` reusing the same `input_outpoint` and the same witness/signature for input 0, the identical output0 (`out_script_pubkey`/`out_amount`), but with the attacker's own funding input(s) and a stripped/garbled OP_RETURN at output2 (or omit it), with a higher fee rate.
4. Mine `attacker_tx` first (regtest `generatetoaddress`) instead of `operator_tx`; assert `operator_tx` is rejected as a double-spend when later submitted.
5. Assert: `db.get_payout_info_from_move_txid` for the deposit returns `operator_xonly_pk == None` (or mismatched) after `update_finalized_payouts` runs.
6. Assert: `PayoutCheckerTask::run_once` returns `Ok(false)` (no unhandled payout found for the real operator) and `db.get_first_unhandled_payout_by_operator_xonly_pk(operator.signer.xonly_public_key)` returns `None`, proving the real operator's fronted withdrawal is never marked handled and no kickoff/reimbursement path is triggered.

### Citations

**File:** core/src/operator.rs (L588-637)
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

        // tracing::info!("Payout txhandler: {:?}", hex::encode(bitcoin::consensus::serialize(&payout_txhandler.get_cached_tx())));

        let sighash = payout_txhandler.calculate_sighash_txin(0, in_signature.sighash_type)?;

        SECP.verify_schnorr(
            &in_signature.signature,
            &Message::from_digest(*sighash.as_byte_array()),
            user_xonly_pk,
        )
        .wrap_err("Failed to verify signature received from user for payout txin. Ensure the signature uses SinglePlusAnyoneCanPay sighash type.")?;
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

**File:** core/src/rpc/parser/operator.rs (L162-187)
```rust
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

**File:** core/src/verifier.rs (L1875-1890)
```rust
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

**File:** core/src/database/verifier.rs (L282-298)
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
```
