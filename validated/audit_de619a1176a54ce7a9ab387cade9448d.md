### Title
Missing Declared-Class Validation in `execute_replace_class` Allows OS-Level Panic Leading to Network Halt — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS Cairo program does not verify that the caller-supplied class hash is actually declared (i.e., present in `contract_class_changes`). An unprivileged attacker can call `replace_class` with an arbitrary undeclared class hash, update a contract's class hash to that value, and then trigger a subsequent call to the same contract. When the OS's `execute_entry_point` attempts to resolve the undeclared class hash, it performs a `dict_read` that returns 0 (the Cairo dict default for an uninitialized key), then calls `find_element` with `key=0`. If no compiled class fact with hash 0 exists — the normal case — `find_element` panics, causing the entire OS execution to abort and the block to be unprovable. This is a permanent network halt.

---

### Finding Description

**Root cause — missing validation in `execute_replace_class`:**

```cairo
// syscall_impls.cairo, execute_replace_class
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

The TODO comment explicitly acknowledges the missing check. No assertion is made that `class_hash` exists in `contract_class_changes`. Any felt value — including 0 or any arbitrary undeclared hash — is accepted and written into the contract's state entry. [1](#0-0) 

**Downstream panic in `execute_entry_point`:**

When the contract is subsequently called, `execute_entry_point` resolves the class hash:

```cairo
// execute_entry_point.cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash   // <-- the attacker-controlled undeclared hash
);

let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    elm_size=CompiledClassFact.SIZE,
    n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
    key=compiled_class_hash,           // <-- 0, the dict default for an uninitialized key
);
```

Because the attacker-supplied class hash is not in `contract_class_changes`, `dict_read` returns 0 (Cairo dict default). `find_element` then searches for a compiled class fact with hash 0. No such fact exists in a normal block, so `find_element` asserts failure — an OS-level panic, not a contract-level revert. [2](#0-1) 

**The analog to the external report:**

| External Report | This Codebase |
|---|---|
| `adapterToId[unknownAddr]` returns 0 (Solidity default) | `dict_read(contract_class_changes, undeclared_hash)` returns 0 (Cairo dict default) |
| `isAdapterInitialized(0)` returns `true` because adapterId 0 is valid | `find_element(..., key=0)` panics because compiled class hash 0 does not exist |
| Any address passes the adapter check | Any class hash passes `replace_class`, corrupting the contract's class pointer |
| Attacker drains funds | OS execution aborts; block cannot be proven → network halt |

---

### Impact Explanation

When `execute_entry_point` is called for a contract whose class hash was replaced with an undeclared value, `find_element` panics at the OS level. This is not a transaction revert — it is an assertion failure inside the Cairo OS program itself. The block containing the subsequent call to the affected contract cannot be proven. Because the StarkNet network depends on block proofs for finality, this constitutes a **permanent network halt** for any block that includes such a call.

---

### Likelihood Explanation

The attack requires only:
1. Deploying any contract (standard unprivileged operation).
2. Calling `replace_class(undeclared_hash)` from within that contract — no privileged role required; any contract can invoke this syscall on itself.
3. Arranging for the same contract to be called again in a subsequent block (trivially done by the attacker themselves, or by any other user who interacts with the contract).

The attacker does not need a leaked key, operator access, or any trusted role. The `replace_class` syscall is a standard user-facing feature. The missing validation is confirmed by the explicit TODO comment in the production OS code.

---

### Recommendation

In `execute_replace_class`, before writing the new class hash into `contract_state_changes`, assert that the class hash is present in `contract_class_changes` (i.e., that it has been declared):

```cairo
// After: let class_hash = request.class_hash;
// Add:
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the validation that `execute_entry_point` implicitly relies on and closes the gap between what the sequencer accepts and what the OS can prove.

---

### Proof of Concept

**Attack sequence (single transaction, same block):**

1. Attacker deploys `MaliciousContract` with a valid declared class hash `C`.
2. Attacker calls `MaliciousContract.__execute__()`.
3. Inside `__execute__`, the contract calls `replace_class(0xDEAD)` where `0xDEAD` is not declared.
   - OS: `execute_replace_class` writes `contract_state_changes[MaliciousContract].class_hash = 0xDEAD`. No validation. Succeeds.
4. Inside `__execute__`, the contract then calls itself via `call_contract(MaliciousContract, some_selector, ...)`.
   - OS: `execute_call_contract` reads `state_entry = dict_read(contract_state_changes, MaliciousContract)` → `state_entry.class_hash = 0xDEAD`.
   - OS: `execute_entry_point` is called with `execution_context.class_hash = 0xDEAD`.
   - OS: `compiled_class_hash = dict_read(contract_class_changes, 0xDEAD)` → **returns 0** (uninitialized key).
   - OS: `find_element(compiled_class_facts, key=0)` → **panics** (no compiled class with hash 0).
5. OS execution aborts. The block cannot be proven. **Network halt.**

The `call_contract` path confirms the updated class hash is used for the inner call: [3](#0-2)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L192-215)
```text
    tempvar contract_address = request.contract_address;
    let (state_entry: StateEntry*) = dict_read{dict_ptr=contract_state_changes}(
        key=contract_address
    );

    // Prepare execution context.
    // TODO(Yoni, 1/1/2026): change ExecutionContext to hold calldata_start, calldata_end.
    tempvar calldata_start = request.calldata_start;
    tempvar caller_execution_info = caller_execution_context.execution_info;
    tempvar caller_address = caller_execution_info.contract_address;
    tempvar execution_context: ExecutionContext* = new ExecutionContext(
        entry_point_type=ENTRY_POINT_TYPE_EXTERNAL,
        class_hash=state_entry.class_hash,
        calldata_size=request.calldata_end - calldata_start,
        calldata=calldata_start,
        execution_info=new ExecutionInfo(
            block_info=caller_execution_info.block_info,
            tx_info=caller_execution_info.tx_info,
            caller_address=caller_address,
            contract_address=contract_address,
            selector=request.selector,
        ),
        deprecated_tx_info=caller_execution_context.deprecated_tx_info,
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-915)
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

    assert [revert_log] = RevertLogEntry(selector=CHANGE_CLASS_ENTRY, value=state_entry.class_hash);
    let revert_log = &revert_log[1];

    return ();
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
