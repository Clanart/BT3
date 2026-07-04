### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Contract Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS accepts any arbitrary class hash from the caller without verifying that the new class hash has been declared in `contract_class_changes`. This is directly analogous to the Bond protocol bug where `pushOwnership` transferred ownership to an address without checking the required whitelist invariant. Here, the OS performs a state transition (replacing a contract's class hash) without enforcing the invariant that the new class must be a known, declared class. The missing check is explicitly acknowledged by a TODO comment in the code.

---

### Finding Description

In `execute_replace_class` (lines 877–916 of `syscall_impls.cairo`), the OS reads the requested class hash directly from the syscall request and writes it into `contract_state_changes` with no validation:

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

The TODO comment at line 898 explicitly acknowledges the missing check. The OS is the authoritative proving layer; if it does not enforce this invariant, a state transition with an undeclared class hash can be committed to L1.

When any subsequent call is made to the affected contract, `execute_entry_point` performs:

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

If `class_hash` is undeclared, `dict_read` returns 0 (`UNINITIALIZED_CLASS_HASH`), and `find_element` fails with an assertion error because no compiled class with hash 0 exists in the bundle. This causes the OS to fail to produce a valid proof for any block containing a call to the affected contract.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is replaced with an undeclared hash and the state is committed to L1 (via a valid OS proof), the contract is permanently broken. No future call to it can be proven by the OS. Any ERC-20 tokens, ETH, or other assets held in the contract's storage become permanently inaccessible. The state commitment is irreversible once accepted on L1.

Additionally, if the broken contract is called in a subsequent block, the OS fails to prove that block, which is a **network halt** scenario.

---

### Likelihood Explanation

The `replace_class` syscall is callable by any unprivileged contract. An unprivileged user can deploy a contract that calls `replace_class` with an arbitrary felt value that does not correspond to any declared class. The OS's missing validation means it will accept this state transition and commit it. The likelihood depends on whether the sequencer's blockifier independently validates the class hash before including the transaction — the TODO comment in the OS code implies this check was deferred to the OS layer, suggesting the sequencer may not perform it either. If the sequencer also lacks this check, the attack is directly reachable by any transaction sender.

---

### Recommendation

Add the missing validation in `execute_replace_class` before updating `contract_state_changes`. Specifically, verify that the requested `class_hash` has a non-zero entry in `contract_class_changes` (i.e., it has been declared):

```cairo
func execute_replace_class{...}(contract_address: felt) {
    let class_hash = request.class_hash;

+   // Verify that the new class hash is a declared class.
+   let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
+   with_attr error_message("Class hash is not declared.") {
+       assert_not_zero(compiled_class_hash);
+   }

    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}
    ...
}
```

This mirrors the fix recommended in the external report: validate the required invariant (new class must be declared) before committing the state transition, just as the Bond fix required validating the new owner is whitelisted before transferring ownership.

---

### Proof of Concept

1. Unprivileged user deploys `AttackerContract` with a function `break_self()` that calls `replace_class(0xdeadbeef)` where `0xdeadbeef` is not a declared class hash.
2. User submits a transaction calling `break_self()`.
3. The sequencer executes the transaction. `execute_replace_class` in the OS accepts the call, updating `contract_state_changes[attacker_address].class_hash = 0xdeadbeef`.
4. The OS generates a valid proof for the block (no validation of the class hash occurs at line 898).
5. The proof is submitted to L1 and accepted. The state root now encodes `attacker_address → class_hash=0xdeadbeef`.
6. In the next block, any call to `attacker_address` causes `execute_entry_point` to call `dict_read(contract_class_changes, 0xdeadbeef)` → returns 0, then `find_element(..., key=0)` → assertion failure. The OS cannot prove the block.
7. All funds in `attacker_address` are permanently frozen; the network halts if the broken contract is included in future blocks. [3](#0-2) [4](#0-3)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L153-177)
```text
    alloc_locals;
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
