### Title
Payout OP_RETURN operator attribution is forgeable, letting an uninvolved operator be auto-reimbursed for a payout it never funded - (File: core/src/builder/transaction/operator_reimburse.rs)

### Summary
`create_payout_txhandler` signs the payout transaction with `TapSighashType::SinglePlusAnyoneCanPay`, which only commits to input 0 and output 0 (the user payout). The anchor output and the `OP_RETURN` output carrying `operator_xonly_pk` are completely uncommitted, and `ANYONECANPAY` explicitly allows arbitrary extra inputs. Any party who obtains the withdrawal outpoint, output script/amount, and the user's S+AP signature can rebuild the payout transaction with a forged `OP_RETURN` naming a victim operator, fund it with their own BTC, and broadcast it. `Verifier::update_finalized_payouts` blindly parses this forged `OP_RETURN` and persists it as `payout_payer_operator_xonly_pk`, which the victim operator's own `PayoutCheckerTask` automation later reads to auto-trigger a kickoff and get reimbursed for a payout it never funded.

### Finding Description
The broken binding: `withdrawals.payout_payer_operator_xonly_pk` (credited operator) **==** the party whose Bitcoin funds actually paid output 0 of the confirmed payout transaction.

Trace:
1. `create_payout_txhandler` (`core/src/builder/transaction/operator_reimburse.rs:407-436`) builds `input(0)=withdrawal UTXO`, `output(0)=user payout`, `output(1)=anchor`, `output(2)=op_return_txout(operator_xonly_pk)`, and calls `set_p2tr_key_spend_witness(&user_sig, 0)`.
2. `Operator::withdraw` (`core/src/operator.rs:560-637`) verifies the user's signature only via `calculate_sighash_txin(0, in_signature.sighash_type)` with `SinglePlusAnyoneCanPay` — this sighash type commits to input 0 and output 0 only; per BIP341/BIP143 semantics, `SIGHASH_ANYONECANPAY` permits arbitrary other inputs, and `SIGHASH_SINGLE` leaves all outputs beyond index 0 (the anchor and the `OP_RETURN`) completely unsigned.
3. The withdrawal outpoint, `output_script_pubkey`, `output_amount`, and the S+AP signature are exactly the values the attacker rules grant: an unprivileged party can construct their own copy of this transaction — same input/output 0, same witness — but substitute any `operator_xonly_pk` in the `OP_RETURN` (output 2, uncovered by the signature) and add their own funding input(s) under `ANYONECANPAY` to cover the payout amount and fee, then broadcast it directly to the Bitcoin network (no aggregator/operator RPC call is even required).
4. Once mined, `Verifier::update_finalized_payouts` (`core/src/verifier.rs:2283-2353`) finds the confirmed payout tx, extracts `first_op_return_output`, and does `parse_op_return_data(...).and_then(XOnlyPublicKey::from_slice)` (`core/src/verifier.rs:2312-2342`) with **no check whatsoever that the named pubkey's owner actually contributed funds** to the transaction. The result is persisted via `update_payout_txs_and_payer_operator_xonly_pk` (`core/src/database/verifier.rs:198-251`) into `withdrawals.payout_payer_operator_xonly_pk`.
5. `PayoutCheckerTask::run_once` (`core/src/task/payout_checker.rs:39-111`) polls `db.get_first_unhandled_payout_by_operator_xonly_pk(self.operator.signer.xonly_public_key)` — purely a DB lookup keyed on the (forgeable) column — and, if a row matches the operator's own key, automatically calls `Operator::handle_finalized_payout` → `get_next_txs_to_send`/`get_reimbursement_txs` (`core/src/operator.rs:839+, 1742+, 2098+`), sending Kickoff and eventually Reimburse transactions, with no verification that the operator itself broadcast or funded the underlying payout.
6. `Verifier::is_kickoff_malicious` (`core/src/verifier.rs:1857-1915`) only checks that `operator_xonly_pk` from the (forgeable) db row matches `kickoff_data.operator_xonly_pk`, and that the committed blockhash matches the actual confirmed payout block — both trivially satisfied since they are derived from the same forged-but-now-canonical chain data. It never checks who funded the payout inputs, so this guard does not catch the forgery.

Root cause: attribution of "who fronted a withdrawal" is derived solely from an unauthenticated, unsigned `OP_RETURN` field in a transaction whose other outputs an attacker can freely control while reusing a legitimately obtained signature that never committed to that field.

### Impact Explanation
An uninvolved operator's own automation (`PayoutCheckerTask`) can be triggered to spend a Kickoff + Reimburse flow and receive bridge deposit funds for a withdrawal it never paid for, matching the Critical category "an operator reimbursed for a payout it never funded." The forged credit also consumes that operator's kickoff/round slot and collateral cycle involuntarily. This is repeatable for every withdrawal index and against any registered operator's known `xonly_pk` (operator pubkeys are public protocol parameters), so the blast radius spans all deposits/withdrawals and all operators.

### Likelihood Explanation
The attacker needs only: (a) a withdrawal registered on Citrea with a signature/UTXO of their choosing (explicitly permitted per the threat model — the attacker can create the withdrawal themselves), (b) enough BTC to fund the payout output and fee (the "cost" of the attack — this is the same cost a legitimate operator would pay to front a withdrawal), and (c) the ability to broadcast a standard Bitcoin transaction. No verifier/operator/aggregator credentials, no key compromise, and no majority hashrate are required. The only "cost" is the BTC value of the payout itself, which the attacker recovers no benefit from directly except forcing a mismatched credit — feasible as a griefing/sabotage tool against a targeted operator, or as a mechanism enabling an operator to disguise self-funding through a third-party wallet to evade other detections.

### Recommendation
Do not trust the payout `OP_RETURN` field alone for attribution. Either:
- Require the `OP_RETURN` output (and ideally the anchor output) to be covered by the signature by changing the payout scheme to a sighash that commits to all outputs (e.g. `SIGHASH_ALL`) where possible, or by having the operator co-sign/commit additional data that binds their identity to the actual funding inputs of the transaction; or
- Change attribution logic in `update_finalized_payouts`/`PayoutCheckerTask` to also verify that the credited operator's own wallet/key was actually used to fund the additional (non-withdrawal) inputs of the confirmed payout transaction (e.g., by requiring those inputs to be spendable only by that operator's key, or by having the operator's own signature over the whole broadcast tx recorded and checked before treating a payout as "theirs").

### Proof of Concept
```rust
// core/src/test/... (new test, illustrative)
// 1. Build the legitimate withdrawal UTXO + user S+AP signature exactly as
//    generate_withdrawal_transaction_and_signature() does in core/src/test/common/setup_utils.rs.
// 2. Build payout_tx_a = create_payout_txhandler(input_utxo, output_txout, operator_a_xonly_pk, user_sig, network)
//    Build payout_tx_b = create_payout_txhandler(input_utxo, output_txout, operator_b_xonly_pk, user_sig, network)
//    where operator_a_xonly_pk != operator_b_xonly_pk, everything else identical.
// 3. Assert both txhandlers succeed at set_p2tr_key_spend_witness/promote (i.e. the identical
//    user_sig / sighash passes verification for both variants):
//    assert_eq!(payout_tx_a.get_cached_tx().input, payout_tx_b.get_cached_tx().input);
//    assert_eq!(payout_tx_a.get_cached_tx().output[0], payout_tx_b.get_cached_tx().output[0]);
//    assert_ne!(payout_tx_a.get_cached_tx().output[2], payout_tx_b.get_cached_tx().output[2]); // differing OP_RETURN
// 4. Fund/broadcast payout_tx_b (attacker adds their own extra input under ANYONECANPAY to cover
//    output value/fee, per fund_raw_transaction usage in Operator::withdraw), mine it.
// 5. Call Verifier::update_finalized_payouts (or the underlying db path) on the block containing
//    payout_tx_b and assert:
//    let (op_pk, ..) = db.get_payout_info_from_move_txid(None, move_txid).await?.unwrap();
//    assert_eq!(op_pk, Some(operator_b_xonly_pk)); // credited to operator_b despite operator_b never signing/funding
// 6. Optionally run PayoutCheckerTask::run_once for operator_b and confirm it proceeds into
//    handle_finalized_payout/kickoff, proving the automated reimbursement path is reachable.
```