### Title
Missing Validation of New Class Hash in `execute_replace_class` Allows Permanent Freezing of Contract Funds - (File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo)

---

### Summary
The `execute_replace_class` syscall handler in the StarkNet OS accepts any arbitrary felt as a new class hash without verifying it corresponds to a declared contract class. This is the direct analog of the reported bug: a critical field in a state-mutating handler is accepted without validation. Any contract can invoke `replace_class` with an undeclared hash, permanently corrupting its own class hash in state and making the contract — and all funds it holds — permanently inaccessible.

---

### Finding Description

In `execute_replace_class`, the OS reads `request.class_hash` and unconditionally writes it into the contract's `StateEntry` via `dict_update`:

```cairo
// execute_replace_class (syscall_impls.cairo ~line 896-913)
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

The TODO comment at line 898 explicitly acknowledges the missing check. No validation is performed that `class_hash` exists in `contract_class_changes` (i.e., was declared in this or any prior block). [1](#0-0) 

Once the invalid class hash is committed to state, every subsequent call to that contract reaches `execute_entry_point`, which does:

```cairo
// execute_entry_point.cairo ~line 154-166
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash   // <-- the invalid hash
);
// compiled_class_hash == 0 (default for unknown key)

let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    elm_size=CompiledClassFact.SIZE,
    n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
    key=compiled_class_hash,           // <-- key=0, not found → hint fails
);
``` [2](#0-1) 

`

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-913)
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

    assert [revert_log] = RevertLogEntry(selector=CHANGE_CLASS_ENTRY, value=state_entry.class_hash);
    let revert_log = &revert_log[1];
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
