### Title
Missing Class Hash Existence Validation in `replace_class` Syscall Enables Permanent Contract Freezing - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary
The `execute_replace_class` function in the StarkNet OS accepts any arbitrary class hash in the `replace_class` syscall without verifying that the hash corresponds to a previously declared class. This is explicitly acknowledged by a TODO comment in the code. An attacker can exploit this to permanently set a contract's class hash to an undeclared value, making all future calls to that contract fail at the OS level and permanently freezing any funds held by the contract.

---

### Finding Description

In `execute_replace_class` (`syscall_impls.cairo`, lines 877–916), the OS reads the requested class hash directly from the syscall request and updates the contract's `StateEntry` without any validation that the new class hash has been declared:

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

The TODO at line 898 explicitly acknowledges the missing check. [2](#0-1) 

When any entry point is subsequently called on the affected contract, `execute_entry_point` performs two lookups:

1. `dict_read{dict_ptr=contract_class_changes}(key=execution_context.class_hash)` — returns `0` (the default uninitialized value) for any undeclared class hash.
2. `find_element(array_ptr=compiled_class_facts_bundle.compiled_class_facts, ..., key=compiled_class_hash)` — **fails with an assertion error** if no compiled class with hash `0` (or the undeclared hash) exists in the bundle. [3](#0-2) 

Once the contract's class hash is set to an undeclared value, every future call to that contract fails at the OS level. There is no recovery path: the contract cannot execute any entry point — including any withdrawal or rescue function — so all funds held by the contract are permanently frozen.

**Analog to the external report:** Just as `extcodesize` returns `0` during contract construction (allowing bypass of the EOA check in LifeBuoy), the OS's `replace_class` handler accepts any class hash without checking whether it is declared, allowing a contract to permanently invalidate its own class reference. In both cases, a validation that is supposed to gate a critical state change is either absent or bypassable.

---

### Impact Explanation

Any contract that calls `replace_class` with an undeclared class hash will have its class permanently set to an invalid value. All subsequent calls to the contract fail inside `execute_entry_point` when `find_element` cannot locate the compiled class. Funds (ERC20 tokens, ETH, STRK) held by the contract are permanently frozen with no recovery path.

**Impact: Critical — Permanent freezing of funds.** [4](#0-3) 

---

### Likelihood Explanation

The attack is reachable by any unprivileged contract deployer or any user who can call a contract that exposes an upgrade function:

- A contract with a public or insufficiently access-controlled `upgrade(class_hash: felt)` function that internally calls `replace_class` can be exploited by any attacker passing an undeclared class hash.
- The OS provides **no last-line-of-defense validation**, so even contracts that intend to validate the class hash before calling `replace_class` are not protected if their own validation is flawed.
- An attacker can monitor the mempool for contracts holding significant funds and attempt to trigger `replace_class` with an invalid hash via any exposed upgrade path. [5](#0-4) 

---

### Recommendation

Implement the missing validation noted in the TODO: before updating the contract's `StateEntry`, assert that the new `class_hash` has a non-zero entry in `contract_class_changes` (i.e., it has been declared). Concretely, add a `dict_read` on `contract_class_changes` keyed by `class_hash` and assert the result is non-zero before proceeding with the `dict_update`. [1](#0-0) 

---

### Proof of Concept

1. Attacker deploys **Contract A** containing a public `upgrade(class_hash: felt)` function that calls `replace_class(class_hash)`.
2. Users deposit funds into Contract A (e.g., via an ERC20 transfer or ETH deposit).
3. Attacker calls `upgrade(0xdeadbeef)` where `0xdeadbeef` is an undeclared class hash.
4. `execute_replace_class` in the OS updates Contract A's `StateEntry` with `class_hash=0xdeadbeef` — **no validation performed**.
5. Any subsequent call to Contract A reaches `execute_entry_point`:
   - `dict_read(contract_class_changes, 0xdeadbeef)` → returns `0` (undeclared).
   - `find_element(compiled_class_facts_bundle, key=0)` → assertion failure; call fails.
6. All calls to Contract A permanently fail. All funds held by Contract A are frozen with no recovery path. [5](#0-4) [6](#0-5)

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
