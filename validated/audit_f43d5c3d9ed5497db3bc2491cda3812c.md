### Title
Missing Declared Class Hash Validation in `execute_replace_class` Enables Permanent Contract Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall in the StarkNet OS accepts any arbitrary felt value as the new class hash without verifying that it corresponds to a declared contract class. An unprivileged user can deploy a contract that calls `replace_class` with an undeclared class hash, permanently bricking the contract and freezing any funds it holds. The missing check is explicitly acknowledged by a TODO comment in the code.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` reads the new class hash directly from the caller-controlled syscall request and writes it into `contract_state_changes` with no validation that the hash exists in `contract_class_changes` (i.e., that it was ever declared):

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

The comment `// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.` is a self-admission that this guard is absent. [2](#0-1) 

Once the state is committed with an undeclared class hash, any subsequent call to the contract reaches `execute_entry_point`, which does:

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

Because the class hash was never declared, `dict_read` returns 0 (the default uninitialized value). `find_element` then searches for a compiled class fact with key `0`, which does not exist. In Cairo, `find_element` relies on a hint to locate the element and then verifies it; if no matching element exists, the hint fails, making the block **unprovable**. The state change is already committed, so the contract is permanently non-callable.

The syscall is dispatched from `execute_syscalls` using the calling contract's own address:

```cairo
if (selector == REPLACE_CLASS_SELECTOR) {
    execute_replace_class(contract_address=execution_context.execution_info.contract_address);
``` [4](#0-3) 

This means any contract can self-inflict this state on itself.

The analog to M-02 is direct: just as `applyRewards` grants the rewarder the ability to call arbitrary targets with arbitrary data (no validation of the swap target or output token), `execute_replace_class` grants any contract the ability to set its own class hash to any arbitrary felt value with no validation that the target class is declared. In both cases, the permissive implementation gives the caller unchecked control over a critical state transition.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

A contract holding user funds (e.g., a vault, escrow, or any ERC-20-holding contract) can call `replace_class` with an arbitrary undeclared class hash. After the transaction is included in a block and the state is committed:

1. The contract's class hash in `contract_state_changes` points to an undeclared hash.
2. Any future transaction that calls the contract causes `execute_entry_point` to fail at `find_element`, making the block unprovable.
3. The sequencer cannot include any block that touches the contract, permanently freezing all funds held by it.

If the bricked contract is a widely-used system contract (e.g., the fee token), the impact escalates to a **total network halt**.

---

### Likelihood Explanation

The attack requires only that an unprivileged user:

1. Deploys (or controls) any contract on StarkNet — a standard, permissionless operation.
2. Invokes a function in that contract that calls the `replace_class` syscall with an arbitrary felt value as the new class hash.

No privileged role, leaked key, or operator cooperation is required. The OS enforces no guard on the class hash value. The attack is deterministic and requires a single transaction.

---

### Recommendation

Inside `execute_replace_class`, before writing the new class hash to `contract_state_changes`, verify that the requested class hash is present in `contract_class_changes` (i.e., it has a non-zero compiled class hash entry):

```cairo
// Verify the new class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the check already performed implicitly in `execute_entry_point` when resolving a class hash to its compiled form, and closes the gap acknowledged by the existing TODO comment. [5](#0-4) 

---

### Proof of Concept

1. **Deploy attacker contract** — A contract whose `__execute__` function calls `replace_class(class_hash=0xdeadbeef)` (any felt not present in `contract_class_changes`).
2. **Submit invoke transaction** — An unprivileged user sends an invoke transaction targeting the attacker contract. The OS processes `execute_replace_class`; no validation occurs; `contract_state_changes[attacker_address].class_hash` is set to `0xdeadbeef`.
3. **State committed** — The block is proven successfully (the `replace_class` call itself does not fail).
4. **Subsequent call** — In the next block, any transaction calling the attacker contract reaches `execute_entry_point`. `dict_read(contract_class_changes, 0xdeadbeef)` returns `0`. `find_element(..., key=0)` finds no compiled class fact and the hint fails.
5. **Block unprovable** — The block containing the call cannot be proven. The contract is permanently non-callable, and any funds it holds are permanently frozen. [6](#0-5) [1](#0-0)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L195-203)
```text
    if (selector == REPLACE_CLASS_SELECTOR) {
        execute_replace_class(contract_address=execution_context.execution_info.contract_address);
        %{ OsLoggerExitSyscall %}
        return execute_syscalls(
            block_context=block_context,
            execution_context=execution_context,
            syscall_ptr_end=syscall_ptr_end,
        );
    }
```
