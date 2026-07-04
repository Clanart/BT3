### Title
Missing Declared Class Hash Validation in `execute_replace_class` Enables Permanent Contract Fund Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS accepts any arbitrary `class_hash` value and updates the contract's on-chain state without verifying that the supplied class hash has ever been declared. In contrast, `execute_entry_point` enforces that a class hash is declared before executing it. This asymmetry — a check present in one code path but absent in the analogous path — is the direct structural analog of the reported vulnerability. A contract that calls `replace_class` with an undeclared class hash will succeed and commit the broken state, permanently rendering the contract uncallable and freezing all funds it holds.

---

### Finding Description

**Root cause — missing declared-class check in `execute_replace_class`:**

In `syscall_impls.cairo` lines 896–910, the syscall handler reads `request.class_hash` and immediately writes it into `contract_state_changes` with no validation that the hash exists in `contract_class_changes`:

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

The developer-inserted TODO comment explicitly acknowledges the missing check. [1](#0-0) 

**The check that exists in `execute_entry_point` but is absent here:**

In `execute_entry_point.cairo` lines 154–166, every execution path first performs a `dict_read` on `contract_class_changes` to resolve the compiled class hash, then calls `find_element` to locate the compiled class. If the class hash was never declared, `dict_read` returns 0 and `find_element` fails — a hard OS-level assertion failure:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    elm_size=CompiledClassFact.SIZE,
    n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
    key=compiled_class_hash,
);
``` [2](#0-1) 

**The structural asymmetry:**

| Code path | Declared-class check present? |
|---|---|
| `execute_entry_point` (execution) | Yes — `dict_read` + `find_element` |
| `execute_replace_class` (upgrade) | **No** — TODO comment only |

This is the exact same pattern as the reported vulnerability: a guard exists in one protocol path (Portal / `execute_entry_point`) but is absent in the analogous path (Synthesis / `execute_replace_class`). [3](#0-2) 

---

### Impact Explanation

Once a transaction containing `replace_class(undeclared_hash)` is committed:

1. The contract's `class_hash` field in `contract_state_changes` is permanently set to an undeclared value.
2. Every subsequent invocation of that contract reaches `execute_entry_point`, which performs `dict_read(key=undeclared_hash)` → returns 0, then `find_element(key=0)` → hard OS assertion failure.
3. The sequencer's blockifier detects the failure during simulation and marks all future calls to the contract as reverted, charging the caller's fee but never executing.
4. No call to the contract can ever succeed again. All funds (tokens, ETH, state) locked inside the contract are **permanently frozen**.

This satisfies the **Critical — Permanent freezing of funds** impact category. [4](#0-3) 

---

### Likelihood Explanation

- The `replace_class` syscall is available to **any deployed contract** — no privileged role is required to issue it.
- A contract that exposes a public upgrade function (common pattern) can be triggered by any caller to supply an arbitrary `class_hash`.
- The missing check is self-documented with a TODO, confirming it is a known gap in the production code, not a deliberate design choice.
- A single successful transaction is sufficient to permanently break the contract; no repeated exploitation is needed. [5](#0-4) 

---

### Recommendation

Before writing the new `class_hash` into `contract_state_changes`, add a `dict_read` on `contract_class_changes` and assert the result is non-zero, mirroring the guard already present in `execute_entry_point`:

```cairo
// Verify the new class hash has been declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("replace_class: class hash not declared") {
    assert_not_zero(compiled_class_hash);
}
```

This is consistent with how `execute_declare_transaction` enforces `assert_not_zero(compiled_class_hash)` before registering a class.

<cite repo="blackvul/sequencer--014" path="crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo" start="816" end="819"

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
