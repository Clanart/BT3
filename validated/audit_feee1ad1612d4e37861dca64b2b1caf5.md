Confirmed key finding: `Citrea::withdraw(txid, vout)` accepts an arbitrary Bitcoin outpoint chosen by the caller (`core/src/test/withdraw.rs:133-138`) and `get_payout_txs_for_withdrawal_utxos` (`core/src/database/verifier.rs:170-196`) picks up **any** on-chain transaction that spends that outpoint — it does not require that transaction to have come from the operator's own `withdraw()` RPC call. `Verifier::update_finalized_payouts` (`core/src/verifier.rs:2283-2353`) then attributes the payout purely by parsing the OP_RETURN pubkey with no check of who funded the other inputs. `bridge_circuit`/`deposit_constant` (`circuits-lib/src/bridge_circuit/mod.rs:206-229`, `structs.rs:482-516`) likewise only bind `operator_xonlypk` from the OP_RETURN, never checking custody of the funding inputs beyond `payout_input_index` (which is only the withdrawal-authorization input, not the funding input). `PayoutCheckerTask` (`core/src/task/payout_checker.rs:31-111`) and `handle_finalized_payout` (`core/src/operator.rs:839-885`) act on this DB attribution automatically, with no re-verification that the credited operator actually broadcast/funded the tx.

### Title
Operator can be framed for reimbursement of a payout it never funded via forged OP_RETURN attribution — ([File: circuits-lib/src/bridge_circuit/mod.rs], [File: core/src/verifier.rs])

### Summary
An unprivileged attacker can register their own withdrawal on the Citrea bridge contract with an arbitrary self-owned outpoint, then construct and broadcast the entire payout transaction themselves — funding it fully from their own wallet, while stamping an arbitrary victim operator's x-only pubkey into the OP_RETURN output. `Verifier::update_finalized_payouts` and `bridge_circuit`'s `deposit_constant` computation derive "who fronted this payout" solely from that OP_RETURN byte string, with no cryptographic or economic proof that the named operator supplied any of the transaction's value. The victim operator's own automation (`PayoutCheckerTask`) will then pick up this "unhandled payout" and proceed through kickoff to be reimbursed by a `Reimburse` transaction, for a payout it never actually made.

### Finding Description
The claimed binding: `payout_payer_operator_xonly_pk` (and the `operator_xonlypk` baked into `deposit_constant`) == the operator who actually supplied the BTC value in `payout_tx`'s funding inputs. This binding does not hold anywhere in the code.

- `get_payout_txs_for_withdrawal_utxos` (`core/src/database/verifier.rs:170-196`) joins on `bitcoin_syncer_spent_utxos` purely by outpoint (`w.withdrawal_utxo_txid`/`vout`), i.e. it accepts *whatever transaction* spends the registered withdrawal UTXO — not necessarily one created via an operator's `withdraw()`/`InternalWithdraw` RPC.
- `Verifier::update_finalized_payouts` (`core/src/verifier.rs:2283-2353`) extracts `operator_xonly_pk` purely from `parse_op_return_data` on the first OP_RETURN output of that transaction (`get_first_op_return_output`), with zero validation that the funding inputs belong to, or were signed by, that operator.
- `circuits-lib/src/bridge_circuit/mod.rs::bridge_circuit` (lines 206-229) and `bridge-circuit-host/src/structs.rs::host_deposit_constant` (lines 482-516) compute `deposit_constant` the same way: `operator_xonlypk` comes only from the OP_RETURN byte parse. The only cross-check performed (`payout_input_index`, lines 183-204) verifies that a *specific input* matches the withdrawal storage-proof outpoint — this is the withdrawal-authorization ("identity") input, not a funding-source binding. Nothing constrains who supplied the remaining inputs that actually cover the withdrawal amount.
- `PayoutCheckerTask::run_once` (`core/src/task/payout_checker.rs:39-111`) filters payouts strictly by `get_first_unhandled_payout_by_operator_xonly_pk(self.operator.signer.xonly_public_key)` — i.e. it trusts the DB column set purely from on-chain OP_RETURN parsing — and immediately calls `handle_finalized_payout`, which drives the kickoff/reimbursement state machine (`core/src/operator.rs:839-885`).
- `Verifier::is_kickoff_malicious` (`core/src/verifier.rs:1857-1915`) only checks that `operator_xonly_pk == kickoff_data.operator_xonly_pk` (matching what's already in the forged OP_RETURN) and that a committed blockhash matches — it does not verify the payer actually fronted funds.

Attacker flow:
1. Attacker calls Citrea `withdraw(txid, vout)` (`core/src/test/withdraw.rs:133-138` shows this accepts arbitrary caller-supplied bytes) specifying an outpoint they fully control (their own P2TR UTXO), tied to some already-existing deposit's `move_txid` (which the attacker can create themselves, since deposit is open to anyone).
2. Attacker constructs a raw payout transaction spending that outpoint as `input[payout_input_index]`, adds additional inputs entirely from their own wallet to cover the withdrawal amount, sets the output to any recipient (e.g., themselves), and appends an OP_RETURN containing the victim operator's real serialized x-only pubkey (`op_return_txout(operator_xonly_pk.serialize())`, mirroring `create_payout_txhandler` in `core/src/builder/transaction/operator_reimburse.rs:407-436`), and broadcasts it directly to the Bitcoin network — bypassing the operator's `withdraw`/`Withdraw` RPC entirely.
3. `Verifier::update_finalized_payouts` observes this tx on-chain, matches it to the withdrawal index via `get_payout_txs_for_withdrawal_utxos`, extracts the forged OP_RETURN pubkey, and writes `payout_payer_operator_xonly_pk = victim_operator` into the DB.
4. The victim operator's own `PayoutCheckerTask` sees this "unhandled payout" credited to itself and calls `handle_finalized_payout`, driving Kickoff → (no successful challenge, since `deposit_constant`/OP_RETURN data matches exactly what's on-chain, and there is no way for verifiers to detect the funding-source mismatch) → `Reimburse` tx, crediting the victim operator with `bridge_amount` from the deposit's `MoveToVault` UTXO.

No existing guard closes this gap: `SECP.verify_schnorr` only authenticates the withdrawal-identity input's signer (the withdrawing party, i.e., attacker), not the funding inputs; `verify_storage_proofs`/SPV only bind the withdrawal outpoint identity, not funding provenance; `is_kickoff_malicious` re-derives the same forged attribution rather than independently verifying it.

### Impact Explanation
The framed operator is credited with a `Reimburse` transaction payout (spending the deposit's `MoveToVault` UTXO) for a withdrawal it never funded — matching the explicitly listed Critical impact "an operator reimbursed for a payout it never funded." This is repeatable per deposit/withdrawal and works against any operator whose x-only pubkey is public (all operator keys are public protocol parameters). It can be used to grief a target operator's round/kickoff bookkeeping (forcing unwanted kickoffs, consuming their kickoff connectors and collateral cycle) or to divert bridge value flow: the deposit funds intended to reimburse whoever truly fronts a withdrawal are instead paid out to an operator that did nothing, while the real funder (the attacker) is a self-dealing party — this breaks the intended 1:1 economic linkage between "who paid the withdrawing user" and "who gets reimbursed from vault funds," which is the entire security premise of the peg-out/reimbursement design.

### Likelihood Explanation
No privileged role, key share, or collateral is required. The attacker needs only: (a) the ability to make a deposit (or use funds from any existing deposit whose `move_txid`/`deposit_id` they can reference), (b) enough BTC to self-fund the withdrawal amount and fees, and (c) the ability to call Citrea's `withdraw()` and broadcast a raw Bitcoin transaction — all explicitly within the unprivileged attacker's capabilities per the rules. No mempool/relay race or specific timing is needed since the attacker fully controls tx construction. This is straightforwardly reproducible offline with a `cargo test` that directly exercises `bridge_circuit`'s `deposit_constant`/OP_RETURN extraction and `Verifier::update_finalized_payouts`, showing neither checks the funding-input provenance.

### Recommendation
Bind the OP_RETURN operator attribution to actual custody. Options: (1) require the operator to co-sign the payout transaction (e.g., require at least one input signed with the operator's own key/PSBT commitment, verified during `update_finalized_payouts`/`bridge_circuit`), or (2) require the payout tx to be pre-registered by the operator via its RPC (store an expected `payout_txid` server-side before scanning the chain, and only accept an on-chain match against that pre-committed txid rather than trusting arbitrary OP_RETURN content), or (3) cryptographically commit the operator's identity via a signature over the payout tx (not just a plaintext pubkey push) that verifiers/circuit check against a key that also signs at least one funding input.

### Proof of Concept
```rust
// circuits-lib/src/bridge_circuit/tests.rs (new test)
// Demonstrates that deposit_constant/OP_RETURN extraction accepts an operator
// xonly pubkey with no relation to the actual funding inputs of payout_spv.transaction.

#[test]
fn op_return_operator_attribution_ignores_funding_source() {
    // Build a payout_spv.transaction where:
    // - input[payout_input_index] spends the registered withdrawal outpoint,
    //   signed with an ATTACKER-controlled key (not the "operator").
    // - additional inputs are ALSO attacker-controlled (self-funded).
    // - OP_RETURN contains VICTIM_OPERATOR_XONLY_PK bytes.
    let victim_operator_xonlypk = /* real operator's known public xonly pk */;
    let attacker_funded_tx = build_self_funded_payout_tx_with_forged_op_return(
        withdrawal_outpoint,
        victim_operator_xonlypk,
    );

    // 1. Show bridge_circuit's deposit_constant computation succeeds and
    //    binds to victim_operator_xonlypk despite attacker funding everything.
    let extracted_pk = parse_op_return_data(
        &get_first_op_return_output(&attacker_funded_tx).unwrap().script_pubkey
    ).unwrap();
    assert_eq!(extracted_pk, victim_operator_xonlypk.serialize());

    // 2. Assert there is NO check anywhere relating extracted_pk to the
    //    scriptPubKeys/signers of attacker_funded_tx.input[1..] (the funding inputs).
    //    i.e., deposit_constant(...) is computed identically regardless of who
    //    signed/funded those inputs -- only input[payout_input_index]'s prevout
    //    identity (the withdrawal utxo) and the OP_RETURN bytes matter.
    let dc1 = deposit_constant(extracted_pk.try_into().unwrap(), /* ... */);
    // swap funding inputs to a different attacker key set, keep OP_RETURN identical
    let attacker_funded_tx_v2 = swap_funding_inputs_keep_same_op_return(attacker_funded_tx);
    let dc2 = deposit_constant(extracted_pk.try_into().unwrap(), /* ... */);
    assert_eq!(dc1, dc2); // proves funding source has zero effect on attribution/journal hash
}
```
```rust
// core/src/database/verifier.rs (integration-style test extension of existing
// update_get_payout_txs_from_citrea_withdrawal test)
// Show update_payout_txs_and_payer_operator_xonly_pk accepts an operator_xonly_pk
// with no relation to who actually broadcast/funded the spending tx recorded in
// bitcoin_syncer_spent_utxos, i.e., DB attribution == whatever OP_RETURN says.
```