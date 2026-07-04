### Title
Missing Validation of New Class Hash in Single-Step `replace_class` Syscall Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS performs a single-step, irreversible replacement of a contract's class hash with no validation that the supplied hash corresponds to a declared class. This is the direct structural analog of the Folks Finance single-step admin transfer: a critical, permanent state change is committed without a confirmation or existence check. A malicious contract deployer can exploit this to permanently brick any contract (including one holding user funds), causing irreversible freezing of those funds.

---

### Finding Description

`execute_replace_class` in `syscall_impls.cairo` reads the caller-supplied `class_hash` directly from the syscall request and immediately writes it into `contract_state_changes` with no check that the hash corresponds to a declared class:

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

The inline `TODO` comment explicitly acknowledges the missing check. [1](#0-0) 

Once the state is committed, the class hash stored for that contract address is the arbitrary attacker-supplied value. When any subsequent call targets that contract, `execute_entry_point` performs:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    ...
    key=compiled_class_hash,
);
``` [2](#0-1) 

If the class hash was never declared, `dict_read` returns 0 (the default uninitialized value), and `find_element` fails with an assertion error because no compiled class with hash 0 exists. This makes the contract permanently unreachable at the OS level — every future call to it will fail — and any funds held in the contract are frozen forever.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

A malicious contract deployer deploys a contract that accepts user deposits. After accumulating funds, the deployer triggers a call that invokes the `replace_class` syscall with an arbitrary, undeclared felt value as the new class hash. The OS accepts this without validation. The contract's class hash in the global state is permanently overwritten. All subsequent calls to the contract fail at the OS proof level (entry point lookup fails), making the contract and all its stored funds permanently inaccessible. There is no recovery path: the state change is irreversible, and no administrative override exists at the OS layer.

---

### Likelihood Explanation

**Medium.** The attack requires a malicious contract deployer — an explicitly allowed attacker type in the bounty scope. The deployer must attract user deposits before triggering the class replacement. The missing check is acknowledged by the codebase itself via the `TODO` comment, confirming the OS does not currently enforce this invariant. No privileged operator access, leaked keys, or external dependency compromise is required.

---

### Recommendation

In `execute_replace_class`, before committing the new class hash to `contract_state_changes`, verify that the supplied `class_hash` has a corresponding entry in `contract_class_changes` (i.e., it has been declared in the current or a prior block). This mirrors the two-step pattern recommended in the Folks Finance report: the "propose" step (declaring the class) must precede the "accept" step (replacing the contract's class). Concretely, perform a `dict_read` on `contract_class_changes` for the new `class_hash` and assert the result is non-zero before proceeding with the `dict_update`.

---

### Proof of Concept

1. Deployer declares a legitimate class `C_legit` and deploys contract `V` (a "vault") using `C_legit`.
2. Users deposit funds into `V`; the contract's storage now holds user balances.
3. Deployer calls a function on `V` that internally invokes the `replace_class` syscall with `class_hash = 0xdeadbeef` (an undeclared felt).
4. `execute_replace_class` in `syscall_impls.cairo` (lines 896–910) writes `class_hash=0xdeadbeef` into `contract_state_changes` for `V`'s address with no validation. The transaction is accepted by the OS.
5. Any subsequent call to `V` reaches `execute_entry_point` (lines 154–166 of `execute_entry_point.cairo`): `dict_read` on `contract_class_changes` for key `0xdeadbeef` returns 0; `find_element` for compiled class hash 0 finds no entry and fails.
6. All calls to `V` are permanently broken. User funds are frozen with no recovery mechanism. [3](#0-2) [2](#0-1)

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
