### Title
Missing Declared-Class Existence Check in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS accepts any arbitrary `class_hash` value from the caller without verifying that the hash corresponds to a previously declared class. This is the direct analog of the reported "contract existence check" vulnerability class: just as the EVM's low-level `call` silently succeeds against a non-existent contract, the OS silently commits a state update pointing a contract at a non-existent (undeclared) class. Any contract whose class is replaced with an undeclared hash becomes permanently uncallable, freezing all funds it holds.

---

### Finding Description

In `execute_replace_class`, the OS reads the requested `class_hash` from the syscall request and immediately writes it into `contract_state_changes` with no validation that the hash is present in `contract_class_changes` (i.e., that it was ever declared):

```cairo
let class_hash = request.class_hash;

// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
local state_entry: StateEntry*;
%{ GetContractAddressStateEntry %}

tempvar new_state_entry = new StateEntry(
    class_hash=class_hash, storage_ptr=state_entry.storage_ptr, nonce=state_entry.nonce
);

dict_update{dict_ptr=contract_state_changes}(
    key=contract_address,
    prev_value=cast(state_entry, felt),
    new_value=cast(new_state_entry, felt),
);
``` [1](#0-0) 

The TODO comment at line 898 explicitly acknowledges the missing check. By contrast, `execute_declare_transaction` enforces that a class can only be declared once and that `compiled_class_hash` is non-zero, making `contract_class_changes` the authoritative registry of declared classes: [2](#0-1) 

When a subsequent call targets the contract whose class was replaced with an undeclared hash, `execute_entry_point` performs:

1. `dict_read{dict_ptr=contract_class_changes}(key=undeclared_class_hash)` — returns `0` (no entry exists for the undeclared hash).
2. `find_element(..., key=0)` — panics if no compiled class with hash `0` is present in `compiled_class_facts_bundle`. [3](#0-2) 

The sequencer's blockifier detects this during simulation and reverts any transaction that calls the broken contract. The contract's storage (and any funds it holds) remains committed on-chain but is permanently inaccessible.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is replaced with an undeclared value, the contract is permanently uncallable. All tokens, ETH, or STRK held in the contract's storage are irreversibly frozen. No upgrade, migration, or recovery path exists because the very mechanism for calling the contract (class lookup) is broken.

---

### Likelihood Explanation

**Medium.** The attack requires a malicious contract that:
1. Accepts user deposits (e.g., presents itself as a vault, pool, or escrow).
2. Exposes a function that calls `replace_class` with an arbitrary, undeclared hash.

No privileged role, leaked key, or external dependency is required. Any contract deployer can craft this. The `replace_class` syscall is a standard, user-accessible syscall reachable from any Cairo 1 contract. The attacker controls the timing: they wait until sufficient funds are deposited, then trigger the freeze.

---

### Recommendation

In `execute_replace_class`, before writing the new class hash into `contract_state_changes`, verify that the requested `class_hash` has a non-zero entry in `contract_class_changes`:

```cairo
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
// Revert if the class has not been declared.
if (compiled_class_hash == 0) {
    write_failure_response(remaining_gas=remaining_gas, failure_felt=ERROR_INVALID_ARGUMENT);
    return ();
}
```

This mirrors the check already enforced during `execute_declare_transaction` and closes the gap acknowledged by the existing TODO comment.

---

### Proof of Concept

1. **Attacker deploys** `MaliciousVault`, a contract that:
   - Accepts `deposit()` calls, recording balances in storage.
   - Exposes `freeze()` which calls `replace_class(0xdeadbeef...)` where `0xdeadbeef...` is a felt that has never been passed to a `declare` transaction.

2. **Users deposit funds** into `MaliciousVault`. The contract's storage accumulates balances.

3. **Attacker calls `freeze()`**. The OS executes `execute_replace_class`:
   - `request.class_hash = 0xdeadbeef...` (undeclared).
   - No existence check is performed (line 898 TODO).
   - `contract_state_changes[vault_address].class_hash` is updated to `0xdeadbeef...`.
   - The transaction succeeds and the state is committed.

4. **Any subsequent call to `MaliciousVault`** (e.g., `withdraw()`):
   - `execute_entry_point` reads `contract_class_changes[0xdeadbeef...]` → returns `0`.
   - `find_element(..., key=0)` finds no compiled class → OS panics / blockifier reverts.
   - The transaction is reverted. Funds remain in storage but are permanently inaccessible. [4](#0-3)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L877-916)
```text
// Replaces the class.
func execute_replace_class{
    range_check_ptr,
    syscall_ptr: felt*,
    contract_state_changes: DictAccess*,
    revert_log: RevertLogEntry*,
}(contract_address: felt) {
    alloc_locals;
    let request = cast(syscall_ptr + RequestHeader.SIZE, ReplaceClassRequest*);

    // Reduce gas.
    let success = reduce_syscall_gas_and_write_response_header(
        total_gas_cost=REPLACE_CLASS_GAS_COST, request_struct_size=ReplaceClassRequest.SIZE
    );
    if (success == FALSE) {
        // Not enough gas to execute the syscall.
        return ();
    }

    let class_hash = request.class_hash;

    // TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}

    tempvar new_state_entry = new StateEntry(
        class_hash=class_hash, storage_ptr=state_entry.storage_ptr, nonce=state_entry.nonce
    );

    dict_update{dict_ptr=contract_state_changes}(
        key=contract_address,
        prev_value=cast(state_entry, felt),
        new_value=cast(new_state_entry, felt),
    );

    assert [revert_log] = RevertLogEntry(selector=CHANGE_CLASS_ENTRY, value=state_entry.class_hash);
    let revert_log = &revert_log[1];

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L154-166)
```text
    let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
        key=execution_context.class_hash
    );

    // The key must be at offset 0.
    static_assert CompiledClassFact.hash == 0;
    let compiled_class_facts_bundle = block_context.os_global_context.compiled_class_facts_bundle;
    let (compiled_class_fact: CompiledClassFact*) = find_element(
        array_ptr=compiled_class_facts_bundle.compiled_class_facts,
        elm_size=CompiledClassFact.SIZE,
        n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
        key=compiled_class_hash,
    );
```
