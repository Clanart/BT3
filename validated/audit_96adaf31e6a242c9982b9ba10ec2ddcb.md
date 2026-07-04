### Title
Missing Class Hash Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS does not validate that the new class hash provided by a contract corresponds to a previously declared contract class. This missing validation — explicitly acknowledged by a TODO comment in the code — allows a contract to commit an invalid class hash to the on-chain state, rendering the contract permanently uncallable and freezing any funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function processes the `replace_class` syscall by directly updating the contract's class hash in the state without verifying that the new class hash corresponds to a declared contract class.

The code contains an explicit TODO comment acknowledging this missing check:

```cairo
// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
``` [1](#0-0) 

The function reads the new class hash from the request and immediately commits it to the state without any existence check:

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
``` [2](#0-1) 

When a subsequent transaction attempts to call this contract, `execute_entry_point.cairo` looks up the compiled class hash for the contract's (now invalid) class hash via `dict_read`:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
``` [3](#0-2) 

Since the class hash was never declared, `dict_read` returns `0` (the default value for an uninitialized dict entry). The subsequent `find_element` call then searches for a compiled class fact with hash `0`:

```cairo
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    elm_size=CompiledClassFact.SIZE,
    n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
    key=compiled_class_hash,
);
``` [4](#0-3) 

Since `0` is not a valid compiled class hash in the facts bundle, `find_element` fails, making the contract permanently uncallable. The state is already committed with the invalid class hash, so there is no recovery path.

---

### Impact Explanation

Any contract that calls `replace_class` with an undeclared class hash will have its class permanently set to an invalid value in the committed on-chain state. The contract becomes permanently uncallable. Any funds — STRK, ETH, or ERC20 tokens — held by the contract are permanently frozen with no recovery mechanism.

**Impact: Critical — Permanent freezing of funds.**

---

### Likelihood Explanation

The likelihood is **medium**. The attack requires a contract to call `replace_class` with an invalid class hash. Realistic paths include:

1. A malicious contract deliberately calling `replace_class` with an invalid hash to grief a shared contract (e.g., a multi-sig or DAO vault) and freeze its funds.
2. A buggy contract accidentally calling `replace_class` with an invalid hash (e.g., passing an uninitialized variable).
3. An attacker exploiting a reentrancy or logic flaw in a legitimate contract to trigger `replace_class` with an attacker-controlled invalid hash.

The explicit TODO comment in the production OS code confirms the developers are aware of this missing check, meaning it is a known unprotected surface that has not yet been closed.

---

### Recommendation

Add validation inside `execute_replace_class` to verify that the new `class_hash` exists in `contract_class_changes` (i.e., has a non-zero compiled class hash entry). If the class hash is not declared, the syscall must write a failure response and return without updating the state, consistent with how other invalid-argument cases are handled (e.g., `write_failure_response(remaining_gas=remaining_gas, failure_felt=ERROR_INVALID_ARGUMENT)`). [5](#0-4) 

---

### Proof of Concept

1. Deploy a contract `C` that holds STRK tokens and exposes a function that calls the `replace_class` syscall with an arbitrary undeclared class hash (e.g., `0xdeadbeef`).
2. Call that function. The OS processes `execute_replace_class` without any class hash existence check and commits `class_hash = 0xdeadbeef` to the state for contract `C`.
3. Attempt any subsequent call to contract `C`. The OS executes `execute_entry_point`, performs `dict_read` on `contract_class_changes` for key `0xdeadbeef`, receives `0` (undeclared), and `find_element` fails to locate a compiled class fact with hash `0`.
4. Contract `C` is permanently uncallable. All STRK tokens held by `C` are permanently frozen.

The analog to the CSP report is direct: just as the CSP report identifies missing directives (e.g., `default-src`, `base-uri`) that leave areas of the browser extension unprotected, the StarkNet OS is missing a validation directive in `execute_replace_class` that leaves the class-replacement surface unprotected — allowing an attacker-controlled input (`class_hash`) to commit an invalid state transition with no fallback check.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L154-156)
```text
    let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
        key=execution_context.class_hash
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L161-166)
```text
    let (compiled_class_fact: CompiledClassFact*) = find_element(
        array_ptr=compiled_class_facts_bundle.compiled_class_facts,
        elm_size=CompiledClassFact.SIZE,
        n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
        key=compiled_class_hash,
    );
```
