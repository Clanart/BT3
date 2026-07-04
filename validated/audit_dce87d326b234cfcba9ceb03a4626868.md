### Title
Missing Validation of `class_hash` in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS does not verify that the caller-supplied `class_hash` corresponds to a previously declared class. An attacker can deploy a contract, collect user funds, and then invoke `replace_class` with an arbitrary undeclared class hash. The OS commits this invalid class hash to the contract state tree. Any subsequent call to that contract will fail irrecoverably inside `execute_entry_point` because the OS cannot resolve the undeclared hash to a compiled class, permanently freezing all funds held by the contract.

---

### Finding Description

In `execute_replace_class`, the new `class_hash` is read directly from the syscall request and written into `contract_state_changes` without any check that the hash exists in `contract_class_changes` (i.e., has been declared via a `DECLARE` transaction): [1](#0-0) 

The code itself contains an explicit acknowledgment of the missing check:

```cairo
// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
``` [2](#0-1) 

After the invalid class hash is committed, any future call to the contract reaches `execute_entry_point`, which performs:

1. `dict_read` on `contract_class_changes` keyed by the invalid class hash → returns `0` (uninitialized default).
2. `find_element` on `compiled_class_facts_bundle` keyed by `0` → no matching compiled class exists, causing an irrecoverable assertion failure. [3](#0-2) 

The contract is permanently non-callable. No proof can ever be generated for a block that successfully calls it, so all funds it holds are permanently frozen.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any contract that calls `replace_class` with an undeclared class hash becomes permanently non-callable. All ERC-20 balances, ETH, or other assets stored in that contract's storage are irrecoverably locked. The state commitment is updated with the invalid hash, and no future block can include a successful call to the contract.

---

### Likelihood Explanation

The `replace_class` syscall is available to any deployed contract with no privilege requirement. A realistic attack path:

1. Attacker deploys a contract that appears to be a legitimate yield farm, DEX, or vault.
2. Users deposit funds.
3. Attacker calls any entry point that internally invokes `replace_class` with an arbitrary undeclared felt (e.g., `0x1`).
4. The OS writes the invalid class hash to `contract_state_changes` and commits it to the state tree.
5. All subsequent calls to the contract fail inside `execute_entry_point`; user funds are permanently frozen.

No privileged role, leaked key, or external dependency is required. The attacker only needs to deploy a contract and call a function.

---

### Recommendation

Inside `execute_replace_class`, before updating `contract_state_changes`, verify that the requested `class_hash` has a non-zero entry in `contract_class_changes` (i.e., it has been declared):

```cairo
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the validation already performed implicitly during `execute_entry_point` and enforces the protocol invariant that a contract's class hash must always correspond to a declared, compiled class. [4](#0-3) 

---

### Proof of Concept

1. Attacker declares and deploys `MaliciousVault` — a contract with a `steal()` entry point that calls `replace_class(class_hash=0x1)`.
2. Users call `deposit()` on `MaliciousVault`, transferring tokens into its storage.
3. Attacker calls `steal()`. The OS executes `execute_replace_class`:
   - `request.class_hash = 0x1` (undeclared).
   - No validation is performed (the TODO check is absent).
   - `contract_state_changes` is updated: `MaliciousVault.class_hash = 0x1`.
4. The block is proven and the state root is updated.
5. Any future `call_contract` to `MaliciousVault` enters `execute_entry_point`:
   - `dict_read(contract_class_changes, key=0x1)` → returns `0`.
   - `find_element(compiled_class_facts, key=0)` → element not found → assertion failure.
6. No valid proof can ever include a successful call to `MaliciousVault`. All deposited funds are permanently frozen. [5](#0-4) [6](#0-5)

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
