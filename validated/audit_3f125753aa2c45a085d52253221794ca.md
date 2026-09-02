### Title
Unauthenticated OP_RETURN operator-attribution in payout tx (SIGHASH_SINGLE|ANYONECANPAY malleability) breaks reimbursement attribution - (`core/src/verifier.rs`)

### Summary
The payout transaction that credits an operator for fronting a withdrawal is only partially signed by the user: the withdrawal authorization uses `TapSighashType::SinglePlusAnyoneCanPay`, which commits solely to input 0 and output 0 (the user's payout). The anchor output and the OP_RETURN output carrying the crediting operator's x-only pubkey are completely unsigned, so anyone who observes a broadcast payout transaction can rebuild an alternative transaction with the same signed input/output but an arbitrary OP_RETURN naming any operator, and get it mined instead.

### Finding Description
The binding claimed to hold is: `withdrawals.payout_payer_operator_xonly_pk == the xonly_pk of the operator whose funds actually left the user's payout output`.

`create_payout_txhandler` (`core/src/builder/transaction/operator_reimburse.rs:407-436`) builds the payout tx with three outputs — user payout (0), anchor (1), OP_RETURN containing `operator_xonly_pk` (2) — and signs input 0 with a user-provided `taproot::Signature` whose `sighash_type` is verified in `Operator::withdraw` (`core/src/operator.rs:630-637`) via `SECP.verify_schnorr` against `payout_txhandler.calculate_sighash_txin(0, in_signature.sighash_type)`. The comment/error explicitly documents `SinglePlusAnyoneCanPay` as the expected type, as also shown in the test helper `sign_withdrawal_output` (`core/src/test/common/setup_utils.rs:499-542`), which signs with `sighash::TapSighashType::SinglePlusAnyoneCanPay`.

Per BIP143/Taproot sighash rules, `SIGHASH_SINGLE | ANYONECANPAY` commits **only** to the single input being spent and the output at the same index (index 0). It places **no constraint whatsoever** on any other output — including output 2, the OP_RETURN carrying the crediting operator's pubkey — nor on any other input added to the transaction. Consequently, the user's signature authorizes spending the specific withdrawal UTXO into the specific user-payout output, but says nothing about which operator gets credited.

Later, `Verifier::update_finalized_payouts` (`core/src/verifier.rs:2283-2353`) simply reads whichever payout transaction actually got confirmed for the withdrawal UTXO, extracts the first OP_RETURN via `get_first_op_return_output` + `parse_op_return_data` (`circuits-lib/src/bridge_circuit/mod.rs:608-617`), and parses the pushed bytes with `XOnlyPublicKey::from_slice` (`core/src/verifier.rs:2319-2321`) — a pure format check, not an ownership/authorization check — then stores it via `update_payout_txs_and_payer_operator_xonly_pk` (`core/src/database/verifier.rs:199-250`) with no cross-check against who actually funded the transaction's inputs.

**Attack**: An unprivileged attacker observes a legitimate, unconfirmed payout transaction broadcast by honest operator A (mempool data is public). The attacker extracts the user's `SinglePlusAnyoneCanPay` signature and the exact input/output-0 pair from that transaction, then constructs a new transaction: same input (the withdrawal UTXO), same witness (re-using the extracted signature, which remains valid because none of the new transaction's structure outside input 0/output 0 is covered by it), identical output 0 (required to keep the signature valid), but a *different* OP_RETURN output pushing any attacker-chosen 32 bytes that happen to parse as a valid x-only pubkey (e.g. operator B's real pubkey, taken from B's public round transactions — the attacker never needs B's private key). The attacker fee-bumps this transaction (fund_raw_transaction/RBF-eligible sequence, or simply races first-broadcast) so it confirms instead of, or in place of, A's original.

Once mined, `update_finalized_payouts` records `payout_payer_operator_xonly_pk = B`, even though B never funded anything and A actually paid. No downstream guard catches this:
- `Verifier::is_kickoff_malicious` (`core/src/verifier.rs:1857-1915`) only checks that the recorded `operator_xonly_pk` from the (already-corrupted) DB record equals `kickoff_data.operator_xonly_pk` — it has no way to know the DB record itself was forged.
- `PayoutCheckerTask` (`core/src/task/payout_checker.rs`) for operator B queries `get_first_unhandled_payout_by_operator_xonly_pk` filtered on B's own key (`core/src/database/verifier.rs:282-313`) — this will now surface the forged payout as "belonging to B" and B's own automation will call `handle_finalized_payout`/kick off for a deposit B never funded.
- Meanwhile A's later legitimate kickoff attempt fails the `operator_xonly_pk != kickoff_data.operator_xonly_pk` check in `is_kickoff_malicious`, gets flagged malicious, and a challenge is queued against A (`core/src/verifier.rs:1966-2024`), even though A is the party who genuinely fronted the funds.

### Impact Explanation
This breaks the ATTRIBUTION invariant with two concrete Critical outcomes, both matching the listed severity categories:
- Operator B is credited/reimbursed for a payout it never funded (if B's automation kicks off on the forged record).
- Honest operator A, who genuinely fronted the withdrawal, is rendered permanently unable to be reimbursed for that payout, and/or has its kickoff flagged malicious and challenged, risking loss of collateral.

This is repeatable per withdrawal: any observed unconfirmed payout transaction from any operator, for any deposit, can be hijacked this way, so the blast radius spans every withdrawal serviced by the protocol and every pair of operators.

### Likelihood Explanation
Preconditions are minimal and fully within the declared "unprivileged attacker" capability set: monitor the Bitcoin mempool (public), extract a valid `SinglePlusAnyoneCanPay` signature and the withdrawal outpoint from any operator's broadcast payout transaction, construct a competing transaction with an arbitrary OP_RETURN, and pay a higher fee to win the race (RBF or plain fee competition — `Operator::withdraw` itself funds its transaction "using RBF", confirming these transactions are relay-replaceable). No key compromise, no verifier/operator privilege, and no majority hashrate is required — only fee-rate competition, which is well within reach of any Bitcoin user. This is highly feasible and cheap for the attacker (cost = one fee bump), and repeatable across every withdrawal cycle.

### Recommendation
Bind the OP_RETURN operator-attribution output to the same signature that authorizes the spend. Concretely, do not use `SIGHASH_SINGLE | ANYONECANPAY` for the withdrawal-authorizing signature (or, if it must remain ANYONECANPAY for fee-bumping flexibility, do not use `SINGLE`; switch to `SIGHASH_ALL | ANYONECANPAY` so all outputs, including the OP_RETURN, are committed to and cannot be altered without invalidating the signature — while still letting the operator add extra fee-paying inputs at the ANYONECANPAY-permitted positions). Alternatively, require a second signature/commitment from the crediting operator (or the aggregator/N-of-N) over the OP_RETURN payload before `update_finalized_payouts` accepts it, and reject payout transactions whose OP_RETURN operator key cannot be tied to whichever party actually supplied additional funding inputs.

### Proof of Concept
```rust
// core/src/test/deposit_and_withdraw_e2e.rs (new test, regtest, MockCitreaClient)
// 1. Run a single deposit, register a withdrawal utxo via generate_withdrawal_transaction_and_signature
//    (uses SinglePlusAnyoneCanPay sighash) and insert_withdrawal_utxo on MockCitreaClient.
// 2. Call operator A's withdraw() gRPC to produce and broadcast the legitimate payout_tx_A
//    (OP_RETURN = move_txid || A.xonly_pk). Capture it from the mempool via rpc.get_raw_transaction.
// 3. Extract the taproot key-path witness (schnorr sig, SinglePlusAnyoneCanPay) from payout_tx_A's
//    input 0 witness.
// 4. Build payout_tx_forged: same input (withdrawal_utxo), identical output[0] (user payout,
//    same script_pubkey/value as payout_tx_A.output[0]), witness = extracted signature,
//    but output[2] OP_RETURN = move_txid || B.xonly_pk (operator B who never funded anything),
//    with a higher fee than payout_tx_A.
// 5. rpc.send_raw_transaction(payout_tx_forged) with fee > payout_tx_A, mine it so it confirms
//    instead of payout_tx_A (or replaces it via RBF if signaled).
// 6. Sync verifiers/operators so update_finalized_payouts processes the block.
// 7. assert_eq!(
//        db.get_payout_info_from_move_txid(None, move_txid).await.unwrap().unwrap().0,
//        Some(B.xonly_pk)   // WRONG: should be None/A, since A funded output[0], not B
//    );
//    assert_ne!(payer_recorded, actual_funder_A);  // demonstrates broken ATTRIBUTION
```
This demonstrates that `get_payout_info_from_move_txid` attributes the payout to operator B, who supplied none of the funds, confirming the attribution binding is broken and reproducible with only public mempool visibility and fee-bumping — no privileged role required.