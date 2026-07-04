### Title
Missing Declared Class Hash Validation in `execute_replace_class` Enables Forced Network Halt via L1 Handler - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall in the StarkNet OS accepts any arbitrary class hash from a contract without verifying it is a declared class. An unprivileged attacker can replace their contract's class with an undeclared hash, then send an L1 message to that contract. Because L1 handler transactions are forced on the sequencer, the OS will attempt to prove a block containing a call to a contract whose class hash cannot be resolved in the compiled class facts, causing `find_element` to panic and the block to be unprovable — a total network halt.

---

### Finding Description

In `execute_replace_class` (`syscall_impls.cairo`, lines 878–916), the new `class_hash` from the syscall request is written directly into the contract state with no check that it corresponds to a declared class. The code itself acknowledges this with an explicit TODO:

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

When the contract is subsequently called (e.g., via a forced L1 handler), `execute_entry_point` reads the class hash and attempts to resolve it:

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

If `execution_context.class_hash` is an undeclared hash, `dict_read` returns the default value `0` (Cairo dict semantics for an uninitialized key). `find_element` then searches for `compiled_class_hash = 0` in the compiled class facts bundle. Since `0` is not a valid compiled class hash, `find_element` panics with an assertion failure, terminating OS execution and making the block unprovable.

---

### Impact Explanation

**High — Network not being able to confirm new transactions (total network shutdown).**

- The OS panics during proving of any block that contains a call to a contract whose class hash was replaced with an undeclared value.
- L1 handler transactions are **forced**: once an L1 message is sent to a contract, the sequencer has no discretion to exclude the corresponding L1 handler transaction from the block.
- The sequencer is therefore stuck: it cannot prove the block (OS panics), and it cannot drop the L1 handler (protocol obligation). This results in a total halt of block finalization.

---

### Likelihood Explanation

The attack is reachable by three classes of unprivileged protocol users acting in sequence:

1. **Contract deployer** — deploys a contract (no privilege required).
2. **Transaction sender** — calls the contract, which invokes `replace_class` with an arbitrary undeclared hash. The OS performs no validation; the state is updated unconditionally.
3. **L1/L2 message sender** — sends an L1 message targeting the now-corrupted contract. The sequencer is obligated to include the resulting L1 handler.

The only uncertainty is whether the **blockifier** (Rust execution engine, out of scope) independently rejects `replace_class` calls with undeclared hashes before the transaction reaches the OS. The OS code itself carries an explicit TODO acknowledging the missing check, which strongly suggests this guard is absent or incomplete at the OS layer. If the blockifier also lacks this check, the attack is straightforward and requires no special privileges.

---

### Recommendation

Add a validation step in `execute_replace_class` to confirm the new class hash exists in `contract_class_changes` (i.e., has been declared) before updating the contract state:

```cairo
// Verify that the class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the lookup performed in `execute_entry_point` and closes the gap between what the OS accepts and what it can safely prove.

---

### Proof of Concept

1. **Attacker (contract deployer)** deploys contract `A` with a valid class hash.
2. **Attacker (transaction sender)** calls contract `A`, which internally invokes the `replace_class` syscall with an arbitrary undeclared felt value (e.g., `0xdeadbeef`).
3. `execute_replace_class` in `syscall_impls.cairo` (line 896) accepts the call without validation and updates contract `A`'s state entry: `class_hash = 0xdeadbeef`.
4. **Attacker (L1 message sender)** sends an L1→L2 message targeting contract `A` (e.g., calling any L1 handler selector).
5. The sequencer is forced to include the L1 handler transaction for contract `A` in the next block.
6. During proving, the OS calls `execute_entry_point` for contract `A`.
7. `dict_read{dict_ptr=contract_class_changes}(key=0xdeadbeef)` returns `0` (undeclared).
8. `find_element(..., key=0)` searches the compiled class facts for hash `0`; it is not present.
9. `find_element` panics → OS execution terminates → block cannot be proven → **network halt**. [3](#0-2) [4](#0-3)

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
