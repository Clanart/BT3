## Title
Unauthenticated operator attribution in `payout_tx` OP_RETURN is malleable under `SinglePlusAnyoneCanPay`, permanently locking an honest operator out of reimbursement — (File: `core/src/builder/transaction/operator_reimburse.rs`)

### Summary
`create_payout_txhandler` signs the user's withdrawal input with `TapSighashType::SinglePlusAnyoneCanPay`, which commits only to the input being spent and the output at the *same index* (index 0, the user's payout output). The OP_RETURN output that records which operator "fronted" the payout is placed at output index 2 and is therefore never covered by the user's signature. Any party can reuse the untouched, publicly-visible witness for input/output-0 to build a competing transaction with an arbitrary OP_RETURN, and get it confirmed instead of the honest operator's broadcast — hijacking the credit for a withdrawal someone else actually paid.

### Finding Description
`create_payout_txhandler` builds:
1. output[0]: user payout (SIGHASH_SINGLE-committed)
2. output[1]: anchor
3. output[2]: OP_RETURN(operator_xonly_pk) [1](#0-0) 

The user's signature is explicitly required/verified to use `SinglePlusAnyoneCanPay`: [2](#0-1) 

Under BIP-341/Bitcoin consensus rules, `SIGHASH_SINGLE|ANYONECANPAY` commits *only* to the single input being signed and the output at the same index (index 0 here). Outputs 1 and 2 — including the OP_RETURN carrying `operator_xonly_pk` — are completely outside the signature's scope, and `ANYONECANPAY` additionally allows arbitrary other inputs to be freely added/removed. This is confirmed by the codebase's own sighash construction logic, which special-cases `*PlusAnyoneCanPay` sighash types to only cover a single prevout: [3](#0-2) 

Downstream, the verifier trusts the OP_RETURN value taken straight from the confirmed on-chain transaction as the sole record of "who paid": [4](#0-3) 

This value is later used both to gate which operator is allowed to claim reimbursement (`is_kickoff_malicious` rejects a kickoff whose `operator_xonly_pk` doesn't match the DB-recorded payer) and to let each operator poll for its own unhandled payouts: [5](#0-4) [6](#0-5) 

Because the OP_RETURN is unsigned, an unprivileged attacker who observes the honest operator's unconfirmed `payout_tx` (mempool, relay, or even pre-broadcast if leaked) can construct a new transaction spending the *same* user UTXO with the *same* SIGHASH_SINGLE|ANYONECANPAY witness for input/output 0, but with a different (or bogus) OP_RETURN pubkey and its own fee-paying inputs, then get it confirmed instead (e.g. by paying a higher fee, since both transactions spend the same outpoint and only one can be mined). Confirmation of the attacker's version breaks the binding `operator credited == party that paid`: the honest operator fronted the BTC but the DB will forever attribute the payout to a different (or non-existent) `operator_xonly_pk`.

### Impact Explanation
This directly breaks a listed critical custody binding — "the operator credited versus the party that paid." Consequences:
- The honest operator who genuinely paid the user can never locate this payout via `get_first_unhandled_payout_by_operator_xonly_pk` (keyed on its own pubkey), so it can never trigger `get_reimbursement_txs`/`handle_finalized_payout` for it — the operator is **permanently unable to be reimbursed** for BTC it fronted.
- If the honest operator, unaware of the hijack, still sends a `Kickoff` tx to claim reimbursement, `is_kickoff_malicious` will flag the mismatch and mark it malicious, which routes into the challenge/disprove path — potentially resulting in **the honest operator's collateral being burned**.

Both outcomes map to the specified Critical impact categories.

### Likelihood Explanation
The attack requires no privileged role (no verifier/operator/watchtower/aggregator key), no majority hashrate, and no compromise of any secret. It only requires observing a not-yet-confirmed payout transaction (an inherently public artifact once it reaches the mempool or is relayed) and racing a fee-competitive replacement — an unprivileged, purely economic action available to any network participant.

### Recommendation
Use a sighash type that commits to all outputs (e.g. `SIGHASH_ALL` or `SIGHASH_ALL|ANYONECANPAY`) for the user's withdrawal signature, or otherwise cryptographically bind the OP_RETURN operator attribution to the signed input (e.g., have the aggregator/verifiers co-sign the operator pubkey as part of the withdrawal authorization, or have the user's off-chain signature explicitly commit to the intended operator's pubkey before it is handed out).

### Proof of Concept
1. Operator A calls `withdraw()`, builds `payout_tx` via `create_payout_txhandler` with `operator_xonly_pk = A`, gets the user's `SinglePlusAnyoneCanPay` signature verified over input/output-0 only, funds it via RBF-enabled `fund_raw_transaction`, and broadcasts it.
2. Attacker observes the unconfirmed tx (or its raw witness data) in the mempool/relay.
3. Attacker crafts `payout_tx'` reusing the identical committed input (outpoint + witness for input/output-0), keeps output[0] (user payout) identical (required by SIGHASH_SINGLE), but replaces output[2]'s OP_RETURN with an arbitrary/attacker-controlled pubkey `X`, and supplies its own fee inputs (`ANYONECANPAY` permits this).
4. Attacker gets `payout_tx'` confirmed first (e.g., via a higher fee).
5. `update_finalized_payouts` in `core/src/verifier.rs:2283-2352` records `payout_payer_operator_xonly_pk = X` for this withdrawal in the DB.
6. Operator A's `get_first_unhandled_payout_by_operator_xonly_pk(A)` never finds this payout; A cannot construct a valid, non-malicious `Kickoff`/reimbursement flow (`core/src/verifier.rs:1882-1890` will flag any attempt as malicious since the DB payer no longer matches A) — A is never reimbursed for the BTC it genuinely paid the user.

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

**File:** core/src/builder/transaction/txhandler.rs (L222-229)
```rust
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

**File:** core/src/verifier.rs (L2312-2335)
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
```

**File:** core/src/database/verifier.rs (L282-296)
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
```
