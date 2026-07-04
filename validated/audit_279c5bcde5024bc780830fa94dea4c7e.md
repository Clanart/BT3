### Title
Missing Class Hash Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS does not validate that the new class hash supplied via the `replace_class` syscall is actually a declared class. Any contract can call `replace_class` with an arbitrary, undeclared class hash, and the OS will commit the state change without verification. This is the direct analog of the reported access-control bug: a state-modifying function lacks a required guard, allowing an unprivileged caller to corrupt critical protocol state.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function processes the `replace_class` syscall:

```cairo
func execute_replace_class{
    range_check_ptr,
    syscall_ptr: felt*,
    contract_state_changes: DictAccess*,
    revert_log: RevertLogEntry*,
}(contract_address: felt) {
    alloc_locals;
    let request = cast(syscall_ptr + RequestHeader.SIZE, ReplaceClassRequest*);
    ...
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
    ...
}
```

The TODO comment at line 898 explicitly acknowledges the missing guard. The function unconditionally writes `class_hash` (attacker-controlled) into `contract_state_changes` for the calling contract. No check is performed against `contract_class_changes` to confirm the hash was ever declared.

The same omission exists in the deprecated path in `deprecated_execute_syscalls.cairo`:

```cairo
func execute_replace_class{contract_state_changes: DictAccess*, revert_log: RevertLogEntry*}(
    contract_address, syscall_ptr: ReplaceClass*
) {
    ...
    let class_hash = syscall_ptr.class_hash;
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}
    tempvar new_state_entry = new StateEntry(
        class_hash=class_hash, storage_ptr=state_entry.storage_ptr, nonce=state_entry.nonce
    );
    dict_update{dict_ptr=contract_state_changes}(...);
    ...
}
```

When a subsequent call to the affected contract is processed by `execute_entry_point`, the OS performs:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    ...
    key=compiled_class_hash,
);
```

`dict_read` on an undeclared class hash returns `0` (the Cairo dict default). `find_element` with key `0` panics if `0` is not present in the compiled class facts bundle, causing the entire OS execution to abort — the block cannot be proven.

---

### Impact Explanation

**Permanent freezing of funds (Critical):** A contract that holds user funds (e.g., a vault, escrow, or token contract) can have its class hash replaced with an undeclared value. All subsequent calls to that contract will fail at the OS level. Funds stored in the contract's storage become permanently inaccessible because no entry point can be executed.

**Network halt (High):** If a block includes a transaction that calls `replace_class` with an undeclared hash and a subsequent call to the same contract in the same block, the OS's `find_element` panics, making the block unprovable. A targeted attacker can craft such a sequence to halt block finalization.

---

### Likelihood Explanation

The `replace_class` syscall is reachable by any deployed contract — no privileged role is required. A contract deployer (unprivileged) can deploy a contract whose code calls `replace_class(arbitrary_undeclared_hash)`. The OS Cairo code contains no constraint preventing this. The explicit TODO comment confirms the check is absent by design (deferred), not accidentally omitted. The identical gap in both the new (`syscall_impls.cairo`) and deprecated (`deprecated_execute_syscalls.cairo`) syscall handlers doubles the attack surface.

---

### Recommendation

In `execute_replace_class` (both `syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`), add a validation step that reads `contract_class_changes` to confirm the requested `class_hash` has a non-zero compiled class hash entry before committing the state update. If the class hash is not declared, write a failure response instead of updating state. This mirrors the guard already present for `execute_deploy` (which reads the class hash from the compiled class facts bundle).

---

### Proof of Concept

1. Attacker deploys contract `M` whose `__execute__` function issues `replace_class(0xDEAD)`, where `0xDEAD` is never declared.
2. Attacker (or victim) sends funds to `M` and calls it.
3. `M.__execute__` runs; the OS dispatches `execute_replace_class(M_address)` with `class_hash = 0xDEAD`.
4. The OS writes `contract_state_changes[M].class_hash = 0xDEAD` with no validation.
5. State is committed. `M` now has class hash `0xDEAD` on-chain.
6. Any future call to `M` causes `execute_entry_point` to call `dict_read(contract_class_changes, 0xDEAD)` → returns `0` → `find_element(..., key=0)` panics → OS execution aborts → block is unprovable.
7. Funds in `M`'s storage are permanently frozen. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L878-916)
```text
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
