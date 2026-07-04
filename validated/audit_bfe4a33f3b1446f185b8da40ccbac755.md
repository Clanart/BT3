### Title
Missing Class Hash Existence Validation in `execute_replace_class` Allows Permanent Contract Freezing - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary

The `execute_replace_class` function in the StarkNet OS does not verify that the new class hash supplied by a contract is actually declared (i.e., present in `contract_class_changes`). The OS itself contains a `TODO` comment acknowledging this missing check. Because the OS is the authoritative proof verifier, this omission means a valid proof can be generated for a state where a contract's class hash is set to an undeclared value. Any subsequent transaction targeting that contract will cause the OS to panic during proof generation, permanently freezing the contract's funds and halting block confirmation.

### Finding Description

In `execute_replace_class` (`syscall_impls.cairo`, lines 878–916), the OS updates `contract_state_changes` with the caller-supplied `class_hash` without verifying it is a declared class:

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
``` [1](#0-0) 

The TODO deadline of `1/1/2026` has already passed (today is 2026-07-03), confirming the check remains unimplemented.

When any future transaction calls the affected contract, `execute_entry_point` reads the class hash from `contract_state_changes` and then looks up the compiled class hash via `dict_read` from `contract_class_changes`:

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

If the class hash was never declared, `dict_read` returns `0` (the default for an uninitialized Cairo dict). `find_element` then searches for `compiled_class_hash = 0` in the compiled class facts array. If `0` is absent (the normal case), `find_element` raises an assertion failure, causing the OS to panic. The block containing that transaction cannot be proven.

### Impact Explanation

**Critical — Permanent freezing of funds.**

1. A malicious sequencer includes a transaction that calls `replace_class` with an arbitrary, undeclared class hash.
2. The OS accepts the syscall and commits the invalid class hash to `contract_state_changes`.
3. A valid STARK proof is generated for this block (the OS does not reject it).
4. The L1 verifier accepts the proof; the on-chain state root is updated.
5. Any future transaction targeting the affected contract causes the OS to panic at `find_element` during proof generation of the next block.
6. The contract is permanently uncallable; all funds held by it are frozen.
7. If the frozen contract is a widely-used system contract (e.g., the fee token), this escalates to a **network halt** — no block containing a call to it can ever be proven.

### Likelihood Explanation

The StarkNet OS is the trust anchor of the protocol. Its role is to enforce all protocol invariants so that even a malicious sequencer cannot produce a valid proof for an invalid state transition. Because the OS omits this check, a sequencer that deviates from the blockifier's off-chain validation can produce a provably-valid block containing the invalid `replace_class`. The TODO comment in the production code confirms the check is absent and was scheduled but not implemented. The attack requires only the ability to submit a transaction from a deployed contract — an unprivileged capability.

### Recommendation

Inside `execute_replace_class`, before updating `contract_state_changes`, verify that the supplied `class_hash` is present in `contract_class_changes` (i.e., has a non-zero compiled class hash):

```cairo
// Verify the class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the recommendation in the external report: confirm existence before allowing state to be updated, and revert if the check fails.

### Proof of Concept

1. Deploy contract `C` with a valid class hash `H_valid`.
2. From within `C`, invoke the `replace_class` syscall with `class_hash = H_fake`, where `H_fake` has never been declared via a `declare` transaction.
3. The OS executes `execute_replace_class`: no existence check is performed; `contract_state_changes[C].class_hash` is set to `H_fake`. The block is proven successfully.
4. In the next block, any transaction that calls contract `C` reaches `execute_entry_point`. `dict_read(contract_class_changes, H_fake)` returns `0`. `find_element(..., key=0)` fails with an assertion error.
5. The block cannot be proven. All funds in `C` are permanently frozen. [3](#0-2) [4](#0-3)

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
