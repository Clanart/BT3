## Title
Front-runnable operator attribution in `payout_tx` via `SIGHASH_SINGLE|ANYONECANPAY` malleability — misattributed reimbursement credit - (File: `core/src/builder/transaction/operator_reimburse.rs`)

## Summary
This is confirmed to be a valid analog of the Covalent front-running bug class: a value-moving action (here, a Bitcoin `payout_tx`) is authorized by a signature that does **not** bind the field the protocol later uses to determine "who gets credited/reimbursed" (the OP_RETURN `operator_xonly_pk`), analogous to Covalent's `transferUnstakedOut()`/`recoverUnstaking()` not checking the `frozen` state that everything else checks.

## Finding Description
The user-signed `payout_tx` is built with `SIGHASH_SINGLE|ANYONECANPAY`, enforced in `parse_withdrawal_sig_params`: [1](#0-0) 

`create_payout_txhandler` then constructs the tx with a single key-spend input using only `user_sig`, and appends an **unsigned** OP_RETURN output containing whichever `operator_xonly_pk` the constructing party chooses: [2](#0-1) 

Because `SIGHASH_SINGLE` only commits the signature to the output at the same index as the input (index 0, the user's payout output) and `ANYONECANPAY` only commits to this one input, **outputs 1 (anchor) and 2 (OP_RETURN operator pubkey) are not covered by `user_sig` at all**. Any party in possession of `user_sig` (which is distributed to all operators via the aggregator's fan-out `withdraw` call, per `aggregator.rs`) can therefore construct an alternate, equally-valid `payout_tx'` that:
- keeps output 0 (the required user payout) unchanged, satisfying the withdrawal correctness checks, but
- substitutes an arbitrary `operator_xonly_pk` in the OP_RETURN output, and
- can bump the fee to win a mempool/RBF race against the legitimately-constructing operator's own broadcast.

Downstream, `update_finalized_payouts` and `is_kickoff_malicious` trust the OP_RETURN pubkey unconditionally as "the operator who fronted this payout": [3](#0-2) [4](#0-3) 

This is the exact binding break called out in the rules: *"the operator credited versus the party that paid"*.

## Impact Explanation
Because the confirmed payout tx's OP_RETURN is unauthenticated data, an unprivileged party who observes/receives `user_sig` (e.g. because the aggregator broadcasts the same withdrawal signature to every registered operator simultaneously — this is not secret to a single legitimate fronting operator) can front-run the real fronting operator's broadcast with a fee-bumped alternative tx that attributes the payout to an arbitrary `operator_xonly_pk`. Two concrete outcomes map to explicit Critical impacts in the rubric:
- If the attacker attributes the payout to a real, honest operator that did **not** actually construct/fund that specific broadcast, that operator's automation (`PayoutCheckerTask` → `handle_finalized_payout`) will proceed through the kickoff/reimbursement flow and be reimbursed for a payout it never itself funded ("an operator reimbursed for a payout it never funded").
- Conversely, if the legitimate operator's own kickoff is later checked against this now-attacker-controlled attribution and it mismatches, `is_kickoff_malicious` flags the kickoff as malicious, leading to the honest operator's collateral being burned ("an honest operator's collateral burned").

## Likelihood Explanation
The only prerequisite is possession of a valid, unmodified `user_sig` for a pending withdrawal — which is routinely distributed to *all* registered operators by the aggregator's `withdraw` RPC fan-out (`core/src/rpc/aggregator.rs`), and is also visible in the mempool once any operator broadcasts its own `payout_tx`. No verifier/operator/watchtower/aggregator role, key compromise, or majority hashrate is required — only standard Bitcoin fee-bumping/replacement of a transaction whose signature does not cover the contested output. This directly parallels the Covalent front-running scenario: a state/attribution-critical field is left outside the authorization boundary that governs the rest of the action.

## Recommendation
Bind the OP_RETURN `operator_xonly_pk` output to the signature that authorizes spending the withdrawal UTXO — e.g., require `SIGHASH_ALL` (or a scheme that commits to all outputs) for the payout transaction's key-spend, or otherwise have the user's off-chain signature (or a companion commitment) explicitly commit to the specific operator's pubkey for this payout, so that no party besides the operator who legitimately produced the broadcast tx can alter attribution while it is in-flight.

## Proof of Concept
1. Aggregator receives a withdrawal request and fans it out to all operators via `withdraw` (`core/src/rpc/aggregator.rs:1811-1917`), each receiving the same `input_signature` (`SinglePlusAnyoneCanPay`).
2. Legitimate OperatorA calls `Operator::withdraw` (`core/src/operator.rs:560-627`), which builds `payout_tx_A` via `create_payout_txhandler` with OP_RETURN = OperatorA's `xonly_pk`, and broadcasts it (fee F).
3. An unprivileged party (any node with mempool visibility, or one of the other fanned-out recipients) constructs `payout_tx_B` reusing the same `user_sig`/input, identical output[0] (payout to user is unchanged so validity checks still pass), but with a different OP_RETURN pubkey (e.g. `OperatorB`'s, or a completely uninvolved key) and a higher fee F' > F.
4. `payout_tx_B` replaces/out-competes `payout_tx_A` in the mempool and confirms.
5. `update_finalized_payouts` (`core/src/verifier.rs:2312-2335`) records the payer as the pubkey embedded in `payout_tx_B`'s OP_RETURN, not OperatorA — misattributing credit/reimbursement rights despite OperatorA (or nobody legitimate) having actually funded the confirmed transaction.

### Citations

**File:** core/src/rpc/parser/operator.rs (L181-187)
```rust
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
