The parallel between the GMX report and Clementine is not "state updated after checks" but a similar class of bug: **an on-chain commitment used for downstream attribution (which operator gets reimbursed) is not actually bound by the signature that authorizes the transaction**, so the value that ends up encoded on-chain can diverge from the truth. Concretely:

### Title
Payout attribution (`OP_RETURN` operator pubkey) is not covered by the user's `SinglePlusAnyoneCanPay` signature, allowing payout-credit hijacking - (File: `core/src/builder/transaction/operator_reimburse.rs`)

### Summary
The payout transaction that fronts a Citrea withdrawal commits the "paying operator" identity only in an unsigned `OP_RETURN` output. The user's authorizing signature uses `SIGHASH_SINGLE | ANYONECANPAY`, which binds only input `0` to output `0` (the user's payout output) and leaves every other output — including the `OP_RETURN` operator-attribution output — completely unconstrained.

### Finding Description
`create_payout_txhandler` builds output 0 (user payout), output 1 (anchor), and output 2 (`OP_RETURN` operator_xonly_pk) [1](#0-0) . The operator calls `Operator::withdraw`, which verifies the user's signature against `sighash_type = in_signature.sighash_type` and explicitly documents that it must be `SinglePlusAnyoneCanPay` [2](#0-1) ; this is also enforced at the RPC parsing layer [3](#0-2) .

For `SinglePlusAnyoneCanPay`, the sighash calculation uses `Prevouts::One(txin_index, ...)` [4](#0-3) , which is standard Taproot `SIGHASH_SINGLE|ANYONECANPAY` semantics: the signature commits only to input 0 and the output at the *same index* (output 0). It does **not** commit to output 1 (anchor) or output 2 (`OP_RETURN`), and `ANYONECANPAY` permits arbitrary additional inputs to be attached by anyone holding the signature.

Downstream, the verifier's block-sync logic reads whichever `OP_RETURN` ends up mined and treats it as ground truth for "who fronted this withdrawal": `update_finalized_payouts` parses the mined payout tx's `OP_RETURN` into `operator_xonly_pk` and persists it as the payer [5](#0-4) . That persisted payer is later used to gate reimbursement (`validate_payer_is_operator` compares it to `self.signer.xonly_public_key` before releasing reimbursement transactions) [6](#0-5) , and to determine whether a kickoff is malicious by comparing the committed payer key to the kickoff's operator (`is_kickoff_malicious`) [7](#0-6) .

Because the `OP_RETURN` (the only field that attributes fronting-credit) is not part of the signed message, any party who obtains the honest operator's constructed/broadcast (but non-standard, `NON_STANDARD_V3`) payout transaction and its `in_signature` can rebuild an alternative transaction that:
- keeps output 0 identical (so the user still receives their exact requested payout — the signature still validates),
- replaces output 2's `OP_RETURN` with a different `operator_xonly_pk`,
- adds/omits additional inputs freely (via `ANYONECANPAY`) to cover any funding gap,

and get that version mined instead of the original.

**Binding broken:** `operator credited in OP_RETURN == operator that actually funded/intended the payout`. Before the attack these are equal (whoever calls `withdraw()` and funds the tx). After the attack, the operator attributed on-chain is not necessarily the operator whose Bitcoin actually flowed into the payout, or is a different registered operator than the one that raced to serve the withdrawal first.

### Impact Explanation
If a second operator captures the signature (e.g., by observing it in a shared relay/mempool for non-standard transactions, or via any off-chain leak — the same `in_signature` given to "operators" off-chain per the code's own comments) and gets their own version mined first, they become the party recorded as `payer_xonly_pk` in `update_finalized_payouts`. They can then pass `validate_payer_is_operator` and claim reimbursement through the kickoff/challenge/reimburse flow for a withdrawal they did not genuinely front (or fronted with minimal marginal cost, since output 0's amount, which is what actually goes to the user, was already fixed by the original signature). Simultaneously, the honest operator who prepared/attempted the original transaction can never claim reimbursement for it, because `validate_payer_is_operator` and `is_kickoff_malicious` will reject their kickoff (payer key mismatch) — this is "an operator reimbursed for a payout it never funded" and "an honest operator permanently unable to be reimbursed," both Critical-severity outcomes per the impact list.

### Likelihood Explanation
This requires no privileged role: any party (including a rival operator or any actor who can observe the raw signed payout transaction before/around confirmation) can perform this substitution, since Taproot's `SIGHASH_SINGLE|ANYONECANPAY` semantics are being relied upon by design here, but the protocol never re-signs or independently commits the operator-attribution output. The primary constraint is getting the substituted, non-standard version mined in place of the original — feasible for a registered operator with mining/relay access to bypass standardness relay policy in the same way the honest operator must (both must submit non-standard `NON_STANDARD_V3` transactions out-of-band), making this realistic among the operator set without requiring any key compromise.

### Recommendation
Bind the operator-attribution output to the same signature that authorizes spending the withdrawal UTXO — e.g., require `SIGHASH_ALL` (or otherwise cryptographically commit all outputs, including the `OP_RETURN`) instead of `SinglePlusAnyoneCanPay`, or have the operator co-sign the full transaction (all outputs) rather than relying solely on the unauthenticated `OP_RETURN` field for attribution.

### Proof of Concept
1. Operator A calls `withdraw()`, which builds and signs (via the user's provided signature) a payout tx `T_A` with output0 = user payout, output1 = anchor, output2 = `OP_RETURN(A_xonly_pk)`, and submits `T_A` (as a non-standard v3 tx) for inclusion [8](#0-7) .
2. Operator B obtains the same `in_signature` (valid `SinglePlusAnyoneCanPay`) and the same `input_utxo`/`output_txout` (both public/derivable from the Citrea withdrawal request).
3. Operator B constructs `T_B` with an identical output0 (so `sighash` verification passes with the same signature — output0 is what's committed by `SIGHASH_SINGLE`), but output2 = `OP_RETURN(B_xonly_pk)`, adding whatever additional inputs `ANYONECANPAY` allows to fund `T_B`.
4. Operator B gets `T_B` mined instead of `T_A`.
5. `update_finalized_payouts` parses the mined tx's `OP_RETURN`, records `B_xonly_pk` as payer [5](#0-4) .
6. Operator B calls `get_reimbursement_txs`/`validate_payer_is_operator` and passes, claiming reimbursement [6](#0-5) ; Operator A's later kickoff for the same deposit fails `is_kickoff_malicious`/payer checks and cannot be reimbursed.

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

**File:** core/src/operator.rs (L620-674)
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
```

**File:** core/src/operator.rs (L1703-1719)
```rust
        // first check if the payer is the operator, and the kickoff is handled
        // by the PayoutCheckerTask, meaning kickoff_txid is set
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
```

**File:** core/src/rpc/parser/operator.rs (L161-187)
```rust
#[allow(clippy::result_large_err)]
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

**File:** core/src/builder/transaction/txhandler.rs (L220-229)
```rust
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
