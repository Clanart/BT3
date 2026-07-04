### Title
Missing Existence Check in `execute_replace_class` Allows Setting Undeclared Class Hash - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in `syscall_impls.cairo` updates a contract's class hash to any caller-supplied value without verifying that the new class hash corresponds to a declared class in `contract_class_changes`. An explicit `TODO` comment in the code acknowledges this missing check. This is a direct analog to the external report's pattern: a resource (class hash) is set without confirming the referenced entity (the class) actually exists. A subsequent call to the affected contract causes `execute_entry_point` to look up a compiled class for an undeclared hash, which fails hard inside the Cairo proof, halting proof generation for the block.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function processes the `replace_class` syscall:

```cairo
func execute_replace_class{...}(contract_address: felt) {
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
``` [1](#0-0) 

The `class_hash` from `request.class_hash` is written directly into `contract_state_changes` with no check that `class_hash` exists as a key in `contract_class_changes` (i.e., that it was ever declared). The `TODO` comment at line 898 explicitly acknowledges this gap.

When a subsequent transaction calls the contract whose class hash was replaced with an undeclared value, `execute_entry_point` runs:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash   // undeclared hash → returns 0
);
...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    elm_size=CompiledClassFact.SIZE,
    n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
    key=compiled_class_hash,           // key = 0, not present → hard failure
);
``` [2](#0-1) 

`dict_read` on `contract_class_changes` returns `0` for any key that was never declared. `find_element` is a Cairo primitive that **asserts** the element exists; if no compiled class with hash `0` is present in the bundle, the assertion fails and the OS proof cannot be generated for that block.

---

### Impact Explanation

- **Impact**: High — Network not being able to confirm new transactions (total network shutdown).
- A single transaction calling `replace_class` with an undeclared class hash poisons the contract's state entry. Any subsequent transaction in the same or a future block that calls that contract causes the OS proof to fail at `find_element`, making it impossible to finalize the block. The network cannot advance past that block.

---

### Likelihood Explanation

- **Likelihood**: Medium.
- Any unprivileged user can deploy a contract that calls `replace_class(arbitrary_undeclared_hash)`. The OS-level check is entirely absent (confirmed by the `TODO` comment). Whether the blockifier (Rust layer) independently enforces this check is a separate question; the OS Cairo code — which is the authoritative proof layer — does not. If the blockifier also lacks the check (or is bypassed), the attack is directly reachable.

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, verify that `class_hash` is present in `contract_class_changes` (i.e., it has a non-zero compiled class hash entry). This mirrors the recommendation in the external report: add an existence flag/check before accepting the resource reference.

```cairo
// Proposed fix inside execute_replace_class:
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This ensures that only previously declared classes can be used as replacement targets, eliminating the undefined-behavior path in `execute_entry_point`.

---

### Proof of Concept

1. **Attacker deploys** a contract `C` whose constructor or any entry point calls `replace_class(0xdeadbeef)`, where `0xdeadbeef` is not a declared class hash.
2. **Attacker sends** an invoke transaction targeting `C`'s entry point. The OS processes `execute_replace_class`:
   - `request.class_hash = 0xdeadbeef`
   - No existence check is performed (line 898 TODO).
   - `contract_state_changes[C].class_hash` is set to `0xdeadbeef`.
3. **In the same block or a subsequent block**, any transaction calls contract `C` (e.g., the attacker sends a second invoke, or another user interacts with `C`).
4. **`execute_entry_point`** is invoked with `execution_context.class_hash = 0xdeadbeef`:
   - `dict_read(contract_class_changes, 0xdeadbeef)` → returns `0` (never declared).
   - `find_element(..., key=0)` → **hard assertion failure** — no compiled class with hash `0` exists.
5. **The OS proof fails**. The block cannot be finalized. The network halts. [3](#0-2) [4](#0-3)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L142-167)
```text
func execute_entry_point{
    range_check_ptr,
    remaining_gas: felt,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    revert_log: RevertLogEntry*,
    outputs: OsCarriedOutputs*,
}(block_context: BlockContext*, execution_context: ExecutionContext*) -> (
    is_reverted: felt, retdata_size: felt, retdata: felt*
) {
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
    local compiled_class: CompiledClass* = compiled_class_fact.compiled_class;
```
