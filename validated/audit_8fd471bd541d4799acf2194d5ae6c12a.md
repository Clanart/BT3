### Title
Operator payout OP_RETURN (operator identity) is not covered by the user's payout signature, allowing malleation of the reimbursement recipient - ([File: core/src/builder/transaction/operator_reimburse.rs], [File: core/src/operator.rs])

### Summary
`Operator::withdraw` verifies the user's signature using a `SinglePlusAnyoneCanPay` sighash, which only commits to input0 and the output at the same index (output0, the user payout). It does not commit to output1 (anchor) or output2 (the OP_RETURN carrying `operator_xonly_pk`). An attacker can take the broadcast payout transaction, keep input0/output0 unchanged, and simply replace the OP_RETURN payload (output2) with a different operator's pubkey or garbage, reusing the *same valid* `user_sig` witness, then get this variant mined first.

### Finding Description
The binding claimed by the protocol is: `operator_xonly_pk recorded in DB for withdrawal i == the operator whose signed input funded output0 (the actual payout)`. 

In `core/src/builder/transaction/operator_reimburse.rs::create_payout_txhandler` (lines 407-436), the payout tx is built with output0 = user payout, output1 = anchor, output2 = `op_return_txout(operator_xonly_pk.serialize())`. The single input is signed with `SpendPath::KeySpend` using `user_sig` [1](#0-0) .

In `core/src/operator.rs::withdraw` (lines 630-637), the signature is verified against a sighash computed with `in_signature.sighash_type`, and the error message explicitly instructs that the sighash type must be `SinglePlusAnyoneCanPay`:
```
.wrap_err("Failed to verify signature received from user for payout txin. Ensure the signature uses SinglePlusAnyoneCanPay sighash type.")
``` [2](#0-1) 

`SIGHASH_SINGLE | SIGHASH_ANYONECANPAY` commits only to the single input being signed and the output at the same index as that input (i.e., output0). It does **not** commit to output1 or output2. This means the operator identity embedded in output2's OP_RETURN — the value later trusted to bind the reimbursement to the correct operator — is completely unauthenticated by the user's signature.

Attack flow:
1. Operator A builds and broadcasts the real payout tx: input0 (`user_sig`, `SIGHASH_SINGLE|ANYONECANPAY`), output0 = user payout, output1 = anchor, output2 = OP_RETURN(A's xonly pk).
2. Attacker observes the mempool tx, copies input0 (witness is valid for any tx with the same output0 at index 0), keeps output0 identical, but sets output2 to a different operator B's xonly pk (or garbage bytes), and rebroadcasts with a higher fee to get it confirmed first (RBF/first-seen race).
3. `Verifier::update_finalized_payouts` (`core/src/verifier.rs` lines 2283-2353) scans the confirmed block, calls `get_first_op_return_output`/`parse_op_return_data` on the attacker's tx, and records `operator_xonly_pk = B` (or `None`) for withdrawal `idx` via `update_payout_txs_and_payer_operator_xonly_pk` [3](#0-2) .
4. `PayoutCheckerTask::run_once` calls `get_first_unhandled_payout_by_operator_xonly_pk` for operator A's key and finds nothing, since the DB now associates the withdrawal with B (or nobody) [4](#0-3) .
5. When A later sends its kickoff (having genuinely fronted the withdrawal), `Verifier::is_kickoff_malicious` compares the DB-stored `operator_xonly_pk` to A's kickoff data and finds a mismatch, flagging A's kickoff as malicious [5](#0-4) .

No existing guard checks that output2 matches what was actually signed/committed by the user, because the signature simply doesn't cover it by design (`SinglePlusAnyoneCanPay`). `is_deposit_valid`, `is_profitable`, and `SECP.verify_schnorr` only validate input0/output0, not outputs 1 and 2.

### Impact Explanation
Operator A fronted the withdrawal (its own BTC left A's control funding output0) but the on-chain record used by verifiers to authorize reimbursement no longer points to A. A's subsequent kickoff will be treated as malicious by every verifier's `is_kickoff_malicious` check, triggering Challenge/Disprove and burning A's kickoff/round collateral, while A can never claim a valid `Reimburse` tx for this withdrawal. This matches the Critical impact category "an honest operator permanently unable to be reimbursed" / "an honest operator's collateral burned." The attack is repeatable for every withdrawal that any operator fronts, since it only depends on the OP_RETURN not being covered by the payout signature — a structural, not incidental, gap.

### Likelihood Explanation
The attacker needs no special role: any unprivileged party monitoring the mempool can observe a broadcast payout transaction, construct a variant with a different output2, and pay a higher fee to win the block-inclusion race (this is a standard first-seen/RBF race, not a cryptographic break — no signature forgery is required since the original `user_sig` is directly reusable due to the sighash flags). The cost is bounded by needing to outbid the operator's fee for one transaction, which is a modest incremental amount. This is fully feasible pre-confirmation for every operator payout and is deterministic given the code's use of `SinglePlusAnyoneCanPay`.

### Recommendation
Bind the operator identity into what the user's signature covers, e.g.:
- Use a sighash type that also commits to output2 (e.g. plain `SIGHASH_ALL` or `SIGHASH_ALL|ANYONECANPAY`) so any mutation of the OP_RETURN invalidates `user_sig`; or
- Restructure so the operator identity/OP_RETURN commitment isn't derived from the payout tx itself but from an out-of-band, separately-authenticated channel (e.g., verifiers/DB record the operator that requested/funded the payout via the `withdraw` RPC call metadata and cross-check against the tx that actually spends the registered `withdrawal_utxo`, rather than trusting an unsigned OP_RETURN output for attribution).
- At minimum, verifiers should associate a confirmed payout tx with the operator based on some signed/committed data rather than an OP_RETURN output not covered by the signature.

### Proof of Concept
```
// core/src/builder/transaction/operator_reimburse.rs / core/src/operator.rs regtest test (proof sketch)
#[tokio::test]
async fn test_payout_op_return_malleation() {
    // 1. Set up regtest bridge, register withdrawal at index i with a known withdrawal UTXO.
    // 2. Operator A calls `Operator::withdraw(...)`, producing `payout_tx_A` with:
    //    output0 = user payout, output2 = OP_RETURN(A.xonly_pk).
    //    Do NOT broadcast payout_tx_A through the normal flow yet.
    // 3. Construct `payout_tx_evil` by cloning payout_tx_A's input0 (identical witness/user_sig),
    //    identical output0, but replace output2 with OP_RETURN(B.xonly_pk) (B != A) and bump fee
    //    (e.g. drop the anchor amount / adjust output1).
    // 4. Assert the reused signature still verifies:
    //    SECP.verify_schnorr(&user_sig.signature, &Message::from_digest(sighash_evil), &user_xonly_pk).is_ok()
    //    where sighash_evil is computed over payout_tx_evil with SIGHASH_SINGLE|ANYONECANPAY.
    // 5. Broadcast payout_tx_evil first and mine it.
    // 6. Run Verifier::handle_finalized_block -> update_finalized_payouts on that block.
    // 7. Assert DB: get_payout_info_from_move_txid(...) for withdrawal i returns operator_xonly_pk == B (or None),
    //    NOT A, while output0/value match the UTXO that actually funded the user (i.e. A's funds).
    // 8. Operator A now sends its real kickoff; assert Verifier::is_kickoff_malicious(...) == true for A's kickoff,
    //    demonstrating A is flagged malicious despite having funded the payout.
}
```
This is fully reproducible in a `cargo test` regtest environment with no mainnet or live Citrea dependency, since only local Bitcoin regtest and DB state are needed to demonstrate the mismatch.

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
