### Title
Missing Class Hash Existence Check in `execute_replace_class` Allows Permanent Freezing of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

Both the Cairo 1 (`syscall_impls.cairo`) and deprecated (`deprecated_execute_syscalls.cairo`) implementations of `execute_replace_class` update a contract's class hash to an attacker-supplied value without verifying that the new class hash is actually declared in the `contract_class_changes` dictionary. This is the StarkNet OS analog of the EVM `safeTransfer` bug: just as a low-level EVM call silently succeeds against a non-existent contract, the OS silently accepts a `replace_class` to a non-existent class. The result is that the contract becomes permanently uncallable, freezing any funds it holds.

---

### Finding Description

In `execute_replace_class` (`syscall_impls.cairo`, lines 877–916), after gas deduction, the function reads the new `class_hash` directly from the syscall request and writes it into `contract_state_changes` with no check that the class hash is present in `contract_class_changes`:

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

The developer-inserted TODO comment at line 898 explicitly acknowledges the missing check.

The identical omission exists in the deprecated path at `deprecated_execute_syscalls.cairo` lines 307–329, which also writes the caller-supplied `class_hash` directly into state without any existence validation.

When any subsequent call is made to the affected contract, `execute_entry_point` performs:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
```

For an undeclared class hash, this returns `0` (the default uninitialized value). `find_element` is then called with `key=0`, which will fail to locate a valid `CompiledClassFact`, causing every future call to the contract to revert. The contract is permanently bricked.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any funds held by the affected contract — ERC20 token balances, ETH, STRK, or any other asset whose accounting is stored in the contract's storage — become permanently inaccessible. No future transaction can execute against the contract because every entry point lookup fails. The state commitment still records the contract's storage (including balances), but no code can ever run to move those assets.

---

### Likelihood Explanation

The attack surface is broad:

1. **Malicious contract owner:** Any contract that exposes `replace_class` (or whose logic can be manipulated to call it) can be weaponized. The owner calls `replace_class` with an arbitrary undeclared felt value. The OS accepts it, the block is proven, and the contract is bricked.
2. **Buggy contract:** A contract with a logic error that passes an incorrect class hash to `replace_class` achieves the same result unintentionally.
3. **No privileged role required:** `replace_class` is a standard syscall available to any Cairo 1 or deprecated contract. The attacker only needs to be the contract's own execution context — i.e., the contract calls the syscall on itself.

The TODO comment confirms the development team is aware of the gap and has not yet closed it.

---

### Recommendation

Before writing the new `class_hash` into `contract_state_changes`, verify that it is present in `contract_class_changes` (i.e., that it has been declared). In Cairo, this can be done by reading the entry and asserting it is non-zero:

```cairo
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
// Ensure the class is declared (compiled_class_hash != 0 means it exists).
assert_not_zero(compiled_class_hash);
```

This mirrors the fix suggested in the original EVM report: check existence before proceeding with the state-mutating operation.

---

### Proof of Concept

1. Attacker deploys `VaultContract` holding user funds (e.g., acts as a token vault). `VaultContract` is declared with a valid class hash `H_valid`.
2. Users deposit funds into `VaultContract`; balances are recorded in its storage.
3. Attacker (as the contract's logic) invokes the `replace_class` syscall with `class_hash = 0xdeadbeef` — an arbitrary felt that has never been declared.
4. The OS processes `execute_replace_class`: gas is deducted, `contract_state_changes[vault_address].class_hash` is updated to `0xdeadbeef`. No existence check is performed. The transaction succeeds and is included in a proven block.
5. Any user who now calls `VaultContract` triggers `execute_entry_point`, which reads `contract_class_changes[0xdeadbeef]` → returns `0`. `find_element` with `key=0` finds no compiled class. The call reverts with `ERROR_ENTRY_POINT_NOT_FOUND`.
6. All funds stored in `VaultContract` are permanently frozen; no withdrawal, transfer, or administrative function can ever execute.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo (L307-329)
```text
func execute_replace_class{contract_state_changes: DictAccess*, revert_log: RevertLogEntry*}(
    contract_address, syscall_ptr: ReplaceClass*
) {
    alloc_locals;
    let class_hash = syscall_ptr.class_hash;

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L153-166)
```text
    alloc_locals;
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
