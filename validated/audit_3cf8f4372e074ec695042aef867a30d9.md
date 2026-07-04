### Title
Missing Class Hash Validation in `execute_replace_class` Enables OS Panic via Undeclared Class Hash - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not validate that the caller-supplied `class_hash` corresponds to a declared contract class. A developer-acknowledged TODO comment at line 898 explicitly marks this check as missing. An unprivileged contract can call `replace_class` with an arbitrary undeclared hash, then immediately call itself recursively. The blockifier includes the outer transaction as reverted; the OS still processes all syscalls of reverted entry points and calls `execute_entry_point` for the recursive call. Inside `execute_entry_point`, `find_element` is called with the resulting compiled-class-hash of 0 (the dict default for an undeclared key). `find_element` panics on a missing key, aborting the OS program and making the block unprovable.

---

### Finding Description

In `execute_replace_class` (`syscall_impls.cairo`, lines 878–916), the new class hash is taken directly from the syscall request and written into `contract_state_changes` with no check that it exists in the declared-class set:

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

The identical omission exists in the deprecated path: [2](#0-1) 

When the contract is subsequently called (e.g., via a recursive `call_contract` in the same transaction), `execute_entry_point` reads the now-invalid class hash from state and performs:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash   // = undeclared hash H
);
// compiled_class_hash == 0  (dict default for undeclared key)

let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    elm_size=CompiledClassFact.SIZE,
    n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
    key=compiled_class_hash,           // = 0 → not found → PANIC
);
``` [3](#0-2) 

`find_element` is a Cairo standard-library function that raises an assertion failure when the key is absent. This aborts the OS program entirely.

Critically, `call_execute_syscalls` is invoked **regardless** of whether the outer entry point reverted:

```cairo
// If necessary, create a new revert_log and dummy outputs before calling
// `call_execute_syscalls`.
if (is_reverted != FALSE) {
    ...
    let revert_log = init_revert_log();
} else {
    ...
}
let builtin_ptrs = return_builtin_ptrs;
with syscall_ptr {
    call_execute_syscalls(   // ← always reached
        block_context=block_context,
        execution_context=execution_context,
        syscall_ptr_end=entry_point_return_values.syscall_ptr,
    );
}
``` [4](#0-3) 

So even when the blockifier marks the transaction as reverted and includes it in the block, the OS still processes every syscall recorded in the syscall segment, including the recursive `call_contract` that triggers the panic.

---

### Impact Explanation

The OS program abort makes the block unprovable. No subsequent block can be produced until the issue is resolved, constituting a **total network shutdown** (High impact: Network not being able to confirm new transactions).

---

### Likelihood Explanation

Any unprivileged transaction sender can:
1. Deploy a contract whose logic calls `replace_class(0)` (or any felt not in the declared-class set) followed by a `call_contract` back to itself.
2. Submit the transaction. The blockifier includes it as reverted (the recursive call fails at the class-lookup layer). The OS then processes the block, hits the recursive `call_contract` syscall, and panics.

No privileged role, leaked key, or operator cooperation is required. The missing validation is explicitly flagged by the developers themselves in the TODO comment.

---

### Recommendation

Inside `execute_replace_class`, before writing the new class hash to state, verify that the hash exists in `contract_class_changes` (i.e., that a class with that hash has been declared):

```cairo
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the pattern already used in `execute_entry_point` and closes the gap identified by the TODO comment.

---

### Proof of Concept

1. Attacker deploys `MaliciousContract` with entry point `__execute__`:
   ```
   replace_class(class_hash=0)          // undeclared hash; OS accepts it
   call_contract(contract_address=self, selector=__execute__, ...)
   ```
2. Attacker submits an invoke transaction targeting `MaliciousContract.__execute__`.
3. Blockifier simulation: `replace_class(0)` succeeds; recursive `call_contract` fails (class 0 unknown); transaction is marked **reverted** and included in the block.
4. OS proves the block: processes the reverted transaction's syscall segment; reaches the `call_contract` syscall; calls `execute_call_contract` → `contract_call_helper` → `execute_entry_point`.
5. Inside `execute_entry_point`: `dict_read(contract_class_changes, key=0)` → `compiled_class_hash = 0`; `find_element(..., key=0)` → **assertion failure / OS panic**.
6. Block proof fails. Network halts.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-910)
```text
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L288-307)
```text
    if (is_reverted != FALSE) {
        // Create a dummy OsCarriedOutputs so that messages to L1 will be discarded.
        // The dummy is initialized with
        // OsCarriedOutputs(messages_to_l1="empty segment", messages_to_l2=0).
        %{ GenerateDummyOsOutputSegment %}
        // Create a new revert log for the reverted entry point. This will be used to revert the
        // entry point changes after calling `call_execute_syscalls`.
        let revert_log = init_revert_log();
    } else {
        assert outputs = orig_outputs;
        tempvar revert_log = orig_revert_log;
    }
    let builtin_ptrs = return_builtin_ptrs;
    with syscall_ptr {
        call_execute_syscalls(
            block_context=block_context,
            execution_context=execution_context,
            syscall_ptr_end=entry_point_return_values.syscall_ptr,
        );
    }
```
