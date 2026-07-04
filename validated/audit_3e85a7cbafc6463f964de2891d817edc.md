### Title
Missing Validation of Replacement Class Hash in `execute_replace_class` Allows Permanent Contract Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not validate that the replacement class hash supplied by a contract corresponds to a previously declared class. The OS unconditionally writes the caller-supplied hash into `contract_state_changes` without checking its existence in `contract_class_changes`. A contract that accepts an external class hash and calls `replace_class` can be permanently bricked by an unprivileged attacker, permanently freezing any funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function reads `request.class_hash` directly from the syscall segment and updates the contract's state entry without any validation:

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

The developer-acknowledged TODO comment confirms the missing check. [1](#0-0) 

When a subsequent call is made to the affected contract, `execute_entry_point` performs a `dict_read` on `contract_class_changes` using the (now invalid) class hash, then calls `find_element` to locate the compiled class. Since the hash was never declared, `dict_read` returns the default value (0), and `find_element` fails to locate a matching `CompiledClassFact`, causing every future call to the contract to fail permanently. [2](#0-1) 

The analog to the external report is direct: just as `EverclearBridge.sendMsg` passes `maxFee` and `ttl` to `newIntent` without enforcing they are zero (routing to an unsupported pathway), `execute_replace_class` passes `class_hash` to `dict_update` without enforcing it is a declared class (routing the contract to a non-existent implementation).

---

### Impact Explanation

Any contract that holds funds (ERC-20 balances, vault deposits, etc.) and exposes an upgrade path accepting an external class hash can have its class hash replaced with an undeclared value. After the state transition is proven and accepted on L1, every subsequent call to the contract reverts at the class-lookup stage. The funds are permanently inaccessible — **permanent freezing of funds**, which is within the allowed critical impact scope.

---

### Likelihood Explanation

The `replace_class` syscall is a standard StarkNet mechanism for contract upgrades. Many production contracts implement a public or role-gated `upgrade(new_class_hash: ClassHash)` entry point that calls `replace_class` with the caller-supplied hash. If the contract itself does not validate that `new_class_hash` is declared (a check the OS is supposed to enforce as the final arbiter), an attacker can supply an arbitrary undeclared hash. The OS, as shown, performs no such check. The attack requires only the ability to call a contract's upgrade function — no privileged access, no key compromise.

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, `execute_replace_class` must verify that the supplied `class_hash` exists as a key in `contract_class_changes` (i.e., has a non-zero compiled class hash entry). The check should mirror the lookup already performed in `execute_entry_point`:

```cairo
// Verify the replacement class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This closes the gap acknowledged by the existing TODO comment. [3](#0-2) 

---

### Proof of Concept

1. Attacker identifies an upgradeable contract `C` holding user funds, with a public `upgrade(new_class_hash)` entry point that calls `replace_class(new_class_hash)`.
2. Attacker submits a transaction calling `C.upgrade(0xdeadbeef)` where `0xdeadbeef` is an arbitrary, never-declared class hash.
3. The sequencer executes the transaction. The contract calls the `replace_class` syscall with `class_hash = 0xdeadbeef`.
4. `execute_replace_class` in the OS reads `request.class_hash = 0xdeadbeef`, skips any existence check (per the TODO), and writes `StateEntry(class_hash=0xdeadbeef, ...)` into `contract_state_changes`. [4](#0-3) 
5. The OS generates a valid proof for this state transition; L1 accepts it.
6. Any subsequent call to `C` reaches `execute_entry_point`, which does `dict_read(contract_class_changes, 0xdeadbeef)` → returns 0, then `find_element(..., key=0)` → not found → returns `is_reverted=1` with `ERROR_ENTRY_POINT_NOT_FOUND`. [5](#0-4) 
7. All calls to `C` permanently revert. Funds held in `C` are frozen forever.

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
