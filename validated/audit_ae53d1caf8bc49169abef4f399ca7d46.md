### Title
Missing Class Existence Validation in `execute_replace_class` Allows Permanent Freezing of Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS accepts any arbitrary class hash from the caller without verifying that the hash corresponds to a declared (existing) contract class. A contract owner can invoke `replace_class` with a non-existent class hash, permanently setting their contract's class to an undiscoverable value. Any subsequent call to that contract will fail at the OS execution level, making all funds held by the contract permanently inaccessible.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function reads the new class hash directly from the syscall request and writes it into the contract state without any existence check:

```cairo
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

The developer TODO at line 898 explicitly acknowledges the missing guard: `"Check that there is a declared contract class with the given hash."` The `class_hash` value originates from `request.class_hash`, which is fully attacker-controlled. No cross-reference against `compiled_class_facts` or `deprecated_compiled_class_facts` (the OS's authoritative class registries, populated in `get_os_global_context`) is performed before committing the state update. [2](#0-1) 

The OS global context that holds the valid class registries is built in `os.cairo`: [3](#0-2) 

These registries are never consulted inside `execute_replace_class`.

---

### Impact Explanation

Once a contract's `class_hash` is set to a non-existent value and committed to the global state root, every future transaction that attempts to call or interact with that contract will fail at the OS entry-point dispatch level (the OS cannot locate the class bytecode). Because the `replace_class` syscall itself succeeds and is committed, there is no revert path. Any ERC-20 tokens, ETH, or other assets held in the contract's storage become permanently inaccessible.

This maps directly to the **Critical — Permanent freezing of funds** impact category.

---

### Likelihood Explanation

The attack surface is broad:

1. Any deployed contract whose owner (or any account with `__execute__` access) can call a function that internally invokes `replace_class` is vulnerable.
2. The syscall is a standard, documented StarkNet syscall available to all Cairo 1 contracts — no special privilege is required beyond being the executing contract.
3. The attack can be executed in a single transaction with no preconditions beyond contract deployment.
4. The missing check is acknowledged in the source as a TODO, confirming it is not an intentional design decision.

---

### Recommendation

Before committing the state update in `execute_replace_class`, verify that `class_hash` is present in either `compiled_class_facts` (Sierra/CASM classes) or `deprecated_compiled_class_facts` (Cairo 0 classes) held in the `OsGlobalContext`. Concretely, perform a lookup analogous to the one done in `validate_compiled_class_facts_post_execution` before writing the new `StateEntry`. If the class hash is not found, write a failure response and return without updating state.

---

### Proof of Concept

1. **Deploy** a contract `Vault` that holds user funds and exposes a function `nuke()` that calls `replace_class(0xdeadbeef)` — a hash that has never been declared.
2. **Users deposit** tokens into `Vault`.
3. **Owner calls** `nuke()`. The OS executes `execute_replace_class` with `class_hash = 0xdeadbeef`. No existence check is performed; the state update `StateEntry(class_hash=0xdeadbeef, ...)` is written to `contract_state_changes` and committed to the global state root.
4. **Any subsequent call** to `Vault` (withdraw, transfer, etc.) causes the OS to attempt to look up class `0xdeadbeef` in `compiled_class_facts` / `deprecated_compiled_class_facts`. The lookup fails; the transaction reverts.
5. **All funds** in `Vault` are permanently frozen with no recovery path, since even a `replace_class` recovery call cannot execute (the contract's entry point is unreachable). [4](#0-3)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os.cairo (L286-291)
```text
    let (n_compiled_class_facts, compiled_class_facts, builtin_costs) = guess_compiled_class_facts(
        );
    let (
        n_deprecated_compiled_class_facts, deprecated_compiled_class_facts
    ) = deprecated_load_compiled_class_facts();

```
