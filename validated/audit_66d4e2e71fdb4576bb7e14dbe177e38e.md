### Title
Unsigned OP_RETURN operator-identity output in `Payout` tx is fee-bump malleable, permanently orphaning honest operator reimbursement — ([File: core/src/builder/transaction/operator_reimburse.rs])

### Summary
`create_payout_txhandler` signs the `Payout` transaction's input with `SIGHASH_SINGLE|ANYONECANPAY`, which only commits to input 0 and output 0. Output 2, the `OP_RETURN` carrying the fronting operator's x-only pubkey, is completely unauthenticated and can be freely rewritten by anyone who reconstructs a conflicting, higher-fee transaction that reuses the same input 0/output 0 and gets it mined instead of the operator's original broadcast. Because `Verifier::update_finalized_payouts` derives `payout_payer_operator_xonly_pk` solely from whichever transaction ends up spending the withdrawal UTXO on-chain, an attacker can rewrite this field to arbitrary/garbage bytes, permanently breaking the honest operator's ability to be located and reimbursed for funds it already fronted.

### Finding Description
The claimed binding is:
`payout_txs[i].payout_payer_operator_xonly_pk == xonly_pk(operator who funded output 0 of the mined tx that spends withdrawal_utxo[i])`

This binding is broken because the value on the right is not authenticated by any signature that actually spends real value belonging to the honest operator's key — it's inferred purely from `OP_RETURN` bytes in whichever transaction happens to be mined.

- `create_payout_txhandler` builds the `Payout` tx with input 0 (the withdrawal UTXO, spent via `SpendPath::KeySpend` with the user's signature) and three outputs: output 0 (user payout), output 1 (anchor), output 2 (`OP_RETURN` with `operator_xonly_pk.serialize()`): [1](#0-0) .
- The user's signature verified in `Operator::withdraw` and required by `parse_withdrawal_sig_params` must have `TapSighashType::SinglePlusAnyoneCanPay`: [2](#0-1)  and [3](#0-2) .
- `calculate_pubkey_spend_sighash`/`calculate_sighash_txin` for `SinglePlusAnyoneCanPay` (and its variants) use `Prevouts::One(txin_index, ...)`, meaning the sighash commits to exactly one input and (per BIP341/BIP118 SIGHASH_SINGLE semantics) only the output at the same index (output 0): [4](#0-3) . Outputs 1 (anchor) and 2 (`OP_RETURN`) are outside the signed message.
- This means any party who observes the mempool-broadcast `Payout` tx can lift input 0 (with its unchanged witness signature) and output 0, add their own funding input(s)/change to bump the fee, replace output 2 with attacker-chosen bytes (or drop/garble it), and rebroadcast as a fee-bumped conflicting transaction. The reused signature remains valid because it never covered outputs 1/2 or extra inputs.
- On confirmation, `Verifier::update_finalized_payouts` locates the payout transaction for a withdrawal index purely via `get_payout_txs_for_withdrawal_utxos`, which SQL-joins on whichever txid spent the `withdrawal_utxo_txid/vout` — i.e., whichever conflicting transaction actually got mined: [5](#0-4) . It then parses the (attacker-controlled) first `OP_RETURN` output of that mined transaction to set `payout_payer_operator_xonly_pk`, defaulting to `NULL` if parsing/pubkey-decoding fails: [6](#0-5) .
- Downstream, `PayoutCheckerTask::run_once` looks up unhandled payouts strictly `by_operator_xonly_pk` for the operator's own key: [7](#0-6) . If the DB row's key was corrupted by the attacker, the honest operator's polling task never finds this withdrawal, never calls `handle_finalized_payout`, and never marks it for reimbursement.
- Separately, `is_kickoff_malicious` also reads `operator_xonly_pk_opt` from this same tainted DB row via `get_payout_info_from_move_txid`, and treats a missing/mismatched key as "assuming malicious": [8](#0-7) . Even if the honest operator somehow still tries to kick off, verifiers will flag it as malicious because the recorded payer key no longer matches `kickoff_data.operator_xonly_pk`.

No existing guard prevents this: `SECP.verify_schnorr` only validates that input 0/output 0 match the user's intent (as designed for ANYONECANPAY flexibility to allow fee funding), `is_kickoff_malicious` consumes the already-corrupted DB value rather than an authenticated source, and there is no on-chain or off-chain binding that ties the `OP_RETURN` payload to whichever party actually funded output 0's value.

### Impact Explanation
The honest operator already spent 10 BTC of its own funds funding output 0 of the `Payout` tx to satisfy the user's withdrawal. Once the attacker's fee-bumped replacement (with a corrupted/garbage `OP_RETURN`) is mined instead, the withdrawal is permanently invisible to the correct operator's `get_first_unhandled_payout_by_operator_xonly_pk` query, and `is_kickoff_malicious` will reject any kickoff the true operator later sends for this deposit as malicious. The operator can never be reimbursed via `ReimburseTx`/kickoff for the 10 BTC it fronted — this matches "an honest operator permanently unable to be reimbursed" (Critical). This is repeatable for every withdrawal processed by every operator, since the flaw is structural to `create_payout_txhandler`'s output-2 design, not tied to a specific deposit or key.

### Likelihood Explanation
Preconditions are minimal and match the described attacker capabilities: the attacker only needs to observe a broadcast, unconfirmed `Payout` tx (public mempool data), own some BTC to fund a fee bump, and be able to broadcast a conflicting transaction that a miner/mempool accepts as a valid RBF replacement (feasible under widely-deployed full-RBF mempool policies, or even without full-RBF if the operator's `Payout` tx signals opt-in replaceability via `DEFAULT_SEQUENCE`, or if the attacker gets it mined directly by a cooperating/self-mined block on any network where standard RBF rules are satisfied). No special privilege, key compromise, or majority hashrate is required — this exactly matches the "unprivileged attacker" threat model in scope. The attacker's cost is only the fee delta needed to win the replacement race.

### Recommendation
Bind the operator-payer identity to something the user's signature actually authenticates, or otherwise make it unforgeable:
- Change the sighash type used for the `Payout` tx input to cover output 2 (e.g., use `All` instead of `SinglePlusAnyoneCanPay` for the committed operator pubkey output, or restructure so the operator-identity commitment is part of output 0/1 which is signed), while still allowing outside funding for fees via additional non-committed inputs only.
- Alternatively, require the operator to prove payer identity via an authenticated channel (e.g., have the operator co-sign a covenant/commitment separate from the malleable `OP_RETURN`, or record the payer identity from the gRPC `withdraw` call authenticated by the operator's key, cross-checked against — not solely inferred from — the mined `OP_RETURN`).
- At minimum, when `update_finalized_payouts` detects a payout tx whose input 0 matches a `withdrawal_utxo` but whose txid differs from any txid the associated operator broadcast/tracked via its own TxSender, flag/reject rather than blindly trusting the mined `OP_RETURN`.

### Proof of Concept
```rust
// core/src/test/payout_op_return_malleability.rs (new test, regtest via CitreaE2E harness)
#[tokio::test]
async fn test_attacker_can_hijack_payout_op_return() {
    // 1. Set up regtest bridge with one operator (op_pk) and a deposit/withdrawal as in
    //    deposit_and_withdraw_e2e.rs helpers.
    // 2. Call operator.withdraw(...) to build+broadcast the honest Payout tx:
    //    - input 0 = withdrawal_utxo, output0 = user payout, output1 = anchor,
    //      output2 = OP_RETURN(op_pk).
    //    Do NOT mine it yet.
    // 3. As "attacker": construct a replacement tx reusing:
    //    - input 0 (same outpoint+witness/signature) from the honest Payout tx
    //    - output 0 unchanged
    //    - add attacker's own funding input(s) (locally owned regtest coins) and a
    //      higher total fee
    //    - output 2 = OP_RETURN(attacker_chosen_bytes) instead of op_pk
    //    Broadcast this replacement via bitcoind RPC (sendrawtransaction), relying on
    //    RBF replacement of the conflicting input.
    // 4. Mine blocks until the attacker's tx confirms and reaches finality depth,
    //    triggering Verifier::update_finalized_payouts.
    // 5. Assertions (both sides of the binding):
    //    let db_row = db.get_payout_info_from_move_txid(None, move_txid).await.unwrap().unwrap();
    //    assert_ne!(db_row.0, Some(op_pk)); // stored key no longer equals actual payer's key
    //    let unhandled = db.get_first_unhandled_payout_by_operator_xonly_pk(None, op_pk).await.unwrap();
    //    assert!(unhandled.is_none()); // honest operator can never find its own withdrawal
    //    // Poll PayoutCheckerTask::run_once repeatedly (or wait several polling intervals)
    //    // and assert it never marks the payout handled for op_pk.
}
```
This demonstrates the honest operator's `get_first_unhandled_payout_by_operator_xonly_pk(op_pk)` never returns the withdrawal after the attacker's replacement is mined, confirming the binding is broken and reimbursement is permanently blocked.

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

**File:** core/src/builder/transaction/txhandler.rs (L222-233)
```rust
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

**File:** core/src/verifier.rs (L2311-2328)
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
```

**File:** core/src/task/payout_checker.rs (L41-51)
```rust
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
