### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Unprovable Block Construction — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS Cairo program accepts an arbitrary `class_hash` from the contract without verifying that the hash corresponds to a declared class. A contract can call `replace_class` during `__validate__` with an undeclared class hash. The OS then updates the contract's class hash in `contract_state_changes`. When `__execute__` is subsequently dispatched using the updated class hash, `execute_entry_point` attempts to look up the compiled class for the undeclared hash, receives `compiled_class_hash = 0` from the dict, and then calls `find_element` with key `0`. Since no compiled class with hash `0` exists in `compiled_class_facts_bundle`, the OS proof fails, making the block unprovable and halting the network.

---

### Finding Description

In `execute_replace_class` (lines 878–916 of `syscall_impls.cairo`), the new class hash is taken directly from the syscall request and written into `contract_state_changes` with no validation:

```cairo
let class_hash = request.class_hash;
// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
``` [1](#0-0) 

The TODO comment is an explicit acknowledgment that this validation is absent. The OS unconditionally writes the attacker-supplied hash into state:

```cairo
dict_update{dict_ptr=contract_state_changes}(
    key=contract_address,
    prev_value=cast(state_entry, felt),
    new_value=cast(new_state_entry, felt),
);
``` [2](#0-1) 

After `__validate__` completes, `execute_invoke_function_transaction` calls `update_class_hash_in_execution_context`, which re-reads the (now poisoned) class hash from `contract_state_changes`: [3](#0-2) 

The updated context is then passed to `__execute__`. Inside `execute_entry_point`, the OS performs:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash   // undeclared hash → returns 0
);
...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    ...
    key=compiled_class_hash,           // key = 0, not present → proof fails
);
``` [4](#0-3) 

`find_element` is a verified Cairo primitive; if the key is absent from the sorted array, the proof is unsatisfiable. The block becomes unprovable.

---

### Impact Explanation

**High — Network not being able to confirm new transactions (total network shutdown).**

If a block containing such a transaction reaches the proving stage, the OS Cairo program cannot generate a valid proof. The block is permanently stuck: it cannot be proven, and the sequencer must identify and excise the offending transaction before re-executing. If the sequencer's blockifier does not independently enforce the declared-class check (consistent with the OS TODO), the sequencer will include the transaction, the prover will fail, and no subsequent blocks can be finalized until the issue is resolved manually.

---

### Likelihood Explanation

The `replace_class` syscall is a standard, publicly accessible syscall available to any deployed contract. The missing check is explicitly flagged with a TODO comment dated 2026, indicating it is a known gap in the OS validation logic. Any account contract whose `__validate__` function calls `replace_class` with an arbitrary felt value (e.g., `1` or any non-declared hash) triggers this path. The attacker-controlled entry point is the `replace_class` syscall request field `class_hash`, which is a single felt with no constraints applied by the OS.

---

### Recommendation

In `execute_replace_class`, before writing the new class hash to `contract_state_changes`, verify that `class_hash` is present in `contract_class_changes` (i.e., it has been declared in the current or a prior block). This is exactly what the existing TODO comment calls for. The check should mirror the lookup already performed in `execute_entry_point`:

```cairo
// Verify the class is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

This ensures the OS enforces the same invariant as the blockifier, closing the gap between the two execution layers.

---

### Proof of Concept

1. Deploy an account contract whose `__validate__` entry point issues a `replace_class` syscall with `class_hash = 0xDEAD` (an undeclared felt).
2. Submit an invoke transaction from this account.
3. The OS processes `__validate__`: the contract bytecode runs, emitting the `replace_class` syscall.
4. `call_execute_syscalls`

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-911)
```text
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

```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L94-108)
```text
func update_class_hash_in_execution_context{range_check_ptr, contract_state_changes: DictAccess*}(
    execution_context: ExecutionContext*
) -> ExecutionContext* {
    let (state_entry: StateEntry*) = dict_read{dict_ptr=contract_state_changes}(
        key=execution_context.execution_info.contract_address
    );
    return new ExecutionContext(
        entry_point_type=execution_context.entry_point_type,
        class_hash=state_entry.class_hash,
        calldata_size=execution_context.calldata_size,
        calldata=execution_context.calldata,
        execution_info=execution_context.execution_info,
        deprecated_tx_info=execution_context.deprecated_tx_info,
    );
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
