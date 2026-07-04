### Title
Missing Class Hash Existence Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the caller-supplied `class_hash` corresponds to a previously declared contract class. The missing check is explicitly acknowledged by a TODO comment in the code. Because the OS accepts any arbitrary felt value as the new class hash, a contract can replace its own class with an undeclared hash, rendering itself permanently uncallable and freezing any funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function reads the requested `class_hash` directly from the syscall request and writes it into `contract_state_changes` without any validation that the hash corresponds to a declared class:

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
``` [1](#0-0) 

The OS-level enforcement that should exist here is the analog of the missing `liquidationInitialAsk` check in the external report: a permissionless parameter write (any contract can call `replace_class`) with no lower-bound or existence constraint on the new value.

When a subsequent call is made to the contract with the invalid class hash, `execute_entry_point` performs:

1. `dict_read{dict_ptr=contract_class_changes}(key=execution_context.class_hash)` — returns `0` for an undeclared hash.
2. `find_element(... key=compiled_class_hash)` — fails to locate a `CompiledClassFact` with hash `0`. [2](#0-1) 

`find_element` is a Cairo primitive that asserts the element exists; if it does not, the proof is invalid. The sequencer therefore cannot include any future transaction that calls the bricked contract, making the freeze permanent and provably enforced at the protocol level.

The external report's vulnerability class maps directly: in Astaria, `liquidationInitialAsk` is a critical loan parameter that goes unchecked during a permissionless refinance. Here, `class_hash` is a critical contract parameter that goes unchecked during a permissionless `replace_class` call. In both cases the missing validation allows an attacker to set the parameter to a value that causes irreversible harm.

---

### Impact Explanation

**Permanent freezing of funds (Critical).**

Any contract that holds user assets (ERC-20 balances, vault deposits, escrow funds) and exposes an upgrade path via `replace_class` can have its class replaced with an undeclared hash. Once replaced:

- Every subsequent call to the contract fails at the proof level.
- The sequencer cannot include those calls in any block.
- All assets stored in the contract's storage become permanently inaccessible.

There is no recovery path: the contract's state entry persists with the invalid class hash, and no syscall exists to revert a `replace_class` without executing the contract (which is now impossible).

---

### Likelihood Explanation

The likelihood is **high** given the current ecosystem:

1. `replace_class` is the standard upgrade mechanism for Cairo 1 contracts. Many deployed contracts expose an owner-callable upgrade function that passes the new class hash directly to `replace_class`.
2. If the upgrade function does not independently validate that the new hash is declared (which is a common omission, since developers reasonably expect the OS to enforce this), an attacker who can trigger the upgrade (e.g., via a governance exploit, a compromised owner key, or a bug in access control) can supply an undeclared hash.
3. A malicious contract author can also intentionally deploy a contract that accepts deposits and then calls `replace_class` with `0` or any arbitrary felt, rug-pulling depositors with no recourse.
4. The TODO comment at line 898 confirms the Astaria team is aware the check is absent and has deferred it, meaning the window of exposure is open until the fix is shipped. [3](#0-2) 

---

### Recommendation

Inside `execute_replace_class`, before writing the new class hash to `contract_state_changes`, verify that `class_hash` is present in `contract_class_changes` (i.e., it has been declared in the current or a prior block):

```cairo
// Verify the new class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the check already performed implicitly in `execute_entry_point` but makes it an explicit, enforced precondition of `replace_class` itself, preventing the invalid state from ever being written. [4](#0-3) 

---

### Proof of Concept

1. **Attacker deploys a vault contract** that accepts ERC-20 deposits from users and exposes an `upgrade(new_class_hash)` function that calls `replace_class(new_class_hash)` with no additional validation.
2. **Users deposit funds** into the vault. The vault's storage now holds balances.
3. **Attacker calls `upgrade(0)`** (or any felt that is not a declared class hash). The OS `execute_replace_class` handler accepts the call, writes `class_hash=0` into the vault's `StateEntry`, and returns success. [5](#0-4) 
4. **Any subsequent withdrawal attempt** causes `execute_entry_point` to call `dict_read` on `contract_class_changes` with key `0`, obtaining compiled class hash `0`, then `find_element` fails to locate a `CompiledClassFact` with hash `0`. [6](#0-5) 
5. **The proof is invalid**; the sequencer cannot include the withdrawal transaction. All deposited funds are permanently frozen with no recovery mechanism.

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
