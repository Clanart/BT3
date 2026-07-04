### Title
Missing Existence Check on `replace_class` Allows Permanent Contract Freezing via Undeclared Class Hash - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS does not verify that the new class hash supplied by a contract actually corresponds to a declared, compiled class. This is the direct analog of the Loihi assimilator bug: a lookup is performed on a mapping (here, `contract_class_changes`) without first confirming the key is initialized. A contract can call `replace_class` with an arbitrary or zero class hash, permanently committing an undeclared class hash to its state entry. Any subsequent call to that contract will fail irrecoverably at the OS level, permanently freezing any funds held by the contract.

---

### Finding Description

In `execute_replace_class`, the OS reads the requested class hash directly from the syscall request and writes it into the contract's `StateEntry` without any check that the hash corresponds to a declared compiled class:

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
```

The developer-inserted TODO comment explicitly acknowledges this missing check. [1](#0-0) 

Once the undeclared class hash is committed to state, any future call to that contract reaches `execute_entry_point`, which performs:

1. `dict_read` on `contract_class_changes` with the undeclared class hash → returns `0` (default uninitialized value).
2. `find_element` on `compiled_class_facts_bundle` with `key=0` → fails/panics if no compiled class with hash `0` exists. [2](#0-1) 

This is structurally identical to the Loihi bug: a lookup on an uninitialized/zero mapping entry is used as a valid reference without a prior existence check. In Loihi, the result was a delegate call to the zero address. Here, the result is an irrecoverable OS-level failure for any block that includes a call to the affected contract.

---

### Impact Explanation

**Critical. Permanent freezing of funds.**

Once a contract's class hash is replaced with an undeclared hash and the block is finalized:
- The state commitment records the invalid class hash permanently.
- Every future transaction calling that contract causes `find_element` to fail at the OS level.
- The contract becomes permanently uncallable.
- Any ERC-20 tokens, ETH, or other assets held by the contract are irretrievably locked.

There is no recovery path: the OS has no mechanism to revert a committed state entry, and the contract can no longer execute any function (including any self-rescue logic).

---

### Likelihood Explanation

**Medium.**

The `replace_class` syscall is callable by any deployed contract without any privileged role. An attacker can:
1. Deploy a contract that holds victim funds (e.g., a token vault or escrow).
2. Have that contract call `replace_class` with a class hash of `0` or any non-existent felt value.
3. The OS accepts the syscall, commits the state, and the contract is permanently frozen.

This is reachable by any unprivileged transaction sender who can deploy or interact with a contract that invokes `replace_class`. The missing check is not gated by any access control in the OS.

---

### Recommendation

Add an existence check inside `execute_replace_class` to verify that `request.class_hash` is present in `contract_class_changes` (i.e., it has been declared in the current block) or in the pre-existing class trie before committing the update. Concretely, perform a `dict_read` on `contract_class_changes` with `key=class_hash` and assert the result is non-zero, analogous to the Loihi fix of `require(shell.assimilators[<TOKEN_ADDRESS>].ix != 0)`. [3](#0-2) 

---

### Proof of Concept

1. Deploy contract `A` holding user funds.
2. Contract `A` calls the `replace_class` syscall with `class_hash = 0` (or any felt not present in `compiled_class_facts_bundle`).
3. `execute_replace_class` in the OS writes `StateEntry(class_hash=0, ...)` for contract `A` into `contract_state_changes` with no validation. [1](#0-0) 
4. The block is proven and finalized; the state trie now records `class_hash=0` for contract `A`.
5. In any subsequent block, a transaction calls contract `A`.
6. `execute_entry_point` calls `dict_read{dict_ptr=contract_class_changes}(key=0)` → returns `0`. [4](#0-3) 
7. `find_element` is called with `key=0` on `compiled_class_facts_bundle`; no such entry exists → OS execution fails. [5](#0-4) 
8. Contract `A` is permanently uncallable; all funds are frozen.

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
