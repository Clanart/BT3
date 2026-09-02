### Title
Withdrawer can self-spend their own withdrawal UTXO with an arbitrary OP_RETURN, causing an honest operator to be falsely credited and reimbursed for a payout it never funded - (File: core/src/database/verifier.rs)

### Summary
`Database::get_payout_txs_for_withdrawal_utxos` (core/src/database/verifier.rs:170-196) matches "the payout tx" for a withdrawal purely by which transaction spends the exact `withdrawal_utxo_txid`/`withdrawal_utxo_vout`, with no check on who signed it or what it actually pays. Since that outpoint is a UTXO whose spending key the withdrawer (attacker) controls, the attacker can spend it themselves in a self-authored transaction that embeds any operator's xonly pubkey in an OP_RETURN, causing that operator's automation to treat the withdrawal as fronted by them and claim a real reimbursement it never funded.

### Finding Description
The intended binding is: `payout_payer_operator_xonly_pk(withdrawal_idx) == xonly_pk of the operator that actually broadcast a valid payout transaction paying the Citrea-registered withdrawal recipient`.

The code path that establishes this binding never checks the second half of the equality:

- `Database::get_payout_txs_for_withdrawal_utxos` (core/src/database/verifier.rs:170-196) joins `withdrawals` to `bitcoin_syncer_spent_utxos` purely on `txid`/`vout` equality — it returns whichever transaction spends the withdrawal outpoint, with no signature/authorization check.
- `Verifier::update_finalized_payouts` (core/src/verifier.rs:2283-2353) takes that transaction, finds the first OP_RETURN output, and parses whatever 32 bytes are there as `operator_xonly_pk` (core/src/verifier.rs:2319-2321), storing it via `update_payout_txs_and_payer_operator_xonly_pk`. There is no check that the output amounts/recipient match the Citrea-registered withdrawal, nor that the spending signature belongs to an operator.
- `PayoutCheckerTask::run_once` (core/src/task/payout_checker.rs:31-111) picks up "unhandled payouts" filtered only by `operator_xonly_pk == self.operator.signer.xonly_public_key` (`get_first_unhandled_payout_by_operator_xonly_pk`) and unconditionally calls `Operator::handle_finalized_payout` (core/src/operator.rs:839+), which allocates a kickoff connector and drives the full reimbursement flow — with no verification that the operator itself ever broadcast/signed that spending transaction.
- `Verifier::is_kickoff_malicious` (core/src/verifier.rs:1859-1915) only checks that `operator_xonly_pk` from the DB matches `kickoff_data.operator_xonly_pk` and that the committed payout blockhash matches — both of which trivially hold here since the attacker chose the victim operator's pubkey and the real blockhash is what it is. It never validates that the operator actually funded the payout.
- The zk `bridge_circuit` (circuits-lib/src/bridge_circuit/mod.rs:182-219) similarly only checks that the payout tx's input at `payout_input_index` matches the withdrawal outpoint from the Citrea storage proof, and extracts the OP_RETURN xonly pubkey into `deposit_constant` — it never verifies who signed the payout tx or that its outputs pay the registered withdrawal recipient/amount.

Because the withdrawal UTXO is a UTXO whose private key is held by the withdrawer (per the withdraw flow), the attacker can, before any operator funds a real payout, broadcast their own transaction spending that UTXO to any destination while embedding a target honest operator's xonly pubkey in an OP_RETURN. None of the checking layers (DB join, `update_finalized_payouts`, `is_kickoff_malicious`, or the bridge circuit) distinguish this from a genuine operator-authored payout.

### Impact Explanation
The targeted honest operator's own automation (`PayoutCheckerTask`) will treat the withdrawal as fronted by itself and proceed through kickoff → challenge-timeout → reimburse, ultimately receiving a real BTC reimbursement from a move-to-vault UTXO for a withdrawal it never actually paid out to the real Citrea-registered recipient. Verifiers will not flag the kickoff as malicious because `is_kickoff_malicious`'s only checks (operator pubkey match, blockhash commitment match) are satisfied by construction. This matches the Critical category "BTC leaving a move-to-vault UTXO without a matching fronted withdrawal" / "an operator reimbursed for a payout it never funded." The attack is repeatable for every withdrawal the attacker (as withdrawer) registers, and can target any operator whose xonly pubkey is public, so the blast radius spans all deposits/withdrawals and all operators.

### Likelihood Explanation
Preconditions are all attacker-controlled and match the given threat model: the attacker only needs to (1) call Citrea `withdraw()` specifying a withdrawal UTXO outpoint whose spending key they hold, and (2) win the race to spend that UTXO with their own Schnorr signature before any operator funds a real payout, embedding an arbitrary bytes/pubkey OP_RETURN. This requires only normal Bitcoin fees and no operator/verifier privileges, collateral, or key compromise. Because the attacker fully controls timing (they can spend the UTXO immediately after registering the withdrawal), likelihood is high and the attack is cheap and repeatable.

### Recommendation
Bind the recorded "payout" strictly to a transaction structurally and cryptographically produced under the expected payout template: verify that the spending transaction's outputs match the exact amount/script_pubkey registered for that withdrawal on Citrea (not just that some transaction spent the outpoint), and/or require the OP_RETURN-embedded operator pubkey to be independently corroborated by an operator-side commitment (e.g., have the operator itself submit/sign a claim referencing the txid, validated against `in_signature`/expected output template) before `update_finalized_payouts` and `PayoutCheckerTask` treat it as a legitimate fronted payout. The bridge circuit should likewise validate that the payout transaction's non-OP_RETURN outputs satisfy the withdrawal amount/recipient constraints from the storage proof, not merely that an input matches the withdrawal outpoint.

### Proof of Concept
```
#[tokio::test]
async fn attacker_self_spend_credits_arbitrary_operator() {
    // 1. Set up DB, register a withdrawal (idx) with withdrawal_utxo = outpoint controlled by "attacker" key.
    // 2. Simulate attacker broadcasting tx `attacker_tx` spending withdrawal_utxo with:
    //      output[0]: to attacker's own address (not fronting anything)
    //      output[1]: OP_RETURN containing victim_operator_xonly_pk (an honest operator's real pubkey)
    // 3. Insert attacker_tx into bitcoin_syncer (insert_txid_to_block + insert_spent_utxo) as if mined.
    // 4. Call db.get_payout_txs_for_withdrawal_utxos(block_id) and assert it returns (idx, attacker_tx.txid())
    //    -- demonstrating the DB attributes ANY spending tx as "the payout".
    // 5. Call update_payout_txs_and_payer_operator_xonly_pk with operator_xonly_pk parsed from
    //    attacker_tx's OP_RETURN (victim_operator_xonly_pk), simulating update_finalized_payouts.
    // 6. Call db.get_first_unhandled_payout_by_operator_xonly_pk(victim_operator_xonly_pk) and assert
    //    it returns this withdrawal/move_txid, proving the victim operator's PayoutCheckerTask would
    //    pick it up as its own fronted payout despite never having broadcast attacker_tx.
    //
    // Binding check: assert operator_xonly_pk_in_db == victim_operator_xonly_pk
    //                but victim_operator never signed/broadcast attacker_tx (no operator secret key used)
    //                => equality holds in DB despite the real-world funding equality being false.
}
```
No mainnet or live Citrea required; this can be validated purely with local `bitcoin_syncer`/`withdrawals` table fixtures as already exercised by `update_get_payout_txs_from_citrea_withdrawal` in core/src/database/verifier.rs:390-521, extended to show the payer pubkey is attacker-chosen rather than operator-authenticated.