Confirmed: `create_reimburse_txhandler` at `core/src/builder/transaction/operator_reimburse.rs:341-385` shows the reimbursement pays the operator (whoever's key controls the `RoundTx`/`KickoffTx` chain and can generate a valid `Kickoff` tx) an amount equal to the full `move_txhandler`'s `DepositInMove` output value — i.e., directly from the bridge's deposited collateral, not from any pool tied to "whoever actually funded the payout." This confirms the reimbursement is paid from bridge deposit funds regardless of who funded the front, based on this analysis I can now finalize.

### Title
Payout OP_RETURN operator attribution is self-declared and unauthenticated, letting any payer credit reimbursement to an arbitrary operator who never funded the payout - (File: core/src/verifier.rs, core/src/database/verifier.rs)

### Summary
`Verifier::update_finalized_payouts` derives `payout_payer_operator_xonly_pk` purely from bytes an attacker fully controls in the payout transaction's OP_RETURN output, with no cryptographic binding to who actually funded the payout's output value. Because the withdrawal UTXO is spent via a `SIGHASH_SINGLE|ANYONECANPAY` signature that only commits to the input and the user-payout output (not the OP_RETURN output or any funding inputs), any unprivileged party who obtains the user's withdrawal signature (which they inherently have, since the attacker is the withdrawing user calling Citrea's `withdraw`) can front their own withdrawal using their own funds while stamping an arbitrary honest operator's `XOnlyPublicKey` into the OP_RETURN, mis-crediting that operator in the `withdrawals` table.

### Finding Description
The broken binding, stated explicitly: `payout_payer_operator_xonly_pk` stored for withdrawal `idx` should equal the xonly public key of the party whose funds/signature actually paid the withdrawal output, but the code only enforces `payout_payer_operator_xonly_pk == bytes decoded from the payout tx's first OP_RETURN output`.

In `update_finalized_payouts` [1](#0-0) , the operator attribution is computed solely as:
```
operator_xonly_pk = op_return_output.and_then(parse_op_return_data).and_then(XOnlyPublicKey::from_slice)
```
and written unconditionally via `update_payout_txs_and_payer_operator_xonly_pk` [2](#0-1) . No signature, presigned transaction, or funding-input check ties this OP_RETURN value to the actual payer.

The payout transaction's only input is spent via `SpendPath::KeySpend` using `user_sig`, a `SinglePlusAnyoneCanPay` Schnorr signature verified against the user's own key in `Operator::withdraw` [3](#0-2)  and in `create_payout_txhandler` [4](#0-3) . `SinglePlusAnyoneCanPay` only commits to this one input and its corresponding output (index 0); it does not commit to the OP_RETURN output (index 2) or to any additional funding inputs. Consequently, whoever holds `(withdrawal_id, input_signature, input_outpoint, output_script_pubkey, output_amount)` — which per the threat model the attacker inherently has, being the user who calls `withdraw` on the Citrea contract and supplies exactly these fields as `WithdrawParams` [5](#0-4)  — can independently construct and broadcast a payout tx, funding the large output from their own wallet, while writing any operator's real `XOnlyPublicKey` into the OP_RETURN.

Downstream, `is_kickoff_malicious` [6](#0-5)  and `send_asserts` [7](#0-6)  only check that the OP_RETURN-derived key equals `kickoff_data.operator_xonly_pk` — i.e., internal consistency between the DB's self-declared attribution and whichever operator's kickoff shows up — never that the credited operator's key/funds were used to construct the payout input. `PayoutCheckerTask` then automatically triggers `handle_finalized_payout` for whichever operator's own `xonly_pk` matches the forged/self-declared field [8](#0-7) , which proceeds through `get_reimbursement_txs` / `create_reimburse_txhandler` to pay the full deposited collateral amount to that operator's `reimburse_addr` [9](#0-8) , regardless of whether that operator ever spent a satoshi funding the payout.

### Impact Explanation
This matches the Critical category "an operator reimbursed for a payout it never funded." The framed operator receives the full bridge-deposited collateral (`move_txhandler`'s `DepositInMove` value) from `create_reimburse_txhandler`, triggered purely by automation reacting to a DB field the attacker unilaterally controls, without ever contributing capital to that specific withdrawal. This is repeatable per withdrawal the attacker (as the withdrawing user) controls and can target any operator whose public xonly key is known (all operator keys are public). It does not cost the attacker extra BTC beyond what they'd already pay to self-front their own withdrawal, and it forces an uninvolved operator's automation into an unwanted, on-chain kickoff/reimbursement cycle and collateral consumption.

### Likelihood Explanation
Preconditions are met by design of the withdraw flow: the attacker is the withdrawing user, who legitimately possesses `in_signature`/`in_outpoint`/`output_script_pubkey`/`output_amount` needed to build a valid `SinglePlusAnyoneCanPay` payout. No special access, no majority hashrate, no verifier/operator privilege is required — just standard Bitcoin fee payment to fund and mine the transaction. This is straightforward and repeatable for every withdrawal the attacker initiates.

### Recommendation
Do not treat the OP_RETURN pubkey as authoritative attribution by itself. Bind the OP_RETURN-declared operator to independent proof that operator's own key/funds paid the output — for example, require an additional operator signature over the payout tx (or over the OP_RETURN commitment) verifiable with `SECP.verify_schnorr` against the claimed `operator_xonly_pk`, and reject/ignore attribution when this proof is absent, rather than trusting unauthenticated OP_RETURN bytes.

### Proof of Concept
`cargo test` plan (extends `core/src/database/verifier.rs` test module and/or `core/src/test/deposit_and_withdraw_e2e.rs`):
1. Run a normal deposit + withdrawal setup so a `withdrawal_utxo` and `in_signature` (SinglePlusAnyoneCanPay) exist for the withdrawing user.
2. Instead of calling operator's `withdraw`, directly build a payout tx (mirroring `create_payout_txhandler`) using the user's `in_signature`, funding the output from a non-operator test wallet, but setting the OP_RETURN to Operator B's real `xonly_pk` (Operator B never signs or funds anything).
3. Broadcast and mine this tx; run `update_finalized_payouts`.
4. Assert `Database::get_payout_info_from_move_txid` returns `payout_payer_operator_xonly_pk == Some(operator_b_xonly_pk)` even though Operator B contributed no signature/funds — demonstrating the binding `payout_payer_operator_xonly_pk == actual funder` is violated.
5. Continue the flow via `PayoutCheckerTask`/`handle_finalized_payout` for Operator B and assert `create_reimburse_txhandler`'s output pays Operator B the full deposit amount, confirming reimbursement is granted absent any operator-side signature/funding check.

### Citations

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

**File:** core/src/database/verifier.rs (L226-248)
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

**File:** core/src/builder/transaction/operator_reimburse.rs (L341-385)
```rust
pub fn create_reimburse_txhandler(
    move_txhandler: &TxHandler,
    round_txhandler: &TxHandler,
    kickoff_txhandler: &TxHandler,
    kickoff_idx: usize,
    paramset: &'static ProtocolParamset,
    operator_reimbursement_address: &bitcoin::Address,
) -> Result<TxHandler, BridgeError> {
    let builder = TxHandlerBuilder::new(TransactionType::Reimburse)
        .with_version(NON_STANDARD_V3)
        .add_input(
            NormalSignatureKind::Reimburse1,
            move_txhandler.get_spendable_output(UtxoVout::DepositInMove)?,
            builder::script::SpendPath::ScriptSpend(0),
            DEFAULT_SEQUENCE,
        )
        .add_input(
            NormalSignatureKind::Reimburse2,
            kickoff_txhandler.get_spendable_output(UtxoVout::ReimburseInKickoff)?,
            builder::script::SpendPath::ScriptSpend(0),
            DEFAULT_SEQUENCE,
        )
        .add_input(
            NormalSignatureKind::OperatorSighashDefault,
            round_txhandler.get_spendable_output(UtxoVout::ReimburseInRound(
                kickoff_idx,
                paramset.num_kickoffs_per_round,
            ))?,
            builder::script::SpendPath::KeySpend,
            DEFAULT_SEQUENCE,
        );

    Ok(builder
        .add_output(UnspentTxOut::from_partial(TxOut {
            value: move_txhandler
                .get_spendable_output(UtxoVout::DepositInMove)?
                .get_prevout()
                .value,
            script_pubkey: operator_reimbursement_address.script_pubkey(),
        }))
        .add_output(UnspentTxOut::from_partial(
            builder::transaction::anchor_output(paramset.anchor_amount()),
        ))
        .finalize())
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

**File:** core/src/rpc/clementine.proto (L239-253)
```text
message WithdrawParams {
  // The ID of the withdrawal in Citrea
  uint32 withdrawal_id = 1;
  // User's [`bitcoin::sighash::TapSighashType::SinglePlusAnyoneCanPay`]
  // signature
  bytes input_signature = 2;
  // User's UTXO to claim the deposit
  Outpoint input_outpoint = 3;
  // The withdrawal output's script_pubkey (user's signature is only valid for
  // this pubkey)
  bytes output_script_pubkey = 4;
  // The withdrawal output's amount (user's signature is only valid for this
  // amount)
  uint64 output_amount = 5;
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
