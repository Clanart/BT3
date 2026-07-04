### Title
Missing Validation of New Class Hash in `execute_replace_class` Allows Permanent Contract Freezing - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS Cairo program does not validate that the caller-supplied `class_hash` corresponds to a previously declared contract class. An unprivileged contract can call `replace_class` with an arbitrary, undeclared class hash. The OS accepts and commits this state change without enforcement. Any future call to that contract will then cause the OS to fail to locate the compiled class, permanently rendering the contract uncallable and freezing any funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function reads `request.class_hash` directly from the syscall pointer (attacker-controlled) and writes it into `contract_state_changes` without verifying that the hash is present in `contract_class_changes` (i.e., that it was previously declared via a `DECLARE` transaction):

```cairo
let class_hash = request.class_hash;

// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
local state_entry: StateEntry*;
%{ GetContractAddressStateEntry %}

tempvar new_state_entry = new StateEntry(
    class_hash=class_hash, storage_ptr=state_entry.storage_ptr, nonce=state_entry.nonce
);
dict_update{dict_ptr=contract_state_changes}(...);
```

The `TODO` comment explicitly acknowledges the missing check. [1](#0-0) 

When any subsequent transaction attempts to call the affected contract, `execute_entry_point` performs:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash   // now the undeclared hash
);
...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    ...
    key=compiled_class_hash,           // resolves to 0 (default dict value)
);
```

`dict_read` on an undeclared key returns the default value `0`. `find_element` (unlike `search_sorted_optimistic`) panics if the key is absent, causing the entire OS execution to abort for any block that includes a call to the frozen contract. [2](#0-1) 

The `replace_class` syscall is dispatched unconditionally for any contract during execution: [3](#0-2) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is set to an undeclared value, the contract is permanently uncallable at the OS proof layer. Any ERC-20 token contract, multisig wallet, vault, or account contract that is targeted (or self-targets via a malicious upgrade path) will have all held assets permanently frozen. The sequencer cannot include any transaction calling the affected contract without causing the block proof to fail, so the freeze is irreversible without a protocol-level upgrade.

---

### Likelihood Explanation

**High.** The attack requires only deploying a contract that calls `replace_class` with an arbitrary felt value (e.g., `1` or any non-declared hash). No privileged access, leaked keys, or external dependencies are required. The syscall is available to every contract. The missing check is self-documented with a `TODO` comment, confirming it is a known gap in the OS enforcement layer. Any contract that implements an upgradeable pattern and is tricked into calling `replace_class` with a bad hash (e.g., via a social-engineering or reentrancy attack on the upgrade logic) is also vulnerable.

---

### Recommendation

In `execute_replace_class`, before writing the new class hash to `contract_state_changes`, verify that `class_hash` is present in `contract_class_changes` (i.e., it has a non-zero compiled class hash entry). This mirrors the check already performed implicitly during `execute_entry_point` for all other class lookups:

```cairo
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This resolves the `TODO` and closes the gap between the blockifier's off-chain validation and the OS's on-chain enforcement.

---

### Proof of Concept

1. Deploy contract `Malicious` with a function `freeze_self()` that calls the `replace_class` syscall with `class_hash = 0xdeadbeef` (an undeclared hash).
2. Send an `INVOKE` transaction calling `freeze_self()`.
3. The OS executes `execute_replace_class`: no validation is performed; `contract_state_changes` is updated with `class_hash = 0xdeadbeef`. [4](#0-3) 
4. The block is proven successfully (the replace itself is valid from the OS's perspective).
5. In a subsequent block, send any transaction calling `Malicious`.
6. `execute_entry_point` calls `dict_read(contract_class_changes, 0xdeadbeef)` → returns `0`. Then `find_element(..., key=0)` panics because no compiled class with hash `0` exists. [5](#0-4) 
7. The block containing that call cannot be proven. The sequencer must permanently exclude all calls to `Malicious`, freezing any funds it holds.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L878-915)
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L195-203)
```text
    if (selector == REPLACE_CLASS_SELECTOR) {
        execute_replace_class(contract_address=execution_context.execution_info.contract_address);
        %{ OsLoggerExitSyscall %}
        return execute_syscalls(
            block_context=block_context,
            execution_context=execution_context,
            syscall_ptr_end=syscall_ptr_end,
        );
    }
```
