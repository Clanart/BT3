### Title
Payout tx OP_RETURN operator-attribution output is unauthenticated by the SIGHASH_SINGLE|ANYONECANPAY signature, letting anyone null it and permanently deny reimbursement - (File: core/src/builder/transaction/operator_reimburse.rs, core/src/rpc/parser/operator.rs, core/src/verifier.rs)

### Summary
`parse_withdrawal_sig_params` only accepts and enforces `TapSighashType::SinglePlusAnyoneCanPay` on the withdrawal input signature [1](#0-0) , which per BIP341 only commits to input 0 and the output at the same index (output 0). The operator-attribution `OP_RETURN` (output 2) built in `create_payout_txhandler` is not covered by that signature [2](#0-1) , so anyone in possession of the signed input/output-0 pair can rebuild a still-valid Payout transaction with a corrupted (e.g. empty) OP_RETURN push, causing `update_finalized_payouts` to store `payout_payer_operator_xonly_pk = NULL` and permanently blocking reimbursement for that withdrawal.

### Finding Description
The claimed binding is: `withdrawals.payout_payer_operator_xonly_pk` (the operator credited for funding output 0 of the mined payout tx) SHOULD equal `operator_xonly_pk` of whichever operator's key-spend input UTXO actually settled the withdrawal.

Trace:
1. `create_payout_txhandler` builds the Payout tx with input 0 (`SpendPath::KeySpend`, `input_utxo`), output 0 (`output_txout`, the user's payout), output 1 (anchor), output 2 (`op_return_txout(operator_xonly_pk.serialize())`) [3](#0-2) .
2. `parse_withdrawal_sig_params` requires (and silently upgrades default signatures to) `SinglePlusAnyoneCanPay` [4](#0-3) . This sighash type, by Taproot/BIP341 semantics, signs only input 0 and output 0 — outputs 1 and 2 (anchor, OP_RETURN) are entirely outside the signed message.
3. Because of this, any party who obtains the signature/outpoint/output-0 tuple (e.g. observed once broadcast to the mempool, or via aggregator gRPC flows that hand out withdrawal signatures) can independently reconstruct a byte-identical-at-input0/output0 transaction, but replace output 2's `OP_RETURN` push with an empty push, and the signature remains valid since it never covered that output.
4. `update_finalized_payouts` extracts the operator key strictly from the mined payout tx's OP_RETURN via `parse_op_return_data` + `XOnlyPublicKey::from_slice` [5](#0-4) . An empty push makes `XOnlyPublicKey::from_slice` fail, so `operator_xonly_pk` becomes `None`, which is persisted as NULL.
5. `get_first_unhandled_payout_by_operator_xonly_pk` filters strictly on `payout_payer_operator_xonly_pk = $1` [6](#0-5) , so a NULL row is never returned to any operator, for any pubkey.
6. `is_kickoff_malicious` treats a missing operator_xonly_pk as "assuming malicious" and rejects the kickoff [7](#0-6) , so no operator can ever be reimbursed for that withdrawal once this row is NULL.

Because Bitcoin consensus only allows one transaction to spend the shared withdrawal outpoint, the mutated (empty-OP_RETURN) transaction and the honest operator's correctly-attributed transaction are mutually exclusive — whichever gets mined wins. No existing guard (`SECP.verify_schnorr`, sighash-type enforcement, `is_kickoff_malicious`) checks that the *mined* transaction's non-signed outputs match what the fronting operator intended; the enforcement in `parse_withdrawal_sig_params` only guarantees sighash *type*, not sighash *coverage* of the attribution output.

### Impact Explanation
If the attacker's mutated transaction (or an operator's own malformed one, or a race-winning attacker's copy) is the one confirmed on-chain, the withdrawal's `payout_payer_operator_xonly_pk` becomes permanently NULL in the database. No operator — not just the one who actually fronted the user's funds — can subsequently be matched by `get_first_unhandled_payout_by_operator_xonly_pk`, and any attempted kickoff by the true funder is rejected as malicious by `is_kickoff_malicious`. This matches the Critical category "an honest operator permanently unable to be reimbursed." The blast radius is per-withdrawal but repeatable across every withdrawal and every operator, since the flaw is structural (SIGHASH_SINGLE|ANYONECANPAY never covering the OP_RETURN output) rather than tied to one deposit.

### Likelihood Explanation
The attacker needs no privileged role: only the ability to observe or obtain a signed withdrawal input/output-0 pair (e.g., from the Bitcoin mempool once any operator broadcasts it, or via any public channel that surfaces the signature) and to broadcast a competing transaction with a higher fee to win the confirmation race. No key compromise, majority hashrate, or verifier/operator privileges are required — only standard fee-bumping/broadcast capability, which is explicitly within the attacker's granted capabilities ("broadcast Bitcoin transactions and pay fees ... choose the bytes of ... an OP_RETURN"). The attack is repeatable for every withdrawal that reaches the payout stage.

### Recommendation
Bind the operator-attribution output to the signature coverage, e.g. by moving the operator xonly pubkey commitment into the signed message (output 0, via `SIGHASH_ALL` or by embedding the pubkey commitment inside the input's script-path leaf that is itself covered by the signature), or by requiring `SIGHASH_ALL`/`SIGHASH_ALL|ANYONECANPAY` semantics that cover the OP_RETURN output, or by having verifiers independently record which operator broadcast which payout via an authenticated channel (e.g., the aggregator's kickoff registration) rather than trusting an unauthenticated OP_RETURN byte string.

### Proof of Concept
```rust
// core/src/test/deposit_and_withdraw_e2e.rs (new test, illustrative)
// 1. Run through deposit + withdrawal flow to obtain a valid
//    (input_signature, input_outpoint, output_script_pubkey, output_amount)
//    tuple via parse_withdrawal_sig_params, as the honest operator would.
// 2. Build txA = create_payout_txhandler(..., operator_xonly_pk = honest_op_pk, user_sig)
//    Build txB = same input_utxo/output_txout/user_sig, but manually replace
//    txA's OP_RETURN output (index 2) script with `OP_RETURN` + empty push (0-byte PushBytesBuf).
// 3. Assert txB's witness (copied from txA, same sighash SinglePlusAnyoneCanPay)
//    still passes bitcoinconsensus/SECP.verify_schnorr for input 0 (i.e., txB is a valid, minable transaction).
// 4. Broadcast/mine txB instead of txA (simulate attacker winning the race).
// 5. Run PayoutCheckerTask::run_once / update_finalized_payouts against the mined block.
// 6. Assert: db.get_payout_info_from_move_txid(...) returns operator_xonly_pk == None (NULL).
// 7. Assert: db.get_first_unhandled_payout_by_operator_xonly_pk(honest_op_pk) never returns
//    this withdrawal's idx across repeated ticks.
```

### Citations

**File:** core/src/rpc/parser/operator.rs (L170-187)
```rust
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

**File:** core/src/builder/transaction/operator_reimburse.rs (L418-435)
```rust
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

**File:** core/src/verifier.rs (L1882-1885)
```rust
        let Some(operator_xonly_pk) = operator_xonly_pk_opt else {
            tracing::warn!("No operator xonly pk found in payout tx OP_RETURN, assuming malicious");
            return Ok(true);
        };
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
