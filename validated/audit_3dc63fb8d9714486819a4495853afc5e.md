### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Contract Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS accepts an arbitrary `class_hash` from the caller without verifying that the hash corresponds to a previously declared contract class. A contract can replace its own class hash with any undeclared felt value. Any subsequent call to that contract will cause the OS to fail irrecoverably when attempting to look up the compiled class, permanently freezing all funds held by the contract.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function reads the requested class hash directly from the syscall request and writes it into `contract_state_changes` with no validation:

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
```

The developer-acknowledged TODO at line 898 confirms the missing check. [1](#0-0) 

When any subsequent call is made to the contract whose class hash was replaced with an undeclared value, `execute_entry_point` performs two lookups:

1. `dict_read` on `contract_class_changes` for the (undeclared) class hash — returns `0` (the dict default).
2. `find_element` in `compiled_class_facts_bundle` for compiled class hash `0` — **this is a hard Cairo assertion that panics if the element is not found**.

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

Because `find_element` is a Cairo primitive that asserts the key exists, passing an undeclared compiled class hash causes the OS execution to abort. The invalid class hash is committed to the global state trie after the block, making the contract permanently uncallable.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any contract that holds user assets (ERC-20 balances, vault deposits, etc.) and whose class hash is replaced with an undeclared value becomes permanently inaccessible. No withdrawal, transfer, or administrative function can ever be called again because every call path through `execute_entry_point` will fail at the `find_element` assertion. The funds are irrecoverably locked in the contract's storage.

---

### Likelihood Explanation

The attack surface is reachable by any unprivileged contract deployer or transaction sender:

1. An attacker deploys a contract that calls `replace_class(undeclared_hash)` — e.g., `replace_class(0x1)` where `0x1` has never been declared.
2. The OS accepts the syscall without validation and commits the invalid class hash to state.
3. Any future invocation of the contract (by the attacker, a victim, or a protocol) triggers the `find_element` failure.
4. If users have deposited funds into the contract (e.g., it is a token contract or a shared vault), those funds are permanently frozen.

A more sophisticated path: an attacker exploits a reentrancy or callback pattern in an existing DeFi protocol to force the protocol contract to call `replace_class` with an invalid hash, freezing all user deposits. The `replace_class` syscall is callable from within any contract execution, with no privileged-role requirement.

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, verify that it corresponds to a declared class by checking its presence in `contract_class_changes` (the class declaration dict). Specifically, assert that `dict_read{dict_ptr=contract_class_changes}(key=class_hash)` returns a non-zero compiled class hash. This mirrors the validation already performed implicitly in `execute_entry_point` and closes the gap identified by the TODO comment.

---

### Proof of Concept

1. Deploy a contract `VictimVault` that holds user ERC-20 deposits.
2. Craft a transaction that causes `VictimVault` to call the `replace_class` syscall with `class_hash = 0xDEAD` (never declared on-chain).
3. The OS executes `execute_replace_class`: no validation is performed; `contract_state_changes` is updated with `class_hash = 0xDEAD` for `VictimVault`'s address. [3](#0-2) 
4. The block is proven and the state is committed. `VictimVault`'s on-chain class hash is now `0xDEAD`.
5. Any user attempts to call `withdraw()` on `VictimVault`. The OS calls `execute_entry_point`:
   - `dict_read(contract_class_changes, key=0xDEAD)` → returns `0` (undeclared).
   - `find_element(compiled_class_facts, key=0)` → **assertion failure; OS aborts**. [4](#0-3) 
6. All funds in `VictimVault` are permanently frozen.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L154-167)
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
```
