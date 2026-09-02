### Title
Payout-tx OP_RETURN is not signature-committed, letting anyone spoof the recorded operator xonly-pk and get an honest operator falsely challenged/unable to disprove - ([File: core/src/verifier.rs], [File: circuits-lib/src/bridge_circuit/mod.rs])

### Summary
`create_payout_txhandler` only has the user sign input 0 / output 0 with `SinglePlusAnyoneCanPay` (`core/src/builder/transaction/operator_reimburse.rs:407-436`), leaving all other outputs — including the operator's OP_RETURN commitment — unauthenticated. `get_first_op_return_output` (`circuits-lib/src/bridge_circuit/mod.rs:688-692`) blindly picks the first OP_RETURN output by position, so anyone who observes the honest payout tx can construct a competing/replacing transaction with the same signed input/output but an extra malformed OP_RETURN placed earlier, causing both `update_finalized_payouts` (`core/src/verifier.rs:2312-2328`) and the bridge circuit's `deposit_constant` computation (`circuits-lib/src/bridge_circuit/mod.rs:206-219`) to fail to recover the real operator xonly-pk.

### Finding Description
Binding that should hold: `payout_payer_operator_xonly_pk (as stored via update_finalized_payouts / read by is_kickoff_malicious) == operator_xonly_pk that the operator actually used in create_payout_txhandler when fronting the withdrawal`.

Trace:
1. `Operator::withdraw` (`core/src/operator.rs:560-637`) builds the payout tx with `create_payout_txhandler` (`core/src/builder/transaction/operator_reimburse.rs:407-436`): output[0] = user payout, output[1] = anchor, output[2] = OP_RETURN(operator_xonlypk). Only input 0 and output 0 are covered by the user's `SinglePlusAnyoneCanPay` signature (`txhandler.set_p2tr_key_spend_witness(&user_sig, 0)`, verified in `withdraw` via `SECP.verify_schnorr` at `core/src/operator.rs:630-637`). Nothing in the signature commits to output[1] or output[2], their order, count, or presence.
2. Because the transaction uses `NON_STANDARD_V3` + an anchor output (CPFP fee-bumping style), it is typically broadcast with low/zero direct fee and is designed to be fee-bumped later — leaving a window before confirmation.
3. An attacker (unprivileged, can broadcast Bitcoin transactions and pay fees per the threat model) copies input 0 (`in_outpoint`) with the identical committed witness/signature and identical output 0, then constructs an alternate transaction that inserts an earlier OP_RETURN output with no data push (e.g., raw `OP_RETURN`) before the operator's legitimate OP_RETURN, and rebroadcasts it with a higher fee (RBF) or otherwise gets it mined first.
4. This attacker transaction is fully valid: the sighash flag `SinglePlusAnyoneCanPay` only commits to input 0's outpoint/amount/scriptPubKey and to output 0 — every other input/output is unconstrained, so Bitcoin consensus accepts it.
5. `Verifier::update_finalized_payouts` (`core/src/verifier.rs:2312-2328`) later scans this on-chain tx: `get_first_op_return_output` returns the attacker's malformed OP_RETURN (first by position); `parse_op_return_data` (`circuits-lib/src/bridge_circuit/mod.rs:609-617`) returns `None` because there is no push after `OP_RETURN`; `operator_xonly_pk` becomes `None` and is written to the DB (`update_payout_txs_and_payer_operator_xonly_pk`, `core/src/database/verifier.rs:198-251`, column allows NULL per `core/src/database/schema.sql:276`).
6. `Verifier::is_kickoff_malicious` (`core/src/verifier.rs:1857-1915`) reads `operator_xonly_pk_opt = None` and unconditionally treats the kickoff as malicious (`core/src/verifier.rs:1882-1885`), regardless of the fact that the operator genuinely fronted the payout. This causes `handle_kickoff` (`core/src/verifier.rs:1966-2027`) to queue a `Challenge` tx against the honest operator.
7. Worse, the *same* `get_first_op_return_output`/`parse_op_return_data` sequence is used inside the actual bridge circuit to compute `deposit_constant` when the operator must defend itself by sending asserts (`circuits-lib/src/bridge_circuit/mod.rs:206-219`, and mirrored in host-side `bridge-circuit-host/src/structs.rs:485-503`). Both use `.expect("Invalid operator xonlypk")`/`.ok_or(...)`. Since the on-chain payout tx now contains the attacker's malformed leading OP_RETURN, the operator's own bridge-circuit proof generation panics/fails at the exact same point, so the operator cannot construct a valid assert/disprove-defense for a challenge it did nothing to deserve.
8. No existing guard prevents this: `is_deposit_valid`, `SECP.verify_schnorr`, `is_profitable`, `SPV::verify`, and `verify_storage_proofs` all validate the withdrawal outpoint/amount/signature, but none of them constrain the position or uniqueness of the OP_RETURN output within the broadcast payout transaction, and none of them re-derive "the operator's OP_RETURN" from anything other than "the first OP_RETURN found."

### Impact Explanation
An honest operator that correctly fronted a withdrawal can be:
- Falsely flagged as malicious (`is_kickoff_malicious` → `true`) and challenged on-chain, even though it paid the user correctly.
- Rendered unable to construct a valid bridge-circuit proof (`SendOperatorAsserts` panics on `parse_op_return_data`/`get_first_op_return_output`), because the deposit_constant computation depends on the same attacker-corruptible OP_RETURN ordering.

This can lead to the operator's collateral being burned via the disprove/challenge/timeout mechanics (since it cannot produce the assert chain the disprove path relies on), or at minimum leaves the operator permanently unable to be correctly reimbursed for a legitimately fronted payout — matching the Critical categories "an honest operator permanently unable to be reimbursed" and "an honest operator's collateral burned." The attack is repeatable against any withdrawal/operator pair, since it only depends on the structural property (SIGHASH_SINGLE|ANYONECANPAY leaving all but input0/output0 unauthenticated) that is common to every payout transaction produced by `create_payout_txhandler`.

### Likelihood Explanation
Preconditions are attacker-affordable and match the stated threat model exactly: the attacker only needs to observe a broadcast (mempool-visible) payout transaction, reuse its public witness/signature for input 0 and output 0, and construct/broadcast a competing transaction with extra outputs, paying enough fee to have it mined instead of (or racing) the operator's original transaction. Because the payout tx design intentionally minimizes its own fee (CPFP anchor pattern), replacing/out-racing it is inexpensive. No special key material, collateral, or privileged role is required — only the ability to build and broadcast a Bitcoin transaction, which is explicitly within the allowed attacker capability set. This is repeatable for every withdrawal processed by any operator.

### Recommendation
Do not select the OP_RETURN "by first occurrence anywhere in the transaction." Instead:
- Fix the expected output index of the operator OP_RETURN (e.g., always output index 2, matching `create_payout_txhandler`'s construction) and validate that no other OP_RETURN precedes it, or
- Commit the operator's xonly-pk / OP_RETURN output cryptographically into something the withdrawal signature covers (impossible today since user's `SinglePlusAnyoneCanPay` signature intentionally leaves this open), or, more robustly,
- Require the operator to co-sign (or otherwise authenticate) its own OP_RETURN output/index so downstream consumers (`update_finalized_payouts` and the bridge circuit) can deterministically and unambiguously recover the *actual* fronting operator regardless of any additional/malformed outputs an unrelated party may append to the payout transaction.
- At minimum, treat `is_kickoff_malicious`'s "operator_xonly_pk missing" branch and `is_kickoff_malicious`'s "mismatch" branch differently from a true dishonest-payout scenario, and add reconciliation logic that scans *all* OP_RETURN outputs (not just the first) for one that decodes to a valid xonly-pk matching `kickoff_data.operator_xonly_pk`, rather than immediately assuming maliciousness on the first (potentially attacker-inserted) unparsable OP_RETURN.

### Proof of Concept
```rust
// core/src/verifier.rs (add test near update_finalized_payouts tests)
// or circuits-lib/src/bridge_circuit/mod.rs test module

#[test]
fn test_get_first_op_return_output_is_spoofable_by_third_party() {
    // 1. Build the honest payout tx as `create_payout_txhandler` would:
    //    output[0] = user payout, output[1] = anchor, output[2] = OP_RETURN(operator_xonlypk)
    let honest_tx = build_honest_payout_tx(); // reuse test helpers in operator_reimburse.rs / setup_utils.rs

    // 2. Simulate an attacker constructing a variant transaction that:
    //    - spends the SAME input[0] outpoint with the SAME witness (SinglePlusAnyoneCanPay covers only input0/output0)
    //    - keeps output[0] identical
    //    - inserts a malformed bare OP_RETURN (no push) as output[1], ahead of the honest OP_RETURN which is pushed to output[3]
    let attacker_tx = build_attacker_variant(&honest_tx);

    // ASSERT LEFT SIDE (expected binding): the operator xonly-pk recovered should equal the
    // xonly-pk the operator actually placed in create_payout_txhandler.
    let expected_pk = honest_operator_xonly_pk();

    // ASSERT RIGHT SIDE (actual behavior): get_first_op_return_output/parse_op_return_data
    // on the attacker's broadcastable, signature-valid transaction.
    let circuit_tx = CircuitTransaction::from(attacker_tx.clone());
    let first_op_return = get_first_op_return_output(&circuit_tx);
    let recovered_pk = first_op_return
        .and_then(|out| parse_op_return_data(&out.script_pubkey))
        .and_then(|bytes| XOnlyPublicKey::from_slice(bytes).ok());

    // Binding is broken: recovered_pk is None, not Some(expected_pk)
    assert_ne!(recovered_pk, Some(expected_pk));
    assert_eq!(recovered_pk, None);

    // 3. Demonstrate downstream consequence: is_kickoff_malicious treats this as malicious
    // even though the operator legitimately fronted the payout (would require DB/verifier
    // test harness — see core/src/database/verifier.rs::update_get_payout_txs_from_citrea_withdrawal
    // as a template for asserting payout_payer_operator_xonly_pk ends up NULL for a legitimately
    // fronted withdrawal whose payout tx was replaced/mutated by a third party).
}
```
Run with `cargo test -p circuits-lib test_get_first_op_return_output_is_spoofable_by_third_party` and a companion `cargo test -p clementine-core` case built on `core/src/database/verifier.rs`'s existing `update_get_payout_txs_from_citrea_withdrawal` pattern to show `get_payout_info_from_move_txid` returning `operator_xonly_pk = None` for a transaction that nonetheless correctly paid the withdrawal recipient — no mainnet, no live Citrea required.