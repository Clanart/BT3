### Title
Front-Running `execute_declare_transaction` With Arbitrary `compiled_class_hash` Permanently Poisons a Class Hash Slot — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

In `execute_declare_transaction`, the OS enforces that a given `class_hash` can only be declared once by using `dict_update` with `prev_value=0`. The `class_hash` itself is cryptographically verified against the Sierra class content, but the `compiled_class_hash` (the CASM commitment) is accepted from the caller with only a non-zero check. An unprivileged attacker who observes a pending declare transaction can front-run it with the same Sierra class (same `class_hash`) but an arbitrary bogus `compiled_class_hash`, permanently occupying the slot. The victim's transaction then reverts, and the class hash slot is forever poisoned with an unusable CASM commitment.

---

### Finding Description

In `execute_declare_transaction`, the Sierra `class_hash` is verified to be the correct hash of the submitted class components via `finalize_class_hash`: [1](#0-0) 

However, the `compiled_class_hash` (the on-chain commitment to the CASM) is accepted with only a non-zero assertion before being written into the class changes dictionary: [2](#0-1) 

The `dict_update` call with `prev_value=0` is the sole uniqueness guard — it enforces that a class may be declared only once. Because `compiled_class_hash` is never verified against the actual CASM in the OS, an attacker can:

1. Observe a victim's pending declare transaction carrying `(class_hash=H, compiled_class_hash=C_correct)`.
2. Copy the identical Sierra class (so `finalize_class_hash` still produces `H`).
3. Submit a competing declare transaction with `(class_hash=H, compiled_class_hash=C_bogus)` and a higher fee.
4. The attacker's transaction is sequenced first; the slot is written as `H → C_bogus`.
5. The victim's transaction hits `dict_update` with `prev_value=0` but the actual previous value is now `C_bogus`, causing an assertion failure and a revert.

The class hash slot `H` is now permanently occupied by an invalid CASM commitment. No valid CASM can ever match `C_bogus` (assuming hash collision resistance), so the class is permanently unusable.

The same one-time-write guard exists in the bootstrap fast-path: [3](#0-2) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once class `H` is poisoned:

- Any contract that subsequently calls the `replace_class` syscall with `H` (e.g., as part of an upgrade flow) will have its class hash replaced with an unusable commitment. Every future call into that contract will fail because no valid CASM can be produced to match `C_bogus`. All funds held by that contract are permanently frozen.
- The original declarer cannot re-declare `H` with the correct `compiled_class_hash`; the `prev_value=0` guard permanently blocks it.
- The attack is repeatable: every time the victim generates a new Sierra class and attempts to declare it, the attacker can front-run again.

---

### Likelihood Explanation

**Medium.** StarkNet's sequencer is currently centralized and orders transactions by fee priority. An attacker who monitors the transaction pool and outbids the victim on gas can reliably have their poisoning transaction sequenced first. The attacker needs only to copy the Sierra class bytes from the victim's transaction (publicly visible in the mempool) and substitute an arbitrary non-zero `compiled_class_hash`. No cryptographic capability is required.

---

### Recommendation

1. **Verify `compiled_class_hash` in the OS.** Require the prover/hint system to supply the CASM and verify `hash(CASM) == compiled_class_hash` before writing the slot. This eliminates the ability to store an arbitrary commitment.
2. **Alternatively, bind the slot key to the declarer.** Use `hash(sender_address, class_hash)` as the dictionary key instead of bare `class_hash`, analogous to the mitigation suggested in the external report (`keccak256(abi.encode(msg.sender, merkleRoot))`). This ensures different declarers cannot collide on the same slot.
3. **Allow re-declaration with the correct hash.** If the stored `compiled_class_hash` is later proven invalid (e.g., no valid proof can be generated), permit an authorized re-declaration. This is a weaker mitigation but reduces the permanence of the DoS.

---

### Proof of Concept

```
1. Victim broadcasts:
     declare(sierra_class=SC, compiled_class_hash=C_correct)
     → class_hash H = finalize_class_hash(SC)

2. Attacker observes the mempool, extracts SC, submits with higher fee:
     declare(sierra_class=SC, compiled_class_hash=0xDEAD)
     → same H (finalize_class_hash is deterministic)

3. Attacker's tx is sequenced first:
     dict_update(key=H, prev_value=0, new_value=0xDEAD)  ✓

4. Victim's tx is sequenced next:
     dict_update(key=H, prev_value=0, new_value=C_correct)
     → FAILS: actual prev_value is 0xDEAD, not 0  → tx reverts

5. Slot H is now permanently bound to 0xDEAD.
   Victim cannot re-declare H. Any contract calling replace_class(H)
   becomes permanently inoperable, freezing all held funds.
``` [4](#0-3)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L738-743)
```text
        let expected_class_hash = finalize_class_hash(
            contract_class_component_hashes=contract_class_component_hashes
        );
        with_attr error_message("Invalid class hash pre-image.") {
            assert [class_hash_ptr] = expected_class_hash;
        }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L764-775)
```text
    if (sender_address == 'BOOTSTRAP' and tx_info.nonce == 0 and tx_info.version == 3) {
        let max_possible_fee = compute_max_possible_fee(tx_info=tx_info);
        if (max_possible_fee == 0) {
            // Declare the class hash and skip the rest of the transaction.
            // Note that prev_value=0 enforces that a class may be declared only once.
            assert_not_zero(compiled_class_hash);
            dict_update{dict_ptr=contract_class_changes}(
                key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
            );
            %{ SkipTx %}
            return ();
        }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
