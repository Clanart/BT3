### Title
Missing Declared Class Validation in `execute_replace_class` Enables Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS does not verify that the new class hash supplied by a contract corresponds to a previously declared contract class. An unprivileged actor can deploy a contract that calls `replace_class` with an arbitrary, undeclared felt value, permanently rendering the contract uncallable and freezing any funds it holds.

---

### Finding Description

In `execute_replace_class`, the OS reads `class_hash` directly from the syscall request and writes it into `contract_state_changes` with no validation against `contract_class_changes`. The developer TODO at line 898 explicitly acknowledges the missing check: [1](#0-0) 

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

After the class hash is committed to state as an undeclared value, any subsequent call to that contract reaches `execute_entry_point`, which performs: [2](#0-1) 

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
```

Because the class hash is undeclared, `dict_read` returns `0`, and `find_element` cannot locate a matching `CompiledClassFact`. The contract becomes permanently uncallable — there is no on-chain recovery path.

---

### Impact Explanation

Any ERC-20 tokens, ETH, or other assets held by a contract whose class hash has been replaced with an undeclared value are permanently frozen. No entry point can be reached to withdraw or transfer them. This matches **Critical — Permanent freezing of funds**.

---

### Likelihood Explanation

The attack requires no privileged access:

1. An attacker deploys a contract that appears legitimate (e.g., a vault or token contract) and accepts user deposits.
2. The contract internally calls `replace_class` with an arbitrary undeclared felt (e.g., `1`, `0xdeadbeef`, or any value absent from `contract_class_changes`).
3. The OS accepts the syscall without validation and commits the invalid class hash to state.
4. All deposited funds are permanently frozen.

The attacker controls the contract code and can time the `replace_class` call after accumulating victim deposits. No operator cooperation or key compromise is required.

---

### Recommendation

Before updating the contract's class hash, verify that the new hash is present in `contract_class_changes` (i.e., it has been declared):

```cairo
// Verify the new class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
if (compiled_class_hash == 0) {
    write_failure_response(remaining_gas=remaining_gas, failure_felt=ERROR_INVALID_ARGUMENT);
    return ();
}
```

This mirrors the validation already performed implicitly in `execute_entry_point` and closes the gap identified by the TODO.

---

### Proof of Concept

1. Attacker deploys contract `V` (a vault) that accepts deposits and exposes a backdoor function that calls `replace_class(class_hash=0x1)`.
2. Users deposit funds into `V`.
3. Attacker calls the backdoor; `execute_replace_class` writes `class_hash=0x1` into `contract_state_changes` for `V` — no validation occurs.
4. Any subsequent call to `V` (withdraw, transfer, etc.) reaches `execute_entry_point`:
   - `dict_read(key=0x1)` → returns `0` (not declared).
   - `find_element(..., key=0)` → fails to find a compiled class fact.
5. All calls to `V` are permanently broken; all deposited funds are frozen with no recovery path.

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
