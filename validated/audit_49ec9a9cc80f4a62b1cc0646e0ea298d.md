### Title
Payout OP_RETURN `operator_xonlypk` is unauthenticated and malleable under `SinglePlusAnyoneCanPay`, allowing reimbursement misattribution - (File: `bridge-circuit-host/src/structs.rs`, `circuits-lib/src/bridge_circuit/mod.rs`, `core/src/builder/transaction/operator_reimburse.rs`)

### Summary
`host_deposit_constant` (and the in-circuit `bridge_circuit`) derive `operator_xonlypk` purely from the OP_RETURN push-data of the confirmed payout transaction, with no cryptographic binding to the entity that actually funded/broadcast that transaction. Because the only signature present on the payout tx (`user_sig`, sighash `SinglePlusAnyoneCanPay`) only commits to input 0 and output 0 (the withdrawer's payout output), the OP_RETURN output (index 2, containing `operator_xonlypk`) is completely unsigned and can be freely substituted by anyone who reconstructs and (re)broadcasts a competing transaction spending the same withdrawal UTXO.

### Finding Description
The claimed binding is:
`operator_xonlypk (from OP_RETURN in confirmed payout tx) == operator who actually supplied/funded the payout output`

This binding is never checked anywhere in the codebase.

- `create_payout_txhandler` (`core/src/builder/transaction/operator_reimburse.rs:407-436`) builds the payout tx with output 0 = user payout, output 1 = anchor, output 2 = OP_RETURN(`operator_xonly_pk`), and signs only input 0 with `user_sig` via `set_p2tr_key_spend_witness`.
- The user's signature is enforced to use `TapSighashType::SinglePlusAnyoneCanPay` in `parse_withdrawal_sig_params` (`core/src/rpc/parser/operator.rs:174-187`). Under BIP341, `SIGHASH_SINGLE` commits only to the output at the *same index* as the signed input (index 0), and `ANYONECANPAY` commits only to that single input — no other inputs or outputs (including the OP_RETURN at index 2) are covered by this signature.
- Consequently, anyone who observes the broadcast (mempool) transaction/signature can construct an alternate, fully valid transaction reusing the exact same signed input (same previous_output, same signature) and the same signed output 0, while freely choosing a different OP_RETURN pubkey at output 2 and supplying their own fee-paying inputs. If this variant confirms instead of (or in place of) the original, it is a valid alternate transaction on the same withdrawal UTXO.
- Downstream, `Verifier::update_finalized_payouts` (`core/src/verifier.rs:2283-2353`) blindly extracts `operator_xonly_pk` from whichever payout tx actually confirms via `get_first_op_return_output`/`parse_op_return_data`, with no signature check, and stores it as `payout_payer_operator_xonly_pk`.
- `PayoutCheckerTask::run_once` (`core/src/task/payout_checker.rs:39-111`) automatically triggers `handle_finalized_payout`/kickoff/reimbursement for any operator whose xonly_pk shows up in that DB column via `get_first_unhandled_payout_by_operator_xonly_pk`, with no verification that this operator instance was the one that broadcast/funded the payout.
- Finally, `host_deposit_constant` (`bridge-circuit-host/src/structs.rs:482-516`) and the in-circuit equivalent (`circuits-lib/src/bridge_circuit/mod.rs:206-229`) recompute `deposit_constant`/`journal_hash` using this same unauthenticated OP_RETURN value, so the zk proof and journal faithfully commit to whatever pubkey ended up on-chain — they don't (and structurally cannot, without an external check) verify that the credited operator actually funded the payout.

None of the existing guards (`SPV::verify`, `verify_storage_proofs`, `lc_proof_verifier`, the input/output-index assertions at `circuits-lib/src/bridge_circuit/mod.rs:190-204`) constrain the OP_RETURN content to the funder's identity — they only bind the withdrawal input's outpoint/vout, which is unaffected by this attack since the attacker must preserve the same signed input/output.

### Impact Explanation
- If the attacker sets OP_RETURN to a victim honest operator's real xonly_pk (while funding the payout output themselves or otherwise having it confirm), the victim's automated `PayoutCheckerTask` will detect the payout as its own and trigger a kickoff to claim reimbursement it never funded — "an operator reimbursed for a payout it never funded" (Critical).
- If the honest operator's own broadcast is front-run/replaced before confirmation and the winning variant carries a garbled/foreign OP_RETURN, the true operator that fronted the withdrawer's payment can never be attributed the payout (`payout_payer_operator_xonly_pk` becomes NULL or someone else's key), permanently losing its ability to be reimbursed via `send_asserts`'s `payout_op_xonly_pk != kickoff_data.operator_xonly_pk` check (`core/src/operator.rs:1290-1295`) — "an honest operator permanently unable to be reimbursed" (Critical).
- This is repeatable across every withdrawal/payout on the bridge, since the flaw is structural (missing sighash coverage), not deposit- or operator-specific.

### Likelihood Explanation
The attacker needs no privileges beyond broadcasting Bitcoin transactions: observe a broadcast (mempool) payout tx (or its signature/outpoint via any off-chain channel), construct a variant transaction reusing the same signed input and output 0, substitute output 2 (OP_RETURN), fund it with their own fee inputs, and get it confirmed ahead of or in place of the original (e.g., via replace-by-fee/pinning racing, since the operator's own payout tx is sent "using RBF" per `core/src/operator.rs`). Cost is limited to Bitcoin transaction fees (and, in the "credit a victim operator" variant, potentially funding the withdrawer's payout amount itself, though even a fee-only race that corrupts/garbles the OP_RETURN is sufficient to permanently deny the honest operator).

### Recommendation
Bind the OP_RETURN `operator_xonlypk` (and any other outputs the operator needs unmalleable) into the sighash actually signed, e.g., have the withdrawer's signature use `SIGHASH_ALL` (or a covenant/commitment scheme covering all outputs) instead of `SinglePlusAnyoneCanPay`, or require the operator to co-sign/commit to the exact payout transaction (including OP_RETURN) before it is considered valid, and have `update_finalized_payouts`/`host_deposit_constant` validate that binding rather than trusting arbitrary on-chain OP_RETURN bytes.

### Proof of Concept
```rust
// bridge-circuit-host or core/src/builder/transaction tests
#[test]
fn test_op_return_operator_pk_not_covered_by_sighash() {
    // 1. Build the withdrawal UTXO + user_sig with SinglePlusAnyoneCanPay via parse_withdrawal_sig_params.
    // 2. Construct payout_tx_A = create_payout_txhandler(utxo, output_txout, operator_A_pk, user_sig, network).
    // 3. Construct payout_tx_B = create_payout_txhandler(utxo, output_txout, operator_B_pk, user_sig, network)
    //    reusing the SAME user_sig and SAME input/output_txout, only operator_xonly_pk differs.
    // 4. Assert both txhandlers produce a VALID witness (txhandler.promote() succeeds) and that
    //    SECP.verify_schnorr succeeds against the same sighash for both (since sighash excludes OP_RETURN):
    //    let sighash_a = payout_tx_A.calculate_sighash_txin(0, SinglePlusAnyoneCanPay)?;
    //    let sighash_b = payout_tx_B.calculate_sighash_txin(0, SinglePlusAnyoneCanPay)?;
    //    assert_eq!(sighash_a, sighash_b); // proves OP_RETURN is not committed
    // 5. Feed both payout_tx_A and payout_tx_B (as payout_spv.transaction) into host_deposit_constant
    //    with identical kickoff_tx/round data, and assert both produce different but "accepted"
    //    DepositConstant values (no error), demonstrating attribution is attacker-controlled.
}
```