### Title
Unvalidated Single-Step Class Hash Replacement Permanently Freezes Contract Funds — (`execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS allows any contract to atomically replace its own class hash with an **arbitrary, undeclared value** in a single irreversible step. The OS contains an explicit TODO acknowledging the missing validation. Because the class hash update is committed to state immediately and there is no two-step confirmation or existence check, a contract that calls `replace_class` with an undeclared hash becomes permanently unexecutable, freezing all funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` reads the requested new class hash directly from the syscall request and writes it into `contract_state_changes` without verifying that the hash corresponds to any previously declared class: [1](#0-0) 

The critical gap is at line 898:

```cairo
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
```

The `class_hash` value comes directly from `request.class_hash` (attacker-controlled calldata) and is committed to state in one step with no existence check.

When any future transaction calls this contract, `execute_entry_point` performs:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
``` [2](#0-1) 

If `class_hash` was never declared, `dict_read` returns 0, and `find_element` is called with key `0`. If no compiled class with hash `0` exists in the bundle, `find_element` panics, making the contract permanently unexecutable at the OS level.

The state update is irreversible: there is no recovery path, no pending-class mechanism, and no two-step confirmation analogous to a `pendingOwner` pattern.

---

### Impact Explanation

**Critical — Permanent Freezing of Funds.**

Any contract holding token balances (ERC-20, ERC-721, native STRK, etc.) that calls `replace_class` with an undeclared class hash will have its class hash permanently set to an invalid value. The contract can never be executed again. All funds stored in its storage are permanently frozen with no recovery mechanism.

---

### Likelihood Explanation

- The `replace_class` syscall is available to every contract with no OS-level access control.
- A malicious contract can accept user deposits and then call `replace_class(arbitrary_undeclared_hash)` to freeze them.
- A buggy contract (e.g., one that reads the new class hash from user-supplied calldata without validating it) can be exploited by any unprivileged transaction sender.
- The TODO comment at line 898 confirms the development team is aware the validation is absent.

---

### Recommendation

Before committing the class hash update to `contract_state_changes`, verify that the requested `class_hash` exists in `contract_class_changes` (i.e., has been declared in the current or a prior block). Concretely:

```cairo
// Verify the new class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("replace_class: class hash not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the two-step ownership-transfer pattern: a class must first be declared (step 1) before it can be adopted via `replace_class` (step 2), preventing a single-step irreversible transition to an invalid state.

---

### Proof of Concept

1. Attacker deploys `VaultContract` (accepts STRK deposits, exposes `backdoor(new_hash: felt)` that calls `replace_class(new_hash)`).
2. Users deposit funds; `VaultContract` accumulates a balance.
3. Attacker sends an invoke transaction calling `backdoor(0xdeadbeef)` where `0xdeadbeef` is never declared.
4. `execute_replace_class` writes `class_hash=0xdeadbeef` into `contract_state_changes` with no validation.
5. In any subsequent block, a call to `VaultContract` reaches `execute_entry_point`:
   - `dict_read(contract_class_changes, 0xdeadbeef)` → returns `0` (never declared).
   - `find_element(..., key=0)` → panics (no compiled class with hash `0`).
6. The sequencer must permanently exclude all transactions targeting `VaultContract`.
7. All deposited funds are permanently frozen. [3](#0-2) [4](#0-3)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L154-167)
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
    local compiled_class: CompiledClass* = compiled_class_fact.compiled_class;
```
