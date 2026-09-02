Confirmed. `Operator::withdraw` uses Bitcoin Core's `fund_raw_transaction` (with `add_inputs: true`, `change_position: 1`) to add the operator's own funding input(s) and change/anchor output to `payout_txhandler`, then signs and broadcasts it. The user's signature only covers input 0 and output 0 (`SinglePlusAnyoneCanPay`), so the operator's added funding input(s) and the OP_RETURN output (identifying the operator) are completely unauthenticated by that signature. [1](#0-0) [2](#0-1) [3](#0-2) 

### Title
Payout attribution via a mutable OP_RETURN lets anyone hijack or destroy reimbursement for a `SinglePlusAnyoneCanPay`-signed withdrawal - (File: `core/src/verifier.rs`, `core/src/operator.rs`)

### Summary
The `Payout` transaction created by `create_payout_txhandler` is only bound to input 0 and output 0 by the withdrawer's `SinglePlusAnyoneCanPay` signature; the operator's funding input(s) and the OP_RETURN carrying the fronting operator's xonly pubkey are unsigned/malleable. `update_finalized_payouts` (feeding `get_payout_info_from_move_txid`, consumed by `get_next_txs_to_send`/`validate_payer_is_operator`/`is_kickoff_malicious`) attributes the withdrawal's payer purely from whatever OP_RETURN happens to be in whichever transaction ends up spending the registered `withdrawal_utxo` on-chain - not from who actually supplied the value.

### Finding Description
The broken binding: **the party recorded in `withdrawals.payout_payer_operator_xonly_pk` for index i` MUST equal the party whose funds paid output 0 of the transaction that spends `withdrawal_utxo` i`.**

Trace:
1. `Operator::withdraw` builds `payout_txhandler` with a single signed input (the user's dust `withdrawal_utxo`, `SIGHASH_SINGLE|ANYONECANPAY`) and output 0 (user payout) already committed by the signature ( [3](#0-2) ). It then calls `fund_raw_transaction` with `add_inputs: true` to add the operator's own value input and a change output, and signs/broadcasts the result ( [2](#0-1) ). Because the signature is `ANYONECANPAY`, none of these added inputs, the anchor output, or the OP_RETURN (operator xonly pubkey) is covered by the signature - anyone possessing the same signed input 0 + output 0 (which the withdrawer trivially has, since they signed it themselves) can build a *different* valid transaction that reuses input 0/output 0 but supplies its own funding input(s) and an arbitrary OP_RETURN (a different real operator's pubkey, or unparsable/garbage data).
2. Attribution is derived purely from chain data: `get_payout_txs_for_withdrawal_utxos` finds whichever txid spent the registered `withdrawal_utxo_txid:vout`, with no reference to which specific transaction was originally broadcast by an operator ( [4](#0-3) ).
3. `update_finalized_payouts` then reads the OP_RETURN of that mined transaction and stores whatever xonly pubkey it parses (or `None` if absent/invalid) as `payout_payer_operator_xonly_pk` ( [5](#0-4) ).
4. This value is the sole source of truth used later by `validate_payer_is_operator` (feeding `get_next_txs_to_send`) to decide which operator may claim reimbursement ( [6](#0-5) ), and by `Verifier::is_kickoff_malicious` to decide whether a kickoff is legitimate ( [7](#0-6) ).

Exploit flow: An honest operator O_A broadcasts its funded `Payout` tx (reusing the withdrawer's signed input 0/output 0, adding O_A's own funding input, OP_RETURN=O_A). Before it confirms, the withdrawer (who already possesses their own signature over input 0/output 0 - no observation of O_A's mempool tx is even required) constructs a conflicting transaction spending the same `withdrawal_utxo`, supplying their own (or no, if amounts happen to be coverable) funding input, and setting the OP_RETURN to (a) a different real operator O_B's xonly pubkey, or (b) garbage bytes/no OP_RETURN. If this transaction confirms first:
- Case (a): O_B, who never funded anything, is recorded as payer, is credited by `PayoutCheckerTask`/`get_first_unhandled_payout_by_operator_xonly_pk`, and can proceed through kickoff/reimburse to be reimbursed from the deposit's `MoveToVault` UTXO for a payout it never made.
- Case (b): `operator_xonly_pk` becomes `None` and is stored permanently; `validate_payer_is_operator` will forever return "Payer info not found for deposit" for this withdrawal, and no operator can ever pass `is_kickoff_malicious`'s check requiring a matching non-`None` payer pubkey. Since `withdrawal_utxo` is already spent, no operator can re-front it either. The corresponding `MoveToVault` UTXO becomes permanently unreimbursable/frozen.

None of the existing guards prevent this: `SECP.verify_schnorr` in `withdraw`/`sign_optimistic_payout` only checks the signature over input 0/output 0, which remains valid under both the honest and hijacked transactions; there is no check tying the specific broadcast txid (or its OP_RETURN) to the operator that originally called `withdraw`; and `is_kickoff_malicious` only checks consistency between the (already-poisoned) DB record and the kickoff, not authenticity of that DB record's origin.

### Impact Explanation
- Frozen `MoveToVault` UTXO (Critical): if the attacker's replacement OP_RETURN is unparsable, the deposit's bridge funds become permanently unreimbursable by any operator, since `payout_payer_operator_xonly_pk` is fixed to `None` and the withdrawal outpoint is already spent, precluding retry.
- Operator reimbursed for a payout it never funded (Critical): if the attacker's replacement OP_RETURN names a real, uninvolved operator, that operator's automated `PayoutCheckerTask` will proceed to kickoff and claim reimbursement it never earned, draining the deposit's `MoveToVault` UTXO without a matching fronted payout.
- Repeatable per deposit/withdrawal; only requires the attacker to be the withdrawer (who always legitimately possesses the signed input/output 0) racing a normal operator's broadcast with a higher-fee, malleated transaction.

### Likelihood Explanation
The precondition is simply that a withdrawer wants to sabotage or redirect attribution of their own withdrawal payout - no special privileges, keys, or roles are needed beyond what any withdrawer already has (their own signature). Cost is limited to mining fees (and, for case (a)/(b), possibly self-funding the output if reusing none of the honest operator's added value, which is roughly break-even since the withdrawer receives their own payout back). This is straightforward to execute on regtest by racing two conflicting spends of the same `withdrawal_utxo` outpoint.

### Recommendation
Bind the OP_RETURN/operator identity to the signature or otherwise make the payout transaction non-malleable in the parts that matter for attribution: e.g., have the operator's funding input(s) be committed by an aggregated/operator-covering signature (or require the OP_RETURN commitment before the user reveals the `ANYONECANPAY` signature, e.g. via committing to a specific operator pubkey hash inside the citrea-side `withdraw` registration and checking the on-chain payout matches it), or require verifiers to co-sign the funding+OP_RETURN portion (similar to the optimistic-payout path where the MoveToVault input pins the transaction). At minimum, `update_finalized_payouts`/`get_payout_txs_for_withdrawal_utxos` should distinguish and require the exact transaction the querying operator broadcast (e.g. by tracking the specific txid returned from `Operator::withdraw`) rather than accepting any tx that happens to spend the registered outpoint.

### Proof of Concept
```
cargo test --test operator -- payout_op_return_can_be_hijacked
```
Plan:
1. Set up regtest bridge with a deposit and a registered `withdrawal_utxo` signed with `SinglePlusAnyoneCanPay` (as in `generate_withdrawal_transaction_and_signature`).
2. Call `Operator::withdraw` for operator O_A to obtain the funded, signed `payout_tx_A` (do not broadcast, or broadcast then immediately double-spend before confirmation).
3. Extract input 0 + witness + output 0 from `payout_tx_A`; build `payout_tx_evil` reusing them, adding attacker's own funding input and an OP_RETURN containing operator O_B's xonly pubkey (never called `withdraw`).
4. Mine `payout_tx_evil` (higher fee) instead of `payout_tx_A`.
5. Run the block-sync/`update_finalized_payouts` path and assert:
   - `db.get_payout_info_from_move_txid(...).0 == Some(O_B_xonly_pk)` (equality binding broken: credited party != funder O_A).
6. Repeat with a garbage/no OP_RETURN and assert `get_payout_info_from_move_txid(...).0 == None`, then assert `Operator::validate_payer_is_operator` (or `get_next_txs_to_send`) returns an error for every operator, demonstrating permanent unreimbursability of the `MoveToVault` UTXO.

### Citations

**File:** core/src/operator.rs (L620-637)
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
```

**File:** core/src/operator.rs (L651-691)
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
