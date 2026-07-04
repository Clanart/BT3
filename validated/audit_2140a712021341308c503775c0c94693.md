### Title
Missing Declared Class Hash Validation in `execute_replace_class` Allows Unprovable Block Construction - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS accepts an arbitrary, caller-supplied class hash without verifying that the hash corresponds to a previously declared contract class. This is directly analogous to the external report's vulnerability class: an unconstrained input bypasses validation. Any unprivileged contract can call `replace_class` with a fabricated class hash, committing an undeclared class hash into the on-chain state. Any subsequent call to that contract — in the same or a future block — causes the OS prover to fail when it cannot resolve the class hash to a compiled class fact, making the block unprovable and halting the network.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function reads the new class hash directly from the syscall request and writes it into `contract_state_changes` without any check that the class hash is declared:

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

The developer-acknowledged TODO comment at line 898 explicitly states the missing check. The `class_hash` field comes directly from `request.class_hash`, which is caller-controlled syscall input with no bounds or existence check applied.

When any subsequent execution targets this contract, `execute_entry_point` performs:

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

If the class hash is undeclared, `dict_read` returns the default value `0`, and `find_element` cannot locate a compiled class fact with hash `0`. Since `find_element` is a hint-driven assertion in Cairo, the prover cannot satisfy the constraint, making the block unprovable.

---

### Impact Explanation

**Impact: High — Network not being able to confirm new transactions (total network shutdown).**

Once a contract's class hash is replaced with an undeclared value and the state is committed, every future block that includes any call to that contract (including cross-contract calls, `call_contract` syscalls, or fee-related calls) will fail at the proving stage. The sequencer cannot produce a valid STARK proof for such a block, preventing L1 state updates and halting the network's ability to confirm new transactions.

---

### Likelihood Explanation

Any unprivileged user can:
1. Deploy a contract (standard operation).
2. Have that contract invoke the `replace_class` syscall with an arbitrary felt value as the class hash.
3. The OS accepts and commits this state change without validation.

No privileged role, leaked key, or external dependency is required. The attack is cheap (one deploy + one invoke) and the TODO comment confirms the check is intentionally absent from the current implementation.

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, verify that the supplied `class_hash` exists in `contract_class_changes` (i.e., has a non-zero compiled class hash mapping). This mirrors the check already performed in `execute_entry_point` when resolving a class for execution:

```cairo
// Proposed fix in execute_replace_class:
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=class_hash
);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This ensures only previously declared classes can be used as replacement targets, closing the unconstrained-input path.

---

### Proof of Concept

1. Attacker deploys `ContractA` with a valid declared class hash `C1`.
2. `ContractA`'s constructor or an external entry point calls `replace_class(0xdeadbeef)` — an arbitrary undeclared felt.
3. The OS executes `execute_replace_class`: the TODO check is absent, so `0xdeadbeef` is written as `ContractA`'s class hash in `contract_state_changes`.
4. The block is sealed and state is committed with `ContractA.class_hash = 0xdeadbeef`.
5. In the next block, any transaction that calls `ContractA` (or any contract that internally calls `ContractA`) triggers `execute_entry_point`:
   - `dict_read(contract_class_changes, 0xdeadbeef)` → returns `0` (undeclared).
   - `find_element(..., key=0)` → no compiled class fact found; hint fails.
   - The prover cannot generate a valid proof for the block.
6. The network cannot finalize any block containing such a call, resulting in a total network halt for all transactions routed through or after `ContractA`. [3](#0-2) [4](#0-3)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L142-177)
```text
func execute_entry_point{
    range_check_ptr,
    remaining_gas: felt,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    revert_log: RevertLogEntry*,
    outputs: OsCarriedOutputs*,
}(block_context: BlockContext*, execution_context: ExecutionContext*) -> (
    is_reverted: felt, retdata_size: felt, retdata: felt*
) {
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
