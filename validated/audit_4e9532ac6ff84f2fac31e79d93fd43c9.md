### Title
Unguarded `call_contract` Syscall to Reserved Addresses with `class_hash=0` Causes OS Execution Failure — (File: `execution/syscall_impls.cairo`)

---

### Summary

`execute_call_contract` in `syscall_impls.cairo` does not validate that the target contract address is not a reserved system address (e.g., `ALIAS_CONTRACT_ADDRESS`). The `ALIAS_CONTRACT_ADDRESS` is explicitly stored with `class_hash=0` in the OS state (`state/state.cairo`). When `execute_entry_point` is subsequently called with `class_hash=0`, it performs a `find_element` lookup for compiled class hash `0` — a hard Cairo VM assertion that fails if no compiled class with hash `0` exists. This causes the entire OS execution to abort, making the block unproducible and halting the network.

---

### Finding Description

**Step 1 — Reserved address has `class_hash=0` in OS state.**

In `state/state.cairo`, during alias allocation, the alias contract's `StateEntry` is explicitly constructed with `class_hash=0`:

```cairo
assert [prev_aliases_state_entry] = StateEntry(
    class_hash=0, storage_ptr=squashed_aliases_storage_start, nonce=0
);
tempvar new_aliases_state_entry = new StateEntry(
    class_hash=0, storage_ptr=squashed_aliases_storage_end, nonce=0
);
``` [1](#0-0) 

**Step 2 — `deploy_contract` guards reserved addresses, but `execute_call_contract` does not.**

`deploy_contract.cairo` explicitly prevents deploying to `ALIAS_CONTRACT_ADDRESS`:

```cairo
assert_not_zero(
    (contract_address - ORIGIN_ADDRESS) * (contract_address - BLOCK_HASH_CONTRACT_ADDRESS) * (
        contract_address - ALIAS_CONTRACT_ADDRESS
    ) * (contract_address - RESERVED_CONTRACT_ADDRESS),
);
``` [2](#0-1) 

However, `execute_call_contract` in `syscall_impls.cairo` performs **no such check**. It only guards against `EXECUTE_ENTRY_POINT_SELECTOR` and insufficient gas, then unconditionally reads the state entry and proceeds to call `contract_call_helper`:

```cairo
tempvar contract_address = request.contract_address;
let (state_entry: StateEntry*) = dict_read{dict_ptr=contract_state_changes}(
    key=contract_address
);
// ...
tempvar execution_context: ExecutionContext* = new ExecutionContext(
    ...
    class_hash=state_entry.class_hash,   // <-- 0 for ALIAS_CONTRACT_ADDRESS
    ...
);
contract_call_helper(..., execution_context=execution_context);
``` [3](#0-2) 

**Step 3 — `execute_entry_point` performs a hard `find_element` lookup with `class_hash=0`.**

Inside `execute_entry_point`, the OS first reads the compiled class hash from `contract_class_changes` using `class_hash` as the key. For `class_hash=0`, the dict returns `0` (the default value for uninitialized entries). It then calls `find_element` to locate the compiled class fact:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash          // key = 0
);
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    elm_size=CompiledClassFact.SIZE,
    n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
    key=compiled_class_hash,                  // key = 0
);
``` [4](#0-3) 

`find_element` is a **hard Cairo VM assertion**: its hint raises `StopIteration` if the key is absent, aborting the entire Cairo program. No compiled class with hash `0` is ever legitimately registered, so this always fails.

**Step 4 — Syscalls are processed even for reverted entry points.**

Even when the outer entry point is reverted, `call_execute_syscalls` is still invoked with the full syscall buffer:

```cairo
if (is_reverted != FALSE) {
    let revert_log = init_revert_log();
} else {
    ...
}
with syscall_ptr {
    call_execute_syscalls(
        block_context=block_context,
        execution_context=execution_context,
        syscall_ptr_end=entry_point_return_values.syscall_ptr,
    );
}
``` [5](#0-4) 

This means a `call_contract` syscall to `ALIAS_CONTRACT_ADDRESS` emitted before the revert is still replayed by the OS, triggering the fatal path.

---

### Impact Explanation

When the OS processes a block containing a transaction that emits `call_contract(ALIAS_CONTRACT_ADDRESS, ...)`, the `find_element` call aborts the Cairo VM. The block cannot be proven. The sequencer cannot advance the chain. This is a **complete network halt** — no new transactions can be confirmed.

---

### Likelihood Explanation

Any unprivileged user can deploy a Cairo contract containing a single `call_contract` syscall targeting `ALIAS_CONTRACT_ADDRESS`. The blockifier will execute the transaction (marking the inner call as failed/reverted), include it in the block, and the OS will then fail when replaying the syscall. No special privileges, leaked keys, or operator cooperation are required.

---

### Recommendation

1. **In `execute_call_contract`** (`syscall_impls.cairo`): add a guard analogous to the one in `deploy_contract.cairo` that rejects calls to reserved addresses (`ORIGIN_ADDRESS`, `BLOCK_HASH_CONTRACT_ADDRESS`, `ALIAS_CONTRACT_ADDRESS`, `RESERVED_CONTRACT_ADDRESS`) by writing a failure response and returning early.

2. **In `execute_entry_point`** (`execute_entry_point.cairo`): add an explicit check that `class_hash != 0` (i.e., `class_hash != UNINITIALIZED_CLASS_HASH`) before performing the `find_element` lookup, returning a soft revert (`ERROR_ENTRY_POINT_NOT_FOUND`) instead of a hard VM abort.

---

### Proof of Concept

1. Attacker deploys contract `A` with an entry point that executes:
   ```
   call_contract(contract_address=ALIAS_CONTRACT_ADDRESS, selector=any_selector, calldata=[])
   ```
2. Attacker submits an invoke transaction calling that entry point.
3. The blockifier executes the transaction: the inner call to `ALIAS_CONTRACT_ADDRESS` fails (no class), the outer call reverts. The transaction is included in the block with revert status.
4. The OS begins proving the block. It replays the syscall buffer for the reverted entry point

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/state.cairo (L172-177)
```text
    assert [prev_aliases_state_entry] = StateEntry(
        class_hash=0, storage_ptr=squashed_aliases_storage_start, nonce=0
    );

    tempvar new_aliases_state_entry = new StateEntry(
        class_hash=0, storage_ptr=squashed_aliases_storage_end, nonce=0
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L44-49)
```text
    // Assert that we don't deploy to one of the reserved addresses.
    assert_not_zero(
        (contract_address - ORIGIN_ADDRESS) * (contract_address - BLOCK_HASH_CONTRACT_ADDRESS) * (
            contract_address - ALIAS_CONTRACT_ADDRESS
        ) * (contract_address - RESERVED_CONTRACT_ADDRESS),
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L192-225)
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

    // Since we process the revert log backwards, entries before this point belong to the caller.
    assert [revert_log] = RevertLogEntry(selector=CHANGE_CONTRACT_ENTRY, value=caller_address);
    let revert_log = &revert_log[1];

    contract_call_helper(
        remaining_gas=remaining_gas,
        block_context=block_context,
        execution_context=execution_context,
    );
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L288-307)
```text
    if (is_reverted != FALSE) {
        // Create a dummy OsCarriedOutputs so that messages to L1 will be discarded.
        // The dummy is initialized with
        // OsCarriedOutputs(messages_to_l1="empty segment", messages_to_l2=0).
        %{ GenerateDummyOsOutputSegment %}
        // Create a new revert log for the reverted entry point. This will be used to revert the
        // entry point changes after calling `call_execute_syscalls`.
        let revert_log = init_revert_log();
    } else {
        assert outputs = orig_outputs;
        tempvar revert_log = orig_revert_log;
    }
    let builtin_ptrs = return_builtin_ptrs;
    with syscall_ptr {
        call_execute_syscalls(
            block_context=block_context,
            execution_context=execution_context,
            syscall_ptr_end=entry_point_return_values.syscall_ptr,
        );
    }
```
