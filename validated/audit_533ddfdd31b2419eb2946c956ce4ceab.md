### Title
Missing `class_hash` Validation in `execute_replace_class` Allows Permanent Contract Bricking - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not validate that the caller-supplied `class_hash` is non-zero or corresponds to a declared class. A contract can call `replace_class` with `class_hash = 0` or any undeclared felt value. The OS unconditionally writes this invalid hash into the contract's state entry. Any subsequent call to that contract will fail at the OS level when it attempts to look up the compiled class, permanently bricking the contract and freezing any funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` reads `class_hash` directly from the syscall request and writes it into `contract_state_changes` without any validation:

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
```

The inline TODO comment is a developer acknowledgment that this check is missing. The same defect exists in the deprecated path in `deprecated_execute_syscalls.cairo`:

```cairo
func execute_replace_class{contract_state_changes: DictAccess*, revert_log: RevertLogEntry*}(
    contract_address, syscall_ptr: ReplaceClass*
) {
    alloc_locals;
    let class_hash = syscall_ptr.class_hash;
    // No validation of class_hash whatsoever
    ...
    dict_update{dict_ptr=contract_state_changes}(...);
```

Missing checks (direct analog to M-02):
1. `class_hash == 0` — zero value accepted, no guard
2. `class_hash` not in declared classes — undeclared hash accepted, no guard

When a future call arrives at the bricked contract, the OS entry-point dispatch in `deprecated_execute_entry_point.cairo` calls `find_element` keyed on `execution_context.class_hash`. If that hash is 0 or undeclared, `find_element` cannot satisfy its soundness constraint and the OS proof fails, or the call reverts with `ERROR_ENTRY_POINT_NOT_FOUND` — either way the contract is permanently inaccessible.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any contract that holds ETH or ERC-20 balances and whose class hash is replaced with 0 or an undeclared value becomes permanently inaccessible. All assets stored in that contract's storage (token balances, NFT ownership, vault deposits) are frozen with no recovery path, because the OS will never be able to dispatch a call into the contract again.

---

### Likelihood Explanation

**Medium.**

The attacker-controlled entry path is straightforward:
1. Deploy a contract (or exploit a contract that exposes an unguarded `replace_class` call path).
2. Invoke the function that calls `replace_class(class_hash=0)` or `replace_class(class_hash=<undeclared felt>)`.
3. The OS writes the invalid hash into state with no rejection.
4. The contract is permanently bricked.

No privileged role is required. Any unprivileged contract deployer or transaction sender can trigger this against a contract they control. The risk extends to any contract that has a logic bug allowing an external caller to influence the argument to `replace_class`.

---

### Recommendation

Add explicit validation inside `execute_replace_class` (both the new and deprecated paths) before the `dict_update` call:

```cairo
// 1. Reject zero class hash.
assert_not_zero(class_hash);

// 2. Reject undeclared class hash.
// Verify class_hash exists in contract_class_changes (declared classes dict).
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

This mirrors the validation already present in `execute_declare_transaction` (`assert_not_zero(compiled_class_hash)`) and closes the gap acknowledged by the TODO comment.

---

### Proof of Concept

1. Deploy a Cairo 1 contract `Bricker` with the following logic:
   ```rust
   #[external]
   fn brick_self(ref self: ContractState) {
       starknet::replace_class_syscall(0_felt252.try_into().unwrap()).unwrap();
   }
   ```
2. Fund `Bricker` with ETH (e.g., via `transfer`).
3. Call `brick_self()` from any unprivileged account.
4. The OS executes `execute_replace_class` with `class_hash = 0`. No validation fires. `dict_update` writes `StateEntry { class_hash: 0, ... }` for `Bricker`'s address.
5. Attempt any subsequent call to `Bricker`. The OS dispatches to `execute_entry_point`, calls `find_element` with key `0`, finds no compiled class, and the call fails with `ERROR_ENTRY_POINT_NOT_FOUND` (or proof failure).
6. The ETH held by `Bricker` is permanently frozen.

**Affected files:** [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_entry_point.cairo (L119-136)
```text
    let (compiled_class_fact: DeprecatedCompiledClassFact*) = find_element(
        array_ptr=compiled_class_facts_bundle.deprecated_compiled_class_facts,
        elm_size=DeprecatedCompiledClassFact.SIZE,
        n_elms=compiled_class_facts_bundle.n_deprecated_compiled_class_facts,
        key=execution_context.class_hash,
    );
    local compiled_class: DeprecatedCompiledClass* = compiled_class_fact.compiled_class;

    let (success, entry_point_offset) = get_entry_point_offset(
        compiled_class=compiled_class, execution_context=execution_context
    );

    if (success == 0) {
        %{ ExitCall %}
        let (retdata: felt*) = alloc();
        assert retdata[0] = ERROR_ENTRY_POINT_NOT_FOUND;
        return (is_reverted=1, retdata_size=1, retdata=retdata);
    }
```
