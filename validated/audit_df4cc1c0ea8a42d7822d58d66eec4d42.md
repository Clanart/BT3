### Title
Payout tx OP_RETURN is not covered by `SIGHASH_SINGLE|ANYONECANPAY`, letting anyone strip it and get the honest operator falsely Challenged - (File: `core/src/verifier.rs`)

### Summary
The signature (`in_signature`) authorizing a payout tx spend is enforced to use `TapSighashType::SinglePlusAnyoneCanPay` (`core/src/rpc/parser/operator.rs:174-187`), which commits only to input 0 and output 0 (the user's payout output), not to output 2 (the OP_RETURN carrying `operator_xonly_pk`) or output 1 (anchor). Anyone who observes a broadcast (mempool or confirmed) payout tx can therefore build a still-validly-signed variant that drops the OP_RETURN output and get it mined instead of/alongside the operator's original, causing `update_finalized_payouts` (`core/src/verifier.rs:2283-2353`) to record `operator_xonly_pk = NULL` for that withdrawal, which forces `is_kickoff_malicious` (`core/src/verifier.rs:1882-1885`) to unconditionally return `Ok(true)`, triggering an automatic `Challenge` against the honest operator's later kickoff (`core/src/verifier.rs:2005-2016`).

### Finding Description
Binding claimed: `withdrawals.payout_payer_operator_xonly_pk` for index `i` == xonly pk of the operator that actually funded `output_txout` for withdrawal `i`, and must be `Some` whenever a registered operator did the funding.

- `create_payout_txhandler` (`core/src/builder/transaction/operator_reimburse.rs:407-436`) builds the payout tx with input 0 (user's withdrawal UTXO, key-spend), output 0 (`output_txout`, user's payout), output 1 (anchor), output 2 (OP_RETURN with `move_txid || operator_xonly_pk`), and signs input 0 via `set_p2tr_key_spend_witness(&user_sig, 0)`.
- `parse_withdrawal_sig_params` (`core/src/rpc/parser/operator.rs:161-203`) enforces the signature's sighash type is `TapSighashType::SinglePlusAnyoneCanPay`. Under BIP341 rules, `SIGHASH_SINGLE` commits only to the output at the *same index* as the signed input (index 0 = the user payout output) and, combined with `ANYONECANPAY`, commits only to that one input. Outputs 1 (anchor) and 2 (OP_RETURN) are **not** covered by the signature at all.
- Therefore, given any broadcast (even unconfirmed, mempool-visible) payout tx, an unprivileged third party can take the same input (same outpoint + witness/signature, which remains valid since it doesn't sign over outputs 1/2) and the same output 0, and construct a new transaction that omits output 2 (the OP_RETURN) — a different txid, still validly spending the same withdrawal UTXO with the exact same signature.
- `get_payout_txs_for_withdrawal_utxos` (`core/src/database/verifier.rs:170-196`) determines "the" payout tx for withdrawal `idx` purely by which txid is recorded as *confirmed* (`bitcoin_syncer_spent_utxos`, populated per-block by `save_transaction_spent_utxos`, `core/src/bitcoin_syncer.rs:143-164`) spending `withdrawal_utxo_txid:vout`. It has no notion of "the operator's" tx specifically — whichever tx that spends the UTXO gets mined is treated as the payout.
- `update_finalized_payouts` (`core/src/verifier.rs:2283-2353`) then parses the OP_RETURN of whichever tx got mined. If the attacker's OP_RETURN-stripped copy is the one confirmed, `get_first_op_return_output` returns `None`, so `operator_xonly_pk` is stored as `NULL` in `withdrawals.payout_payer_operator_xonly_pk`.
- `is_kickoff_malicious` (`core/src/verifier.rs:1857-1915`) reads this row via `get_payout_info_from_move_txid`; when `operator_xonly_pk_opt` is `None` it logs "assuming malicious" and returns `Ok(true)` unconditionally (`core/src/verifier.rs:1882-1885`), regardless of which operator actually fronted the payout.
- `handle_kickoff` (`core/src/verifier.rs:1966-2026`) then queues a `Challenge` transaction against the operator's kickoff (`core/src/verifier.rs:2005-2016`) whenever `is_malicious` is true and this is the first malicious kickoff of the round — i.e., automatically, without any operator misbehavior.

No existing guard catches this: `is_deposit_valid`/`SPV::verify`/`verify_storage_proofs`/`lc_proof_verifier` operate on Citrea-side withdrawal registration and later bridge-circuit proof, not on which Bitcoin tx variant gets confirmed for a given withdrawal UTXO; there is no on-chain or DB uniqueness constraint tying the confirmed payout txid to a specific, signature-committed OP_RETURN.

### Impact Explanation
An honest operator that legitimately fronted a withdrawal has its later kickoff automatically Challenged (and consequently pushed into the Disprove path) purely because a third party — with no relationship to the deposit/withdrawal, no key material, and no privileged role — rebroadcast an OP_RETURN-stripped malleated copy of the operator's own payout transaction with a bumped fee and got it mined first (or instead). This burns the honest operator's collateral via the Challenge/Disprove flow. It is repeatable against any withdrawal/operator in the system as long as the attacker can observe the payout tx (mempool monitoring) before/around confirmation and can fee-bump a competing spend of the same UTXO. This matches the Critical impact category "an honest operator's collateral burned."

### Likelihood Explanation
This requires no privilege beyond broadcasting Bitcoin transactions and paying fees — a pure network participant. The attacker needs only to observe any operator's payout tx (public on the P2P network/mempool) for any withdrawal, strip the (uncommitted) OP_RETURN output, and mine it (or otherwise get it confirmed ahead of/instead of the original) with a higher fee, which is straightforward transaction malleation exploiting the sighash flag's scope. Attacker cost is only the fee delta needed to win the race against the operator's own broadcast/CPFP. This is feasible per-withdrawal and repeatable across all operators and deposits since the vulnerable code path (`SinglePlusAnyoneCanPay` sighash on the payout tx, plus OP_RETURN-based operator attribution) is universal to the protocol.

### Recommendation
Do not rely on an unauthenticated OP_RETURN output to attribute the payout to an operator. Either (a) require the operator's key to also sign/commit to the OP_RETURN output (e.g., have the operator co-sign the payout tx, or use `SIGHASH_ALL`/commit the OP_RETURN via a script-path condition bound to the operator's key rather than relying purely on the user's `SinglePlusAnyoneCanPay` signature), or (b) attribute payouts by which operator broadcast/CPFP'd the transaction (tracked by the operator's own tx-sender bookkeeping) instead of by parsing an unauthenticated OP_RETURN from whatever tx happens to be confirmed spending the withdrawal UTXO, and treat a "no OP_RETURN"/mismatched confirmation as a signal to look for the operator's own recorded payout txid/CPFP chain before concluding "malicious."

### Proof of Concept
```rust
// core/src/test/manual_reimbursement.rs (or a new e2e test)
// 1. Set up a deposit + registered withdrawal as in existing e2e helpers
//    (see core/src/test/common/clementine_utils.rs payout_and_start_kickoff).
// 2. Have operator 0 call Operator::withdraw(...) to build+broadcast the
//    legitimate payout tx (input0 = withdrawal UTXO w/ SinglePlusAnyoneCanPay
//    sig, output0 = user payout, output1 = anchor, output2 = OP_RETURN(move_txid||op0_pk)).
// 3. Before it confirms, construct an "attacker" tx reusing the exact same
//    input (outpoint+witness) and output0, dropping output2 (OP_RETURN),
//    with a higher fee (e.g. bump anchor value or add attacker-funded extra
//    input/output for fees not covered by SIGHASH_SINGLE).
// 4. Broadcast the attacker tx and mine it (assert it, not the operator's
//    original, is the one confirmed spending the withdrawal UTXO).
// 5. Run block sync to trigger update_finalized_payouts; assert
//    withdrawals.payout_payer_operator_xonly_pk IS NULL for this idx via
//    db.get_payout_info_from_move_txid(...) == (None, ..., ...).
// 6. Drive operator 0's kickoff through the state manager
//    (dispatch_new_kickoff_machine / check_if_kickoff_malicious) and assert
//    that tx_sender's queue contains a TransactionType::Challenge tx
//    targeting operator 0's kickoff, proving the honest operator is
//    challenged despite having correctly funded the payout.
```