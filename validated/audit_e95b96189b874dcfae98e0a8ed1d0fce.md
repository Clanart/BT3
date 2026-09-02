### Title
Unauthenticated OP_RETURN payer attribution in payout txs lets an attacker frame an operator as payer, causing that operator to be reimbursed for a withdrawal it never funded - ([File: core/src/verifier.rs])

### Summary
`Verifier::update_finalized_payouts` attributes a payout to an operator purely by reading an OP_RETURN output from whichever transaction happens to spend the committed withdrawal UTXO, with no check that the named operator actually produced or funded that transaction. Because the payout tx's only signed commitment (`SIGHASH_SINGLE|ANYONECANPAY`) covers input 0 and output 0 only, any unprivileged party who can obtain (or self-generate) a valid withdrawal signature can build and broadcast the payout tx themselves, place any operator's xonly pubkey in the OP_RETURN, and thereby make that operator's own `PayoutCheckerTask` believe it fronted the withdrawal and trigger a kickoff/reimbursement it never earned.

### Finding Description
The claimed binding is:
`withdrawals.payout_payer_operator_xonly_pk` (written by `update_payout_txs_and_payer_operator_xonly_pk`) == the xonly pubkey of the party whose own funds satisfied output 0 of the payout tx.

`update_finalized_payouts` (`core/src/verifier.rs:2283-2353`) looks up the payout tx that spends the withdrawal UTXO via `get_payout_txs_for_withdrawal_utxos` (`core/src/database/verifier.rs:170-196`), parses `get_first_op_return_output`/`parse_op_return_data` on it, and stores whatever 32-byte value is found there as `operator_xonly_pk` with no signature check at all (verifier.rs:2312-2342). This value is then persisted to `payout_payer_operator_xonly_pk` (`core/src/database/verifier.rs:198-251`).

The payout tx itself (`create_payout_txhandler`, `core/src/builder/transaction/operator_reimburse.rs:407-436`) has:
- input 0: the withdrawal UTXO, spent with the user-supplied `taproot::Signature` (`in_signature`), whose sighash flag is chosen by whoever calls Citrea's `withdraw()`/the operator's `withdraw` RPC (`core/src/operator.rs:560-637`, `core/src/rpc/operator.rs:168-258`);
- output 0: user payout (the only output committed if `SIGHASH_SINGLE|ANYONECANPAY` is used);
- output 2: the OP_RETURN with an arbitrary operator xonly pubkey (`operator_reimburse.rs:418`), which is *not* covered by the SINGLE+ANYONECANPAY commitment.

Per the threat model the attacker can call `withdraw` on the Citrea Bridge contract and choose the withdrawal UTXO bytes, the Schnorr signature and its sighash flag. With `SIGHASH_SINGLE|ANYONECANPAY`, the attacker (or anyone) can independently reconstruct the payout tx, add their own funding inputs to satisfy output 0, and set output 2's OP_RETURN to an arbitrary operator C's xonly pubkey, then broadcast it directly to Bitcoin — completely bypassing operator C's `withdraw`/`internal_withdraw` gRPC.

C's own `PayoutCheckerTask::run_once` (`core/src/task/payout_checker.rs:39-111`) then queries `get_first_unhandled_payout_by_operator_xonly_pk(self.operator.signer.xonly_public_key)` (`core/src/database/verifier.rs:282-313`), which matches purely on `payout_payer_operator_xonly_pk = C` and `is_payout_handled = FALSE` — it never checks whether C's own node ever broadcast a payout tx for this withdrawal. It then calls `Operator::handle_finalized_payout` (`core/src/operator.rs:839-`), which allocates a fresh kickoff connector and signs/broadcasts a Kickoff tx crediting C.

Existing guards do not catch this:
- `Verifier::is_kickoff_malicious` (`core/src/verifier.rs:1859-1915`) only checks that the DB-stored `operator_xonly_pk` equals `kickoff_data.operator_xonly_pk` (i.e., that the kickoff sender matches the poisoned attribution it itself created) and that the committed payout blockhash matches — since the attacker wrote C's own key, and C is the one who issues the kickoff, both checks trivially pass.
- `Operator::validate_payer_is_operator` (`core/src/operator.rs:1687-1740`) likewise only compares the same poisoned `payout_payer_operator_xonly_pk` column against `self.signer.xonly_public_key` — it re-reads the same attacker-controlled data rather than independently verifying fund provenance.

No component ever verifies that the named operator's own key, signature, or wallet actually funded output 0 of the payout tx; attribution is 100% derived from unauthenticated bytes in an on-chain OP_RETURN that a third party controls under `SIGHASH_SINGLE|ANYONECANPAY`.

### Impact Explanation
Operator C is forced into consuming one of its limited kickoff connectors and its round/collateral cycle to claim a reimbursement for a withdrawal it never fronted with its own money — matching the listed Critical impact "an operator reimbursed for a payout it never funded." This is repeatable per deposit/withdrawal and against any operator whose xonly pubkey the attacker chooses to embed, since the attacker only needs to win the race to spend the withdrawal UTXO with their own funding inputs and a false OP_RETURN. At minimum this is a griefing vector that forces an operator into an unwanted kickoff/challenge cycle; in the case where the kickoff proceeds unchallenged it results in C being reimbursed for funds it never fronted, and the true payer (the attacker) receives nothing back for the BTC it actually spent to fulfill the withdrawal — the bridge's compensation flow is misdirected to the wrong party.

### Likelihood Explanation
The attacker only needs standard capabilities already granted in the threat model: ability to call `withdraw` on the Citrea Bridge contract, choose a withdrawal UTXO and a Schnorr signature/sighash flag, and broadcast a Bitcoin transaction. No verifier, operator, or aggregator privilege is required. Cost is limited to the BTC needed to satisfy output 0 of the payout tx plus mining fees — the same cost a legitimate operator would pay, so the attacker can mount this at will for any withdrawal it can win the race on. This does not require majority hashrate, key compromise, or any other excluded precondition.

### Recommendation
Do not trust an unauthenticated OP_RETURN as proof of who funded a payout. Require the payout tx's OP_RETURN commitment (and/or the operator-attribution data) to be covered by a signature verifiably tied to the named operator's own key (e.g., have the operator sign over the OP_RETURN output with their own key, and have `update_finalized_payouts`/`is_kickoff_malicious` verify that signature, or otherwise structurally bind output 2 into the same sighash that commits output 0), so that a third party cannot attribute a payout to an operator that never produced/signed it.

### Proof of Concept
```rust
// cargo test proof outline (core/src/database/verifier.rs / task/payout_checker.rs)
#[tokio::test]
async fn attacker_can_frame_operator_via_op_return() {
    // 1. Set up withdrawal utxo + citrea deposit as in
    //    update_get_payout_txs_from_citrea_withdrawal (core/src/database/verifier.rs:390).
    // 2. Simulate the attacker's payout tx: spend the withdrawal utxo with a
    //    SIGHASH_SINGLE|ANYONECANPAY user signature, add attacker-funded inputs,
    //    and set OP_RETURN to operator C's xonly_pk (NOT C's own broadcast tx).
    // 3. Call db.update_payout_txs_and_payer_operator_xonly_pk(
    //        Some(&mut dbtx), vec![(idx, attacker_payout_txid, Some(operator_c_pk), block_hash)]
    //    ) -- mirroring what update_finalized_payouts would persist after parsing
    //    the attacker's OP_RETURN.
    // 4. Assert C's own db has no record of ever broadcasting attacker_payout_txid
    //    via operator withdraw()/internal_withdraw (i.e., no local funding evidence).
    // 5. Call db.get_first_unhandled_payout_by_operator_xonly_pk(None, operator_c_pk)
    //    and assert it returns Some((idx, move_txid, block_hash)) -- proving C's
    //    PayoutCheckerTask::run_once would pick this up and call
    //    handle_finalized_payout, issuing a kickoff for C.
    // 6. Assert validate_payer_is_operator(deposit_id) for operator C succeeds
    //    (returns Ok) even though C never funded output 0 -- proving no guard
    //    blocks the false credit.
    //
    // Binding check:
    //   LHS: db.get_payer_xonly_pk_blockhash_and_kickoff_txid_from_deposit_id(deposit_id).0 == Some(operator_c_pk)
    //   RHS: actual funder of attacker_payout_tx output 0 == attacker (NOT operator_c_pk)
    //   assert_ne!(LHS, RHS)  // binding is broken
}
```