### Title
Forged OP_RETURN via SIGHASH_SINGLE|ANYONECANPAY substitution lets an operator be credited/reimbursed for a payout it never funded - (File: core/src/builder/transaction/operator_reimburse.rs, core/src/operator.rs, core/src/verifier.rs, core/src/task/payout_checker.rs)

### Summary
The user's payout authorization signature uses `SIGHASH_SINGLE | SIGHASH_ANYONECANPAY`, which only commits to input 0 and output 0 of the Payout transaction, leaving the OP_RETURN output (index 2, carrying `operator_xonly_pk`) and all other inputs unsigned. Because `TxSenderClientQueueExt::add_tx_to_queue` queues `TransactionType::Payout` as a plain RBF transaction with empty `cancel_outpoints`/activation lists, an attacker who observes the broadcast payout tx can build a replacement spending the same withdrawal outpoint, keep output 0 identical, self-fund the transaction, and swap the OP_RETURN to name an arbitrary registered operator, causing that operator's automated `PayoutCheckerTask` to claim reimbursement for a payout it never funded.

### Finding Description
The claimed binding is:
`operator_xonly_pk` parsed by `update_finalized_payouts` from the confirmed payout tx's OP_RETURN (core/src/verifier.rs:2319-2321) == xonly_pk of the party whose inputs actually funded output[0] of that tx.

Trace:
1. `Operator::withdraw` (core/src/operator.rs:560-637) verifies the user's signature with `SECP.verify_schnorr` against `in_signature.sighash_type`, documented and enforced to be `SinglePlusAnyoneCanPay` (core/src/operator.rs:637). This sighash type commits only to input 0 (the withdrawal UTXO) and output 0 (the user payout) per BIP341 — the anchor output and the OP_RETURN (`operator_xonly_pk`, set in `create_payout_txhandler`, core/src/builder/transaction/operator_reimburse.rs:407-436) are **not** covered by the user's signature, and `ANYONECANPAY` permits arbitrary additional inputs.
2. The operator funds the tx via `fund_raw_transaction`/`sign_raw_transaction_with_wallet` and broadcasts it (core/src/operator.rs:651-689).
3. `add_tx_to_queue` (core/src/tx_sender_queue.rs:92-105) inserts `TransactionType::Payout` with `FeePayingType::RBF` and empty `cancel_outpoints`/`cancel_txids`/`activate_*` — nothing pins the eventual confirmed txid to the originally-broadcast one beyond ordinary Bitcoin RBF fee-bump rules.
4. Because the OP_RETURN and extra funding inputs are unsigned, any party can take the leaked signature+sighash and build a second transaction: same input 0 (same signature valid, since sighash inputs unchanged), same output 0 (SIGHASH_SINGLE forces this), but with its own additional funding inputs/fee and a different OP_RETURN naming an arbitrary registered operator's `operator_xonly_pk`, then get it accepted by ordinary Bitcoin RBF (better feerate) ahead of the original.
5. Once confirmed, `Verifier::update_finalized_payouts` (core/src/verifier.rs:2283-2352) parses the substituted OP_RETURN and stores that arbitrary operator's xonly_pk as `payout_payer_operator_xonly_pk` for the withdrawal, with no check that this operator actually supplied the funding inputs.
6. That operator's own `PayoutCheckerTask::run_once` (core/src/task/payout_checker.rs:39-79) polls `get_first_unhandled_payout_by_operator_xonly_pk` keyed purely on its own `operator.signer.xonly_public_key`, finds the forged entry, and automatically calls `handle_finalized_payout` → builds/sends a Kickoff and proceeds to reimbursement, with no verification step confirming the operator itself broadcast/funded that specific payout tx.
7. `Verifier::is_kickoff_malicious` (core/src/verifier.rs:1859-1915) only checks that the OP_RETURN-derived `operator_xonly_pk` equals `kickoff_data.operator_xonly_pk` — which trivially holds since the attacker chose that operator's key — so this guard does not catch the forgery; it was never designed to bind funding-source to OP_RETURN identity.

No existing guard (`is_kickoff_malicious`, `SECP.verify_schnorr`, DB uniqueness) checks that the party named in the OP_RETURN is the party whose inputs funded the transaction; the signature verified in `withdraw` structurally cannot enforce that because of the chosen sighash flag.

### Impact Explanation
An honest, previously-uninvolved operator is silently credited in the DB and then automatically reimbursed (collateral movement through Round/Reimburse tx flow) for a payout it never funded — the withdrawal was in fact paid for by the attacker's own funding inputs. This matches the Critical impact category "an operator credited and reimbursed for a payout it never funded." It is repeatable for every withdrawal broadcast under this scheme and can target any registered operator (the attacker only needs the operator's public `operator_xonly_pk`), so the blast radius spans all deposits/withdrawals and all operators using this queue path.

### Likelihood Explanation
Preconditions: attacker must observe a broadcast (unconfirmed) Payout transaction — standard mempool visibility — extract the witness signature, and be capable of funding a competing/higher-feerate transaction from their own BTC to win Bitcoin's ordinary RBF replacement before the original confirms. This requires only ordinary Bitcoin transaction construction skill, fee-market awareness, and the withdrawal amount + fee in BTC (an actual monetary cost to the attacker, since output[0]/the user payment is unavoidably paid). No verifier/aggregator/operator privilege is needed. Feasibility depends on winning a mempool race, which is realistic given typical confirmation delays and no in-protocol pinning of the specific broadcasting operator to the eventual confirmed OP_RETURN.

### Recommendation
Bind the payout's funding party to the OP_RETURN cryptographically: require the sighash used by the operator to cover the OP_RETURN output (e.g., have the operator itself co-sign with `SIGHASH_ALL` over all outputs including the OP_RETURN via an additional signature/covenant, or restructure so the OP_RETURN commitment is embedded in a scriptPath the operator must satisfy), and additionally have `PayoutCheckerTask`/`handle_finalized_payout` cross-check against a local record that this specific operator itself broadcast that exact `payout_txid` (e.g., only auto-claim payouts whose txid matches one this operator's own `withdraw()` call produced and stored, not merely OP_RETURN pattern-matching from chain scan).

### Proof of Concept
```
cargo test -p clementine-core --test tx_sender_payout_substitution -- --nocapture
```
Plan:
1. Set up regtest, register two operators A (honest) and B (target), create a deposit/move-to-vault, register a withdrawal via Citrea mock with `input_outpoint = U`.
2. Call `Operator::withdraw` for operator A; capture the resulting signed Payout tx `tx_A` and the user's `taproot::Signature` (SinglePlusAnyoneCanPay) used for input 0.
3. Before `tx_A` confirms, construct `tx_B`: input 0 = same `U`, add attacker-controlled funding input(s)/change output(s), output 0 identical to `tx_A`'s output 0, OP_RETURN = operator B's `operator_xonly_pk`, higher fee than `tx_A`. Reuse the witness from `tx_A` for input 0 (still valid since sighash inputs unchanged).
4. Broadcast `tx_B` with higher feerate to replace `tx_A` via RBF; mine blocks so `tx_B` confirms and `tx_A` is evicted.
5. Run `Verifier::update_finalized_payouts`; assert `get_payout_info_from_move_txid` returns `payout_payer_operator_xonly_pk == operator_B.xonly_public_key` (equality of the two sides of the broken binding, showing it now equals B's key though B funded nothing).
6. Run `PayoutCheckerTask` for operator B and assert it proceeds to `handle_finalized_payout`/kickoff without error, and that `is_kickoff_malicious` returns `false` for operator B's kickoff — demonstrating operator B is credited/reimbursed for a payout it never funded.