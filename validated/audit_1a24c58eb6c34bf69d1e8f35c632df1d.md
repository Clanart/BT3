### Title
Missing Class Hash Existence Validation in `replace_class` Syscall Enables Permanent Fund Freezing - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS accepts any arbitrary felt value as a new class hash without verifying that the hash corresponds to a declared class. A malicious contract can exploit this to permanently freeze all funds it holds by replacing its class with an undeclared hash, making the contract permanently uncallable.

---

### Finding Description

In `execute_replace_class`, the OS writes the caller-supplied `class_hash` directly into `contract_state_changes` with no validation that the hash exists in `contract_class_changes` (the declared class registry). The code even contains an explicit TODO acknowledging this missing check:

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

After the `replace_class` call succeeds and is committed to state, any subsequent call to the contract triggers `execute_entry_point`, which performs:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
// ...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    elm_size=CompiledClassFact.SIZE,
    n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
    key=compiled_class_hash,
);
``` [2](#0-1) 

Since the undeclared class hash has no entry in `contract_class_changes`, `dict_read` returns `0`. `find_element` (unlike `search_sorted_optimistic`) **asserts** the element exists and panics if it does not, making the entire block proof invalid. The sequencer cannot include any call to this contract in a provable block. The contract — and all funds it holds — are permanently frozen.

The `replace_class` syscall is callable by any contract from its own execution context, with no privileged role required.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

A malicious contract accepts user token deposits, then calls `replace_class` with an arbitrary undeclared felt value. After the block containing `replace_class` is proven and finalized:

- The contract's class hash in the global state tree is set to an undeclared value.
- Every future call to the contract causes `find_element` to panic inside the OS, making any block containing such a call unprovable.
- The sequencer is forced to exclude all calls to the contract.
- All funds held by the contract are permanently inaccessible — no withdrawal, no recovery.

This matches the Critical impact category: **Permanent freezing of funds**.

---

### Likelihood Explanation

The attack path is fully reachable by an unprivileged contract deployer:

1. Deploy a contract that accepts ERC-20 or native token deposits.
2. Advertise the contract to users; collect deposits.
3. From within the contract's execution, issue a `replace_class` syscall with an arbitrary undeclared felt (e.g., `0xdeadbeef`).
4. The `replace_class` succeeds and is committed to state in the proven block.
5. All deposited funds are permanently frozen.

No privileged role, leaked key, or operator cooperation is required. The `replace_class` syscall is a standard user-accessible syscall.

---

### Recommendation

Before writing the new class hash to `contract_state_changes`, the OS must verify that the hash exists in `contract_class_changes` (i.e., it was previously declared). Concretely, in `execute_replace_class`, add a `dict_read` on `contract_class_changes` for `class_hash` and assert the returned `compiled_class_hash` is non-zero:

```cairo
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the check already performed implicitly in `execute_entry_point` but enforces it at the point of replacement, preventing the invalid state from ever being committed. [3](#0-2) 

---

### Proof of Concept

1. Attacker deploys `VaultContract` with a `deposit()` entry point and a `rug()` entry point.
2. Users call `deposit()`, transferring tokens into `VaultContract`.
3. Attacker calls `rug()`, which internally issues:
   ```
   replace_class(class_hash=0xdeadbeef)  // undeclared hash
   ```
4. The OS processes `execute_replace_class`: no validation is performed; `contract_state_changes` is updated with `class_hash=0xdeadbeef`.
5. Block is proven and finalized on L1. `VaultContract`'s state now has `class_hash=0xdeadbeef`.
6. Any user attempts `withdraw()` on `VaultContract`. The sequencer simulates the call: `execute_entry_point` reads `compiled_class_hash = dict_read(contract_class_changes, 0xdeadbeef) = 0`, then `find_element(..., key=0)` panics. The block containing this call is unprovable.
7. The sequencer permanently excludes all calls to `VaultContract`. All deposited funds are frozen forever. [4](#0-3) [5](#0-4)

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
