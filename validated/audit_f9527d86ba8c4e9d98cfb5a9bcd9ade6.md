### Title
Missing Declared Class Hash Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS accepts any arbitrary felt value as the new class hash without verifying that it corresponds to a previously declared class. This is an explicitly acknowledged missing check (marked `TODO` in the code). Because the OS commits this invalid state transition to the chain, any contract that calls `replace_class` with an undeclared hash becomes permanently unexecutable, freezing all funds it holds.

---

### Finding Description

In `execute_replace_class` (syscall_impls.cairo, lines 877–915), the OS reads the requested `class_hash` directly from the syscall request and writes it into the contract's `StateEntry` without any validation:

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

The `class_hash` field is taken from `request.class_hash` with no cross-reference against the `compiled_class_facts_bundle` or `contract_class_changes` dict. The TODO comment at line 898 explicitly acknowledges this missing invariant. [2](#0-1) 

By contrast, `execute_entry_point` (execute_entry_point.cairo, lines 154–166) requires the class hash to be present in `compiled_class_facts_bundle` via `find_element`. If the class hash is not found, the call fails:

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
``` [3](#0-2) 

Once an invalid class hash is committed to state, every future call to that contract will fail at `find_element`, making the contract permanently unexecutable.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

After the OS commits the invalid `StateEntry`, the contract's class hash points to a non-existent compiled class. Every subsequent call to the contract (including any call that would withdraw or transfer funds) fails unconditionally inside `execute_entry_point`. There is no recovery path: the OS has no mechanism to revert a committed state update, and the invalid class hash cannot be corrected without a protocol-level upgrade. Any ERC-20 balances, ETH, or other assets held by the contract are permanently inaccessible.

---

### Likelihood Explanation

**Medium.**

The attack requires an unprivileged actor to deploy a contract whose code calls `replace_class` with an attacker-chosen (undeclared) class hash. Deploying a contract is a permissionless action on StarkNet. The attacker can make the contract appear legitimate (e.g., a token vault or DEX pool), attract user deposits, and then invoke `replace_class` with an arbitrary felt. Because the OS does not validate the class hash, the state transition is accepted and finalized. The social-engineering component reduces likelihood, but the OS-level missing check is the necessary enabling condition: if the OS enforced the invariant, the transaction would revert and no funds would be frozen.

---

### Recommendation

Before writing the new `StateEntry`, verify that `class_hash` is present in `contract_class_changes` (i.e., it has been declared in the current or a prior block). Concretely, perform a `dict_read` on `contract_class_changes` with `class_hash` as the key and assert the returned compiled class hash is non-zero:

```cairo
// Verify the new class hash is a declared class.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

This mirrors the check already performed implicitly in `execute_entry_point` and closes the gap between what the OS accepts at state-write time and what it can actually execute later.

---

### Proof of Concept

1. Attacker deploys Contract A (e.g., a token vault) with a legitimate class hash `C_valid`. Users deposit funds.
2. Attacker calls a function in Contract A that internally invokes the `replace_class` syscall with `class_hash = 0xdeadbeef` (an undeclared felt).
3. The OS executes `execute_replace_class`. At line 898 the TODO check is absent; the OS writes `StateEntry(class_hash=0xdeadbeef, ...)` into `contract_state_changes` and commits it.
4. Any subsequent transaction targeting Contract A reaches `execute_entry_point` → `dict_read(contract_class_changes, key=0xdeadbeef)` returns 0 → `find_element` fails to locate a `CompiledClassFact` → the call reverts with `ENTRYPOINT_NOT_FOUND`.
5. All funds held by Contract A are permanently frozen with no recovery path. [4](#0-3) [5](#0-4)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L877-915)
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
