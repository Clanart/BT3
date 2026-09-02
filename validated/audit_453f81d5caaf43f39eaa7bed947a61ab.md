### Title
Payout OP_RETURN operator attribution is not covered by the user's `SinglePlusAnyoneCanPay` signature, allowing an unprivileged attacker to hijack payout-tx credit before confirmation - (File: `core/src/builder/transaction/operator_reimburse.rs`)

### Summary
`create_payout_txhandler` places the operator-attribution `OP_RETURN` output (output index 2) in a taproot key-spend transaction whose only signature is the user's `SinglePlusAnyoneCanPay` signature over input 0. Because `SIGHASH_SINGLE` only commits to the output at the same index as the signed input (index 0) and `ANYONECANPAY` only commits to the signed input itself, the OP_RETURN output (and any fee-paying inputs) is completely unauthenticated. An unprivileged attacker who observes an honest operator's unconfirmed payout transaction can rebuild a competing transaction that reuses the same input 0 (and its witness) and byte-for-byte output 0, but substitutes a different xonly pubkey in the OP_RETURN, and get it confirmed instead.

### Finding Description
The binding the system relies on is:
`operator_xonly_pk` stored by `Verifier::update_finalized_payouts`/`update_payout_txs_and_payer_operator_xonly_pk` for withdrawal `i` == the xonly pubkey of the operator whose funds actually paid output 0 of the confirmed payout tx for withdrawal `i`.

Trace:
- `create_payout_txhandler` (`core/src/builder/transaction/operator_reimburse.rs:407-436`) builds a `Payout` tx with input 0 = user's withdrawal UTXO (key-spend), output 0 = user payout, output 1 = anchor, output 2 = `OP_RETURN` with `operator_xonly_pk.serialize()`. Only input 0 is signed, via `set_p2tr_key_spend_witness(&user_sig, 0)`. [1](#0-0) 
- `Operator::withdraw` verifies the user's signature with `SECP.verify_schnorr` over `payout_txhandler.calculate_sighash_txin(0, in_signature.sighash_type)`, explicitly expecting `SinglePlusAnyoneCanPay`. [2](#0-1) 
- `calculate_pubkey_spend_sighash` uses `Prevouts::One(txin_index, prevout)` for `*PlusAnyoneCanPay` sighash types and calls `taproot_key_spend_signature_hash` with that sighash type. [3](#0-2)  Per BIP341, `SIGHASH_SINGLE` commits only to `outputs[input_index]` (i.e. output 0 here) and `ANYONECANPAY` commits only to the signed input's own outpoint/amount/scriptPubKey/sequence — it does not reference the total output count, output 1 (anchor), or output 2 (OP_RETURN) at all, and it does not reference any other input.
- After verification, `Operator::withdraw` calls `fund_raw_transaction` (adds extra fee inputs) then `sign_raw_transaction_with_wallet` to sign those extra inputs, and broadcasts. [4](#0-3)  This protects the *specific broadcast tx* from tampering by third parties who don't control those wallet inputs, but it does nothing to stop an attacker from building an *entirely separate* competing transaction from scratch that reuses only input 0 and its leaked witness (which is visible in the mempool/relay network the moment the honest operator broadcasts), adds the attacker's own fee inputs (signed by the attacker), keeps output 0 byte-for-byte, and rewrites output 2's OP_RETURN to an arbitrary xonly pubkey. This new transaction's input 0 signature check still passes because the sighash never depended on output 2 or on any other input.
- If this attacker transaction is the one that gets mined (double-spend race, RBF fee bump, or simple propagation race — this is stipulated as a precondition in the question and is a standard Bitcoin mempool/relay outcome, not something the Clementine code can prevent), `Verifier::update_finalized_payouts` reads the confirmed block, extracts `get_first_op_return_output` + `parse_op_return_data` from the *attacker's* transaction, and stores that attacker-chosen xonly pk via `update_payout_txs_and_payer_operator_xonly_pk`. [5](#0-4) 
- Later, `is_kickoff_malicious` compares `kickoff_data.operator_xonly_pk` (the honest operator A, who actually fronted output 0) against the DB-stored `operator_xonly_pk` (now the attacker's chosen value) and, since they mismatch, flags A's kickoff as malicious. [6](#0-5) 

No existing guard closes this gap: `Operator::withdraw`'s `SECP.verify_schnorr` only checks input 0 / output 0 as shown above; `is_deposit_valid`, `is_profitable`, `only_aggregator_and_self`, and DB uniqueness constraints do not constrain the content of an as-yet-unconfirmed competing transaction's OP_RETURN, because nothing in the protocol signs or otherwise commits to that output.

### Impact Explanation
The party that actually fronts a user's peg-out (spends real BTC into output 0) can be permanently denied reimbursement: `is_kickoff_malicious` will forever flag their kickoff as malicious once the DB's stored payer xonly pk for that withdrawal doesn't match theirs, since the payout tx is already confirmed with immutable OP_RETURN content. This matches the stated Critical category "an honest operator permanently unable to be reimbursed" (and, if the attacker instead points the OP_RETURN at a colluding-looking but non-funding operator, "an operator reimbursed for a payout it never funded" if that operator subsequently claims reimbursement). The attack is repeatable per withdrawal/operator: any withdrawal for which the honest operator's payout tx has not yet confirmed is exposed, so the blast radius scales with the volume of pending (unconfirmed) payouts, independent of deposit or operator identity.

### Likelihood Explanation
Preconditions: an honest operator has broadcast (but not yet confirmed) a payout tx for a registered Citrea withdrawal, and the attacker can observe that broadcast (any node on the Bitcoin P2P network, or the aggregator's mempool visibility) before it confirms. The attacker needs no special key material, collateral, or role — only the ability to construct and fee-bump a Bitcoin transaction (cost = attacker's own BTC for the competing fee, which is not tied to bridge funds). Whether the attacker's version actually gets mined depends on standard Bitcoin mempool/relay/RBF mechanics, which the question stipulates as a given precondition rather than something requiring proof here. Given that user withdrawal signatures are specified to use `SinglePlusAnyoneCanPay` — precisely the sighash flag that leaves the OP_RETURN unauthenticated — this is a structural weakness present on every payout, not a rare edge case.

### Recommendation
Bind the operator attribution to the same signature that authorizes spending the withdrawal UTXO, or otherwise make it tamper-evident: e.g. require `user_sig` sighash type `AllPlusAnyoneCanPay` (or `Default`) so the signature commits to *all* outputs (including the OP_RETURN), while still allowing `ANYONECANPAY` for extra fee inputs; alternatively, commit the operator xonly pk into a value the user's Citrea-side withdrawal request itself locks (so the operator can't be swapped post hoc by a third party), or have `is_kickoff_malicious`/`update_finalized_payouts` cross-check the payout tx's other inputs' signer to corroborate operator identity rather than trusting an unauthenticated OP_RETURN.

### Proof of Concept
```
cargo test (core/src/test, regtest harness) plan:
1. Spin up regtest with one operator A and register withdrawal index i's UTXO with a valid
   SinglePlusAnyoneCanPay user_sig via the existing withdrawal setup helpers
   (e.g. sign_withdrawal_output in core/src/test/common/setup_utils.rs).
2. Call Operator A's withdraw()/internal_withdraw() to build+broadcast payout_tx_A
   (input0 = withdrawal UTXO, output0 = user payout, output2 = OP_RETURN(A.xonly_pk)),
   but do NOT mine it yet.
3. Extract input 0's witness from payout_tx_A (the taproot signature+sighash byte).
4. Build payout_tx_attacker using create_payout_txhandler with:
   - same input_utxo (same outpoint)
   - identical output_txout (byte-for-byte output 0)
   - operator_xonly_pk = B.xonly_pk (a different xonly key, not A's)
   - user_sig copied from step 3
   then attach attacker-funded extra inputs/fee (attacker's own UTXOs) instead of A's.
5. Assert SECP.verify_schnorr still succeeds for input 0 of payout_tx_attacker
   (i.e., Operator::withdraw's own verification logic, replicated, does not reject it).
6. Broadcast payout_tx_attacker instead of payout_tx_A and mine it (simulating the
   attacker's tx winning the race).
7. Run Verifier::update_finalized_payouts on the mined block.
8. Assert get_payout_info_from_move_txid(move_txid) returns operator_xonly_pk == B.xonly_pk
   (i.e., != A.xonly_pk), proving the binding
   "stored payer xonly pk == funder of output 0 (A)" is broken.
9. Have Operator A produce its kickoff for this deposit and call is_kickoff_malicious;
   assert it returns true, proving A can never be reimbursed despite having funded output 0.
```

### Citations

**File:** core/src/builder/transaction/operator_reimburse.rs (L414-436)
```rust
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

**File:** core/src/operator.rs (L652-691)
```rust
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

**File:** core/src/verifier.rs (L2311-2350)
```rust
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

        self.db
            .update_payout_txs_and_payer_operator_xonly_pk(
                Some(dbtx),
                payout_txs_and_payer_operator_idx,
            )
            .await?;
```
