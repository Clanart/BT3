### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Contract Bricking — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS accepts an arbitrary `class_hash` from the calling contract without verifying that the hash corresponds to a previously declared class. A contract can replace its own class with a non-existent class hash, permanently rendering itself unexecutable and freezing any funds it holds. A TODO comment in the code explicitly acknowledges this missing check.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function reads the requested `class_hash` directly from the syscall request and writes it into `contract_state_changes` without any validation that the hash is a declared class:

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

The TODO comment at line 898 explicitly acknowledges the missing check. The analogous check in `execute_entry_point` — which is the downstream consumer of the class hash — performs a `dict_read` on `contract_class_changes` for the stored class hash, then calls `find_element` to locate the compiled class fact. If the class hash is not declared, `dict_read` returns 0 (the default for an uninitialized dict entry), and `find_element` will panic or the sequencer will be unable to construct a valid proof for any block that includes a call to the bricked contract. [2](#0-1) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is set to an undeclared value via `replace_class`, every subsequent call to that contract will fail at the OS level because no compiled class fact exists for the invalid hash. The contract becomes permanently unexecutable. Any ERC-20 tokens, ETH, or other assets held in the contract's storage are irrecoverably frozen, as there is no entry point that can be invoked to transfer them out.

---

### Likelihood Explanation

The attack path is directly reachable by any unprivileged transaction sender:

1. Deploy a contract (or use an existing one) that accepts user deposits.
2. After accumulating funds, call `replace_class` with an arbitrary felt value that does not correspond to any declared class hash (e.g., `0x1`).
3. The OS writes the invalid class hash into `contract_state_changes` with no validation.
4. The contract is permanently bricked; all held funds are frozen.

This is a one-transaction, zero-privilege attack. No operator cooperation, leaked keys, or network-level access is required. The TODO comment confirms the team is aware the check is absent.

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, verify that the hash exists in `contract_class_changes` (i.e., it has been declared in the current or a prior block). Concretely, perform a `dict_read` on `contract_class_changes` for `request.class_hash` and assert the returned compiled class hash is non-zero:

```cairo
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(
    key=class_hash
);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the validation already performed implicitly in `execute_entry_point` and closes the gap acknowledged by the TODO.

---

### Proof of Concept

1. Declare class `A` (valid) and deploy contract `C` using class `A`. Fund `C` with tokens.
2. From within `C`, issue a `replace_class` syscall with `class_hash = 0xDEAD` (never declared).
3. The OS processes the syscall via `execute_replace_class`: no validation occurs; `contract_state_changes[C].class_hash` is set to `0xDEAD`.
4. In any subsequent block, attempt to call any entry point of `C`. The OS calls `execute_entry_point`, which does `dict_read(contract_class_changes, 0xDEAD)` → returns `0`. Then `find_element(..., key=0)` finds no compiled class fact → the sequencer cannot include any call to `C` in a provable block.
5. All funds in `C` are permanently frozen. [3](#0-2) [4](#0-3)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L154-177)
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
    let (success, compiled_class_entry_point: CompiledClassEntryPoint*) = get_entry_point(
        compiled_class=compiled_class, execution_context=execution_context
    );

    if (success == 0) {
        %{ ExitCall %}
        let (retdata: felt*) = alloc();
        assert retdata[0] = ERROR_ENTRY_POINT_NOT_FOUND;
        return (is_reverted=1, retdata_size=1, retdata=retdata);
    }
```
