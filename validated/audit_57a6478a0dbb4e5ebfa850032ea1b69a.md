### Title
Withdrawer can forge the payout tx's `OP_RETURN` operator attribution independently of who actually funds/broadcasts it - ([File: core/src/verifier.rs::update_finalized_payouts / core/src/builder/transaction/operator_reimburse.rs::create_payout_txhandler])

### Summary
`Operator::withdraw`'s payout transaction is signed by the withdrawer using `SIGHASH_SINGLE|ANYONECANPAY`, which per BIP341 only commits to input 0 and output 0 [1](#0-0) . The `OP_RETURN` output that records the fronting operator's x-only pubkey is a separate, third output that is completely outside this signature's commitment [2](#0-1) . `Verifier::update_finalized_payouts` blindly trusts whatever `operator_xonly_pk` is parsed from that `OP_RETURN` in the mined transaction and stores it as `payout_payer_operator_xonly_pk` [3](#0-2) , with no check that this pubkey belongs to whoever actually funded the extra inputs/outputs of the transaction.

### Finding Description
Binding claimed: `input_signature` authorizes `(input_utxo, out_script_pubkey, out_amount)` for a specific operator == `withdrawals.payout_payer_operator_xonly_pk` recorded after the tx is mined.

Trace:
- The withdrawer signs only input 0 + output 0 via `SinglePlusAnyoneCanPay`; `calculate_pubkey_spend_sighash`/`calculate_script_spend_sighash` explicitly use `Prevouts::One` for this sighash flag, and Bitcoin's Taproot sighash algorithm for `SIGHASH_SINGLE|ANYONECANPAY` never includes any other input or output (including any `OP_RETURN`) in the digest [4](#0-3) .
- `create_payout_txhandler` places the operator attribution in a *third*, unsigned-by-the-user output (`op_return_txout`) [2](#0-1) . In the honest flow, `Operator::withdraw` always sets this to `self.signer.xonly_public_key` [5](#0-4) , but nothing on-chain or in the withdrawer's signature enforces that this value must equal the actual funder.
- `Verifier::update_finalized_payouts` reads whichever transaction ultimately confirms and unconditionally extracts and stores `operator_xonly_pk` from its first `OP_RETURN` output, explicitly acknowledging "an operator constructed the payout tx wrong" is a possibility but performing no funder-consistency check [6](#0-5) .
- `Verifier::is_kickoff_malicious` later trusts this DB value as ground truth to decide whether a kickoff by a given operator is legitimate, purely by pubkey equality, with no cross-check against who actually paid [7](#0-6) .

Exploit flow: the withdrawer (an unprivileged party who legitimately initiated the Citrea `withdraw` call and controls the dust `withdrawal_utxo`) signs input 0/output 0 with `SinglePlusAnyoneCanPay` as required by `parse_withdrawal_sig_params` [8](#0-7) . Instead of sending this signature to an operator, the attacker assembles and broadcasts the payout transaction directly on Bitcoin themselves, adding their own extra funding input(s)/change and a completely arbitrary third output (`OP_RETURN`) naming any x-only pubkey they choose — a real, uninvolved operator, or a nonexistent one. None of the signed fields are violated because `SIGHASH_SINGLE|ANYONECANPAY` never covers this output. Once mined, `update_finalized_payouts` records that chosen pubkey as `payout_payer_operator_xonly_pk`, regardless of the fact that this "operator" never funded or broadcast anything.

Existing guards fail because: `SECP.verify_schnorr` (in `Operator::withdraw`) only ever validates the honest operator's own path and is bypassed entirely if the attacker skips the operator's gRPC path and broadcasts on their own; `is_kickoff_malicious` only compares pubkeys already stored from the untrusted `OP_RETURN`; no code path anywhere checks that the party who added the non-signature-committed inputs (i.e. paid the fee/topped up funds) matches the `OP_RETURN` pubkey.

### Impact Explanation
Two concrete outcomes, both matching the Critical category list:
1. If the attacker names a real, unrelated registered operator, that operator's automation (which watches `get_first_unhandled_payout_by_operator_xonly_pk`) will treat this externally-funded payout as its own and proceed through the kickoff/BitVM reimbursement flow, claiming the deposit's `move-to-vault` UTXO reimbursement for a payout it never funded — "operator reimbursed for a payout it never funded."
2. If the attacker names a nonexistent/garbage x-only pubkey (or one belonging to an operator who will never notice/claim it), no operator ever picks up the payout as "unhandled" for their own key, and any operator who does attempt a kickoff for that deposit will be flagged malicious by `is_kickoff_malicious` since the pubkey mismatches — permanently blocking reimbursement and leaving the `move-to-vault` UTXO effectively stuck ("a move-to-vault UTXO permanently frozen").

This is repeatable per withdrawal/deposit at the discretion of whichever party assembles/broadcasts the final payout transaction, and does not require any privileged role, key compromise, or majority hashrate — only the withdrawer's own signing key over their own dust UTXO and enough BTC to cover the payout output/fees themselves.

### Likelihood Explanation
The attacker must be the withdrawer (or otherwise obtain a validly signed `SinglePlusAnyoneCanPay` witness for input 0/output 0 — which the protocol design explicitly hands out "off-chain" to any operator, making it non-secret by design). To realize the exploit, the attacker must front the withdrawal amount themselves (cost roughly equal to the amount they're withdrawing, which they already control since they initiated the withdrawal), plus normal transaction fees. This is fully client-side and requires no interaction with verifiers/operators, no majority hashrate, and no compromise of any key beyond the attacker's own withdrawal signing key. It is deterministic and reproducible for any deposit/withdrawal pair.

### Recommendation
Bind the `OP_RETURN` operator attribution to the actual authorization path: either (a) have the withdrawer's signature use a sighash type that also commits to the `OP_RETURN` output (e.g. `SIGHASH_SINGLE` is insufficient by definition; consider requiring the additional inputs/outputs, including the OP_RETURN, to be committed via a script-path condition enforced by a covenant, or by requiring the operator to co-sign an additional commitment covering the OP_RETURN value), or (b) have `Verifier::update_finalized_payouts`/`is_kickoff_malicious` independently verify that the party who supplied the additional (non-signature-committed) input(s) funding the payout is cryptographically tied to the `OP_RETURN` pubkey (e.g., require that operator's registered address/pubkey be used as one of the funding inputs, and validate that on-chain).

### Proof of Concept
```
cargo test forged_payout_op_return_attribution
```
Test plan:
1. Set up a deposit and withdrawal exactly as in existing withdrawal integration tests (`core/src/test/...` regtest harness), obtaining `input_utxo` (withdrawer's dust UTXO) and its owning keypair (the withdrawer/attacker's own key — no theft required).
2. Sign input 0 / output 0 with `TapSighashType::SinglePlusAnyoneCanPay` exactly as `sign_withdrawal_output` does, using `out_script_pubkey`/`out_amount` equal to the amount recorded via Citrea's `WithdrawParams`.
3. As the attacker, independently build a full transaction: add the signed input as input 0, add attacker-funded extra input(s)/change to cover `out_amount` + fees, output 0 = the signed payout output, output 1 = anchor, output 2 = `OP_RETURN` containing an arbitrary x-only pubkey `bystander_pk` (belonging to a legitimate, uninvolved test operator, not the real funder).
4. Broadcast and mine this transaction on regtest, bypassing `Operator::withdraw` entirely.
5. Run the verifier's block-sync path so `update_finalized_payouts` processes the block.
6. Assert in the `withdrawals` table: `payout_payer_operator_xonly_pk == bystander_pk`, even though `bystander_pk`'s operator never signed, funded, or broadcast anything — proving the binding "signed output authorizes payer == recorded payer" is violated.
7. Optionally continue the flow and assert that the bystander operator's automation (`get_first_unhandled_payout_by_operator_xonly_pk`) surfaces this payout as claimable by them, demonstrating the "reimbursed for a payout it never funded" impact.

### Citations

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

**File:** core/src/operator.rs (L620-626)
```rust
        let payout_txhandler = builder::transaction::create_payout_txhandler(
            input_utxo,
            output_txout,
            self.signer.xonly_public_key,
            in_signature,
            self.config.protocol_paramset().network,
        )?;
```

**File:** core/src/rpc/parser/operator.rs (L162-203)
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

    let input_outpoint: OutPoint = params
        .input_outpoint
        .ok_or_else(error::input_ended_prematurely)?
        .try_into()?;

    let users_intent_script_pubkey = ScriptBuf::from_bytes(params.output_script_pubkey);

    Ok((
        params.withdrawal_id,
        input_signature,
        input_outpoint,
        users_intent_script_pubkey,
        Amount::from_sat(params.output_amount),
    ))
}
```
