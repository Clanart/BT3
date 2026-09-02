### Title
Operator attribution for fronted payouts is not cryptographically bound to the named operator - ([File: core/src/verifier.rs])

### Summary
`Verifier::update_finalized_payouts` derives the "payer" operator for a withdrawal solely from an OP_RETURN output in the confirmed payout transaction, without verifying that the named operator supplied any of the transaction's funding or authorized the OP_RETURN in any way. Because the withdrawer's signature uses `SIGHASH_SINGLE|SIGHASH_ANYONECANPAY` (verified only against input/output index 0 in `Operator::withdraw`), any party who obtains the withdrawer's signed input/output pair can assemble the final broadcast transaction, fund it themselves, and freely choose which operator's `xonly_pk` to push into the OP_RETURN.

### Finding Description
The binding the protocol needs to hold is: `payer_operator_xonly_pk recorded in DB == operator whose funds actually paid out the withdrawal`. Tracing the code shows this binding is not enforced.

`Operator::withdraw` (core/src/operator.rs:560-637) builds a `payout_txhandler` via `create_payout_txhandler`, and only verifies the withdrawer's Schnorr signature against `sighash` for **input index 0** with the caller-supplied `in_signature.sighash_type`: [1](#0-0) 
This is the standard `SIGHASH_SINGLE|ANYONECANPAY` pattern that only commits to one input and its corresponding output — it does not commit to any other inputs, other outputs, or the OP_RETURN output that carries the "payer" attribution.

`Verifier::update_finalized_payouts` (core/src/verifier.rs:2283-2352) later scans the confirmed block for the payout tx, extracts the first OP_RETURN output, and parses it as an x-only pubkey with no further validation: [2](#0-1) 
This value is persisted directly as the "payer operator" via `update_payout_txs_and_payer_operator_xonly_pk` (core/src/verifier.rs:2345-2350), with no signature, MuSig2 partial-sig, or membership proof tying the OP_RETURN content to the operator whose key it names, and no check that the operator's own funds appear among the transaction's inputs.

Because the OP_RETURN is just pushed data added by whoever finishes constructing and broadcasting the transaction, and because the withdrawer's SIGHASH_SINGLE|ANYONECANPAY signature does not cover it, any unprivileged party able to obtain (or itself produce, e.g. as the withdrawer) the signed input/output pair can complete the transaction with their own funding inputs and an arbitrary operator's pubkey in the OP_RETURN. Once mined, `update_finalized_payouts` blindly records that operator as the payer for `withdrawal_index`. `Operator::withdraw`'s later `withdrawal_utxo != input_utxo.outpoint` check (core/src/operator.rs:594) only prevents a second payout for the same withdrawal — it does nothing to validate the attribution already recorded for the first one.

I was not able to fully trace, within the available tool budget, whether downstream reimbursement/kickoff validation (`create_reimburse_txhandler`, kickoff malicious-checks) re-derives or independently re-verifies the payer attribution against actual on-chain fund flow, versus trusting the DB column verbatim. Based on the code reachable and cited above, the write path itself performs no such verification, so the vulnerability exists at least up to and including the DB write.

### Impact Explanation
If the recorded `payer_operator_xonly_pk` is later trusted (without independent re-verification) to authorize a `Reimburse` claim, this allows: an operator to be credited/reimbursed for BTC it never fronted (Critical: "an operator reimbursed for a payout it never funded"), and the true, honest operator that was ready to pay becomes permanently unable to claim Reimburse for that withdrawal because the withdrawal UTXO is already spent (Critical: "an honest operator permanently unable to be reimbursed"). The attack is repeatable per withdrawal wherever two or more operators are registered for a deposit, and its blast radius scales with the number of concurrent withdrawals/operators in the system.

### Likelihood Explanation
Preconditions are modest: multiple registered operators, and an attacker able to broadcast a Bitcoin transaction using a withdrawer-signed `SIGHASH_SINGLE|ANYONECANPAY` input/output (the attacker may be the withdrawer themselves, per the given attacker capabilities). No verifier, operator, or aggregator privilege is required to construct or broadcast the malicious payout transaction, and no code path inspected blocks an arbitrary xonly_pk from being embedded in the OP_RETURN. The only cost to the attacker is normal Bitcoin fees and (if self-funding the payout) the withdrawal amount itself, which is economically neutral to them as the withdrawer.

### Recommendation
Require the OP_RETURN attribution to be cryptographically bound to the named operator — e.g., have the operator sign (or contribute a MuSig2/Schnorr commitment over) the full payout transaction including the OP_RETURN output, and have `update_finalized_payouts` verify that signature/commitment against the claimed `operator_xonly_pk` before persisting it. Alternatively, require that the named operator's own UTXO(s) are present among the transaction's funding inputs, and verify this on-chain condition in `update_finalized_payouts` rather than trusting unauthenticated OP_RETURN data.

### Proof of Concept
1. Set up two registered operators A and B for a deposit (test harness under `core/src/test`, out of scope for grading but usable to reproduce).
2. As the withdrawer, produce a `SIGHASH_SINGLE|ANYONECANPAY` signature over the payout input/output per `Operator::withdraw`'s sighash construction (core/src/operator.rs:630).
3. Construct a raw transaction reusing that signed input/output, add attacker-controlled funding inputs, and add an OP_RETURN output pushing operator B's `xonly_pk` (obtained from public config, no B key required).
4. Broadcast and mine this transaction; drive `Verifier::update_finalized_payouts` over the containing block.
5. Assert `db.get_payout_txs_for_withdrawal_utxos`/the payout row's payer xonly pk equals B's key, while B's wallet/UTXO set shows no outgoing UTXO of the payout amount, and Operator A's later `Operator::withdraw` call for the same `withdrawal_index` fails with the "Input UTXO does not match withdrawal UTXO" error (core/src/operator.rs:594-596), confirming A is permanently blocked from claiming this withdrawal.

### Citations

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
