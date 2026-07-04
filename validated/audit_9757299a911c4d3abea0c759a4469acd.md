### Title
Missing Declared-Class Validation in `execute_replace_class` Allows OS Proof Abort — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS program updates a contract's class hash to an attacker-supplied value without verifying that the new class hash has ever been declared on-chain. When the replaced contract is subsequently called in the same block, the OS attempts to look up the compiled class fact for the undeclared hash, fails with a Cairo assertion error, and aborts the entire OS execution. This prevents the block from being proven, constituting a network halt.

---

### Finding Description

`execute_replace_class` in `syscall_impls.cairo` reads the new class hash directly from the syscall request and writes it into `contract_state_changes` without any check that the hash exists in `contract_class_changes` (the class declaration registry):

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

The `class_hash` value originates from `request.class_hash`, which is fully attacker-controlled (it is the calldata of the `replace_class` syscall). No assertion or dict lookup is performed to confirm that `class_hash` was ever added to `contract_class_changes` via a declare transaction.

When a subsequent transaction in the same block calls the replaced contract, `execute_entry_point` performs:

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

Because the undeclared class hash was never inserted into `contract_class_changes`, `dict_read` returns the default value `0`. `find_element` then searches for a compiled class fact with hash `0`. No such fact exists in the bundle, so `find_element` raises a Cairo assertion failure, aborting the entire OS execution.

The `contract_class_changes` dict is initialized with `dict_new()`, which returns `0` for any key that has never been written: [3](#0-2) 

The OS explicitly acknowledges the missing check with a TODO comment, confirming this is a known gap: [4](#0-3) 

---

### Impact Explanation

**High — Network not being able to confirm new transactions (total network halt).**

When the OS aborts mid-execution due to the `find_element` assertion failure, the block cannot be proven. If the sequencer's off-chain simulation (blockifier) does not independently enforce the same declared-class precondition — which is plausible given the TODO exists only in the OS layer — the sequencer will include both the `replace_class` transaction and the follow-up call in the same block. The OS will then fail to generate a valid proof for that block, stalling the chain.

---

### Likelihood Explanation

**Medium.** The attack requires two transactions in the same block: one that calls `replace_class` with an undeclared hash, and one that calls the now-broken contract. The sequencer's blockifier must not independently reject the second transaction. Given that the missing check is explicitly flagged only in the OS (not in the blockifier), a divergence between blockifier acceptance and OS provability is plausible. Any unprivileged user who can deploy and invoke a contract can trigger this path.

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, assert that it exists in `contract_class_changes`:

```cairo
// Verify the class hash has been declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

This mirrors the check already implicitly required by `execute_entry_point` and closes the gap between what `replace_class` accepts and what the OS can actually prove.

---

### Proof of Concept

1. Attacker deploys Contract A containing a `replace_class(0xdeadbeef)` call in its `__execute__` entrypoint, where `0xdeadbeef` is an arbitrary felt that has never been declared.
2. Attacker submits Tx1: invoke Contract A → `replace_class(0xdeadbeef)` executes, `contract_state_changes[A].class_hash = 0xdeadbeef`. Syscall returns success; no OS check fires.
3. Attacker (or anyone) submits Tx2 in the same block: call Contract A with any selector.
4. OS processes Tx2: `execute_entry_point` calls `dict_read(contract_class_changes, 0xdeadbeef)` → returns `0`. Then `find_element(compiled_class_facts, 0)` → no fact with hash `0` exists → Cairo assertion failure.
5. OS execution aborts. Block cannot be proven. Network halts.

Relevant code path: [5](#0-4) [6](#0-5)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L878-915)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L153-167)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os.cairo (L267-275)
```text
    let (contract_state_changes: DictAccess*) = dict_new();

    %{ InitializeClassHashes %}
    // A dictionary from class hash to compiled class hash (Casm).
    let (contract_class_changes: DictAccess*) = dict_new();

    return (
        contract_state_changes=contract_state_changes, contract_class_changes=contract_class_changes
    );
```
