### Title
Missing Class Hash Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS does not validate that the new class hash supplied by the caller corresponds to a previously declared contract class. An unprivileged contract can call `replace_class` with an arbitrary, undeclared hash, permanently committing an invalid class hash to the contract's on-chain state. Any funds held in that contract are then permanently frozen because the contract can never be executed again.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function processes the `replace_class` syscall and immediately writes the caller-supplied class hash into `contract_state_changes` with no validation that the hash is declared:

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

The TODO comment at line 898 explicitly acknowledges the missing check. The function accepts any `felt` value as the new class hash and commits it to state.

Once this invalid class hash is committed, any future execution of the contract reaches `execute_entry_point`, which performs:

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
``` [2](#0-1) 

`dict_read` on `contract_class_changes` returns `0` for an undeclared hash (the dict default). `find_element` then panics when `0` is not found in the compiled class facts bundle, making the contract permanently non-provable and non-callable. The sequencer must reject all future calls to the contract, permanently freezing any funds it holds.

This is the direct analog of the reported `claim` bug: just as `claim` lacked validation against `coverageMap` to confirm the amount was covered, `execute_replace_class` lacks validation against `contract_class_changes` (or the compiled class facts bundle) to confirm the new class hash is declared.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any ERC-20 balance, ETH, or other asset held in a contract whose class is replaced with an undeclared hash becomes permanently inaccessible. The contract's state entry persists on-chain with the invalid class hash; no future block can execute the contract, and no recovery path exists within the protocol.

---

### Likelihood Explanation

The attack requires only that an unprivileged user deploy a contract and invoke `replace_class` with an arbitrary felt value. No privileged role, leaked key, or external dependency is needed. The syscall is available to any executing contract. The attack is one transaction, deterministic, and irreversible.

---

### Recommendation

Before writing the new class hash to `contract_state_changes`, validate that `request.class_hash` is present in `contract_class_changes` (i.e., it was declared in a prior or current block). Concretely, perform a `dict_read` on `contract_class_changes` with `request.class_hash` as the key and assert the returned compiled class hash is non-zero before proceeding with the state update. This mirrors the lookup already performed in `execute_entry_point` and closes the gap.

---

### Proof of Concept

1. Attacker deploys contract `C` with a valid declared class hash `H_valid`.
2. Contract `C` executes a call to the `replace_class` syscall supplying `H_invalid` — any felt value that has never been declared (e.g., `0xdeadbeef`).
3. `execute_replace_class` in `syscall_impls.cairo` (lines 878–916) passes the gas check, reads the current state entry, and writes `new StateEntry(class_hash=H_invalid, ...)` into `contract_state_changes` with no further validation.
4. The transaction succeeds; the block is proven and finalized. `C`'s on-chain class hash is now `H_invalid`.
5. In any subsequent block, a call to `C` causes `execute_entry_point` to call `dict_read(contract_class_changes, H_invalid)` → returns `0`, then `find_element(..., key=0)` → panics (element not found). The sequencer cannot include any transaction calling `C`.
6. All funds in `C` are permanently frozen. [3](#0-2) [4](#0-3)

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
