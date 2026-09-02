## Title
Attacker-forged OP_RETURN-shaped payout output lets `get_first_op_return_output` misattribute a genuine payout to the wrong operator, permanently blocking the honest operator's reimbursement - (File: core/src/builder/transaction/operator_reimburse.rs)

### Summary
`Operator::withdraw` (`core/src/operator.rs:560-627`) builds the payout tx via `create_payout_txhandler` (`core/src/builder/transaction/operator_reimburse.rs:407-436`) using an attacker-supplied `out_script_pubkey` for output index 0, without ever checking it is a standard (non-OP_RETURN) script — unlike `Aggregator::optimistic_payout` and `Verifier::sign_optimistic_payout`, which explicitly reject non-p2tr/p2pkh/p2sh/p2wpkh/p2wsh scripts (`core/src/rpc/aggregator.rs:1044-1054`, `core/src/verifier.rs:1588-1599`). Because `get_first_op_return_output`/`update_finalized_payouts` (`core/src/verifier.rs:2312-2321`) scans outputs in order and takes the *first* OP_RETURN, an attacker-controlled output at index 0 crafted as `OP_RETURN <victim_operator_xonly_pk>` is picked up instead of the genuine operator OP_RETURN at index 2, causing the DB to record the wrong `payout_payer_operator_xonly_pk`.

### Finding Description
The binding this relies on is: `operator_xonly_pk parsed by get_first_op_return_output(circuit_payout_tx)/parse_op_return_data == operator_xonly_pk embedded by create_payout_txhandler at UtxoVout index 2 (the real operator OP_RETURN)`. This binding silently breaks because `create_payout_txhandler` places `output_txout` (fully attacker-chosen) at index 0, the anchor at index 1, and the real `op_return_txout(operator_xonly_pk)` at index 2 (`core/src/builder/transaction/operator_reimburse.rs:428-433`), while `get_first_op_return_output` (`circuits-lib/src/bridge_circuit/mod.rs:688-692`) simply returns the *first* output whose script is OP_RETURN-shaped, with no positional constraint.

Root cause: `Operator::withdraw` never validates `out_script_pubkey`'s script type before calling `create_payout_txhandler` (`core/src/operator.rs:583-626`). The user's `SinglePlusAnyoneCanPay` signature (`core/src/rpc/parser/operator.rs:161-203`) commits to output index 0 (script + amount) via TapSighashType::SinglePlusAnyoneCanPay, but this only proves the withdrawing user authorized *this specific output content* — it does not prevent the withdrawing user from choosing an OP_RETURN-shaped script for their own withdrawal output. The threat model explicitly allows the attacker (the withdrawing user) to choose the withdrawal UTXO bytes, the signature/sighash flag, and the OP_RETURN/script content.

Exploit flow:
1. Attacker (a legitimate Citrea withdrawer) crafts `WithdrawParams.output_script_pubkey = OP_RETURN <victim operator B's public xonly_pk>` (operator xonly pubkeys are public, from `Operator::GetParams`).
2. Attacker signs the sighash of the resulting payout tx (which they control since they choose the output) and submits via `Aggregator::withdraw` (`core/src/rpc/aggregator.rs:1811-1887`), which forwards `WithdrawParamsWithSig` unmodified to each operator's `withdraw` gRPC.
3. Honest operator A, who actually owns/funds the deposit and is willing to front the withdrawal, processes it via `Operator::withdraw` (`core/src/operator.rs:560-627`) — no check rejects the OP_RETURN-shaped destination — and broadcasts a valid payout tx: output0 = `OP_RETURN<B>` (fake, attacker's), output1 = anchor, output2 = `OP_RETURN<A>` (genuine, embedded by `create_payout_txhandler`).
4. `update_finalized_payouts` (`core/src/verifier.rs:2283-2353`) on confirmation calls `get_first_op_return_output`, which returns output0 (fake `OP_RETURN<B>`), and stores `payout_payer_operator_xonly_pk = B` in the DB instead of A.
5. Operator A's own `PayoutCheckerTask::run_once` calls `get_first_unhandled_payout_by_operator_xonly_pk(A's own key)` (`core/src/task/payout_checker.rs:41-47`), which will never find this payout (DB says payer is B) — A can never call `handle_finalized_payout` for a payout it actually funded, permanently blocking its reimbursement flow (or if A still attempts a kickoff, `is_kickoff_malicious` at `core/src/verifier.rs:1887` will flag `operator_xonly_pk (B) != kickoff_data.operator_xonly_pk (A)` and mark the kickoff malicious).
6. Simultaneously, operator B's `PayoutCheckerTask` will find the row (DB payer == B) and attempt `handle_finalized_payout` for a payout it never funded — corrupting attribution in the other direction as well.
7. `deposit_constant` in the bridge circuit (`bridge-circuit-host/src/structs.rs:485-503`, `circuits-lib/src/bridge_circuit/mod.rs:206-219`) uses the same `get_first_op_return_output`, so downstream disprove/proof logic is also corrupted with the wrong operator key baked into the journal hash.

Existing guards that fail to catch this: `Operator::is_profitable` only checks fee amounts, not script type; `SECP.verify_schnorr` only validates the user's signature validity, not the semantic acceptability of the output script; the standard-script check (`is_p2tr`/`is_p2pkh`/`is_p2sh`/`is_p2wpkh`/`is_p2wsh`) exists only in the optimistic-payout code paths (`aggregator.rs:1044-1054`, `verifier.rs:1588-1599`), not in `Operator::withdraw`'s regular payout path.

### Impact Explanation
This corrupts ATTRIBUTION: an honest operator (A) who genuinely fronts a user's withdrawal from their own funds can be made permanently unable to be reimbursed (DB attribution moved to another operator, `is_kickoff_malicious` flags their legitimate kickoff as malicious), matching the Critical category "an honest operator permanently unable to be reimbursed." It simultaneously falsely attributes the payout to an uninvolved operator B, who may then attempt reimbursement flows for a payout they never made, and it corrupts the `deposit_constant` computed in the bridge/disprove circuit (used in `PROOF_SOUNDNESS`). This is repeatable across any withdrawal and against any known target operator xonly pubkey, at zero cost beyond the attacker's own withdrawal transaction fees.

### Likelihood Explanation
The precondition is simply that `Operator::withdraw` accepts an arbitrary `out_script_pubkey` from the withdrawing user without validating it is a standard payment script — which is confirmed present in the code (`core/src/operator.rs:560-627`) with no such check, in contrast to the optimistic path. The attacker only needs to be a legitimate Citrea withdrawer (in-scope, unprivileged actor per the threat model) and knowledge of a target operator's public xonly key (public information). No verifier/aggregator secrets or majority collusion needed if the aggregator forwards params unmodified (as observed in `core/src/rpc/aggregator.rs:1811-1887`, which does not itself validate `output_script_pubkey`'s type in this code path). This is a highly feasible, cheap, and repeatable attack across every withdrawal/operator pair.

### Recommendation
Add the same standard-script-type validation (`is_p2tr() || is_p2pkh() || is_p2sh() || is_p2wpkh() || is_p2wsh()`, explicitly rejecting OP_RETURN and any non-standard script) to `Operator::withdraw` before constructing `create_payout_txhandler`, mirroring the checks already present in `Aggregator::optimistic_payout` and `Verifier::sign_optimistic_payout`. Additionally, harden `get_first_op_return_output`/`update_finalized_payouts` to only inspect the OP_RETURN at the fixed, protocol-defined index (`UtxoVout` for the OP_RETURN output, i.e. the last output) rather than scanning for the first OP_RETURN-shaped script in the transaction, removing reliance on output ordering being non-adversarial.

### Proof of Concept
```rust
// core/src/test (illustrative outline, not exhaustive):
// 1. Set up two operators A and B in a regtest e2e harness (as in deposit_and_withdraw_e2e.rs).
// 2. Perform a deposit so operator A is eligible to front the withdrawal.
// 3. Craft output_script_pubkey = OP_RETURN push(operator_B.signer.xonly_public_key.serialize())
//    let fake_op_return = ScriptBuf::builder().push_opcode(OP_RETURN).push_slice(op_b_xonly_pk_bytes).into_script();
// 4. Build WithdrawParams with output_script_pubkey = fake_op_return, sign with
//    TapSighashType::SinglePlusAnyoneCanPay over the resulting payout tx sighash (as user).
// 5. Call operator_a_client.withdraw(WithdrawParamsWithSig { withdrawal: params, verification_signature }).
//    Assert Ok(_) is returned (i.e. no rejection for non-standard script pubkey).
// 6. Mine blocks to confirm the payout tx; run verifier's block-sync task so
//    update_finalized_payouts() executes.
// 7. Query DB: db.get_payout_info_from_move_txid(...) and assert
//    payout_payer_operator_xonly_pk == operator_B.xonly_pk   // WRONG attribution
//    (expected: == operator_A.xonly_pk, the actual payer)
// 8. Additionally assert operator_a's PayoutCheckerTask.run_once() returns Ok(false)
//    (no unhandled payout found for A), proving A can never proceed to handle_finalized_payout/kickoff
//    for a withdrawal it genuinely funded.
```