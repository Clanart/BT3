### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Contract Freezing — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the new class hash supplied by the caller corresponds to a previously declared Sierra class. An unprivileged contract can call `replace_class` with an arbitrary, undeclared class hash. The OS accepts the state update unconditionally, committing an invalid class hash to the contract's on-chain state. Any subsequent transaction targeting that contract will cause the OS proof to be ungenerable, permanently freezing all funds held by the contract.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` reads the requested class hash directly from the syscall request and writes it into `contract_state_changes` without any check that the hash exists in `contract_class_changes` (the Sierra→CASM mapping):

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

The inline TODO comment explicitly acknowledges the missing guard. [1](#0-0) 

Once the state is committed with an undeclared class hash, any future call to that contract reaches `execute_entry_point`, which performs:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash   // ← undeclared hash → returns 0
);
...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    ...
    key=compiled_class_hash,           // ← key=0, not present → assertion failure
);
``` [2](#0-1) 

`find_element` is a hint-assisted Cairo primitive that **asserts** the element is present. When `compiled_class_hash` is 0 (the default dict value for an undeclared key), no matching compiled class exists, the assertion fails, and the prover cannot produce a valid proof for any block containing a transaction to that contract. The contract is permanently inaccessible.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

After a successful `replace_class` call with an undeclared hash:

1. The contract's class hash in the committed state is set to an undeclared value.
2. Every future transaction targeting the contract causes an unrecoverable Cairo assertion failure inside `execute_entry_point`.
3. The sequencer cannot include any such transaction in a provable block.
4. All ERC-20 balances, ETH, or other assets stored in the contract's storage are permanently inaccessible — there is no recovery path because the contract itself cannot be called to self-repair.

---

### Likelihood Explanation

**High.** The attack requires only:

1. Deploying any contract (standard `deploy_account` or `deploy` syscall — no privilege required).
2. Having that contract call the `replace_class` syscall with an arbitrary felt value that is not a declared Sierra class hash.

No key material, operator access, or special role is needed. The OS imposes no on-chain constraint on the new class hash value. The attacker controls the argument entirely. Any contract that exposes a public entry point calling `replace_class` (e.g., an upgradeable proxy with an unguarded upgrade function) is exploitable by any transaction sender.

---

### Recommendation

Inside `execute_replace_class`, before writing the new class hash to `contract_state_changes`, verify that the requested class hash exists in `contract_class_changes` (i.e., has a non-zero compiled class hash entry). Concretely:

```cairo
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the existing pattern used in `execute_entry_point` and closes the gap acknowledged by the TODO comment. [3](#0-2) 

---

### Proof of Concept

1. **Attacker deploys** `MaliciousVault` — a contract that holds user funds and exposes `freeze_self()`.
2. `freeze_self()` calls `replace_class(0xdeadbeef)` where `0xdeadbeef` is not a declared Sierra class hash.
3. The OS executes `execute_replace_class`: gas is deducted, `contract_state_changes[MaliciousVault.address].class_hash = 0xdeadbeef` is written, the revert log entry is appended, and the function returns successfully. [1](#0-0) 
4. The block is proven and the state root is updated on L1 with `MaliciousVault`'s class hash set to `0xdeadbeef`.
5. A victim later calls `MaliciousVault.withdraw()`. The OS enters `execute_entry_point`, calls `dict_read(contract_class_changes, 0xdeadbeef)` → returns `0`, then calls `find_element(..., key=0)` → no compiled class with hash 0 exists → Cairo assertion failure. [4](#0-3) 
6. The sequencer cannot produce a valid proof for any block containing this transaction. The victim's funds are permanently frozen.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L154-176)
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
```
