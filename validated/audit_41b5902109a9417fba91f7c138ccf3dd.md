### Title
Constructor Failure Not Handled in `deploy_contract` — Incomplete State Rollback on Revert — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo`)

---

### Summary

`deploy_contract` mutates `contract_state_changes` (setting the new class hash) and appends revert-log entries before invoking the constructor. If the constructor reverts, a hard `assert is_reverted = 0` fires instead of processing those revert-log entries. The state change is never rolled back, and the OS proof generation aborts. Any block the sequencer includes that contains a `deploy` syscall whose constructor reverts cannot be proven, causing a network halt.

---

### Finding Description

`deploy_contract` follows this sequence:

1. **State mutation** — writes the new `class_hash` into `contract_state_changes` via `dict_update`.
2. **Revert-log population** — appends `CHANGE_CONTRACT_ENTRY` (caller address) and `CHANGE_CLASS_ENTRY` (`UNINITIALIZED_CLASS_HASH`) so that a future `handle_revert` call could undo the mutation.
3. **Constructor invocation** — calls `select_execute_entry_point_func`, which returns `is_reverted`.
4. **Hard assertion** — `assert is_reverted = 0`. [1](#0-0) [2](#0-1) 

Step 4 is the root cause. When `is_reverted = 1`, the Cairo VM raises an assertion failure before `handle_revert` is ever called. The revert-log entries written in step 2 are therefore never consumed, and the `class_hash` mutation from step 1 is never undone. This is the direct analog of the NFT bug: state fields that should be restored on failure are silently left in their mutated form — except here the consequence is a proof abort rather than a stale on-chain value.

The same function is invoked by the **new** (non-deprecated) `execute_deploy` syscall handler: [3](#0-2) 

That handler also hard-codes `failure_flag=0` in the response, with a TODO acknowledging the gap: [4](#0-3) 

The comment inside `deploy_contract` itself reads `// TODO(Yoni, 1/1/2027): handle failures.`, confirming the missing rollback path is a known, unresolved gap in the production OS code. [5](#0-4) 

For comparison, the correct rollback path that **is** implemented for ordinary reverted entry points shows what should happen: a fresh revert log is created, `call_execute_syscalls` processes the syscall trace, and then `handle_revert` walks the log backwards to undo every `dict_update`: [6](#0-5) 

`handle_revert` itself correctly restores `class_hash` and `storage_ptr` but is simply never reached for the deploy path: [7](#0-6) 

---

### Impact Explanation

If the sequencer's off-chain execution model (which does not run the Cairo OS program) allows a `deploy` syscall to return a failure response when the constructor reverts — and includes the outer transaction in a block — the OS Cairo program will abort at `assert is_reverted = 0` during proof generation. No valid STARK proof can be produced for that block. The block cannot be finalized on L1, and the network cannot advance past it: **total network shutdown**.

---

### Likelihood Explanation

Any unprivileged user can:
- Declare a Sierra class whose constructor unconditionally panics.
- Deploy a wrapper contract that calls the `deploy` syscall with that class hash.
- Submit a transaction invoking the wrapper.

The sequencer's execution engine (separate from the OS Cairo program) is expected to handle constructor failures gracefully and return `failure_flag=1` from the deploy syscall, allowing the outer transaction to succeed. The TODO comment `// TODO(Yoni, 1/1/2026): support failures.` in `execute_deploy` confirms the OS has not yet been aligned with this expected behavior. The discrepancy between the sequencer's execution model and the OS proof program is the exploitable gap.

---

### Recommendation

In `deploy_contract`, check `is_reverted` after the constructor call. If `is_reverted != 0`:
1. Call `handle_revert` with the revert log accumulated during the constructor's execution to undo the `class_hash` mutation and any storage writes made by the constructor.
2. Return a failure response from `execute_deploy` (`failure_flag=1`) instead of asserting.
3. Remove the `assert is_reverted = 0` hard assertion.

This mirrors the pattern already used in `execute_entry_point` for ordinary reverted calls.

---

### Proof of Concept

1. Declare class `C` whose constructor body is `panic(array![])` (always reverts).
2. Declare class `W` (wrapper) whose `__execute__` calls `deploy(class_hash=C, ...)`.
3. Deploy `W` and submit a transaction invoking `W.__execute__`.
4. The sequencer simulates: deploy syscall returns `failure_flag=1`; outer transaction succeeds; block is produced.
5. The OS Cairo program processes the block: `deploy_contract` is entered, constructor returns `is_reverted=1`, `assert is_reverted = 0` fires.
6. Proof generation aborts; the block cannot be finalized; the network halts. [8](#0-7)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L28-29)
```text
// TODO(Yoni, 1/1/2027): handle failures.
func deploy_contract{
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L56-66)
```text
    tempvar new_state_entry = new StateEntry(
        class_hash=constructor_execution_context.class_hash,
        storage_ptr=state_entry.storage_ptr,
        nonce=0,
    );

    dict_update{dict_ptr=contract_state_changes}(
        key=contract_address,
        prev_value=cast(state_entry, felt),
        new_value=cast(new_state_entry, felt),
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L70-92)
```text
    assert [revert_log] = RevertLogEntry(
        selector=CHANGE_CONTRACT_ENTRY,
        value=constructor_execution_context.execution_info.caller_address,
    );
    let revert_log = &revert_log[1];

    assert [revert_log] = RevertLogEntry(
        selector=CHANGE_CLASS_ENTRY, value=UNINITIALIZED_CLASS_HASH
    );
    let revert_log = &revert_log[1];

    // Invoke the contract constructor.
    let (is_reverted, retdata_size, retdata, _is_deprecated) = select_execute_entry_point_func(
        block_context=block_context, execution_context=constructor_execution_context
    );

    // Entries before this point belong to the deployed contract.
    assert [revert_log] = RevertLogEntry(selector=CHANGE_CONTRACT_ENTRY, value=contract_address);
    let revert_log = &revert_log[1];

    // The deprecated deploy syscalls do not support reverts.
    assert is_reverted = 0;
    return (retdata_size=retdata_size, retdata=retdata);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L527-531)
```text
    with remaining_gas {
        let (retdata_size, retdata) = deploy_contract(
            block_context=block_context, constructor_execution_context=constructor_execution_context
        );
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L538-539)
```text
    // TODO(Yoni, 1/1/2026): support failures.
    assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L288-320)
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

    if (is_reverted != FALSE) {
        handle_revert(
            contract_address=execution_context.execution_info.contract_address,
            revert_log_end=revert_log,
        );
        // Restore the original revert log and outputs.
        let revert_log = orig_revert_log;
        let outputs = orig_outputs;
        return (
            is_reverted=is_reverted, retdata_size=retdata_end - retdata_start, retdata=retdata_start
        );
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/revert.cairo (L37-71)
```text
func handle_revert{contract_state_changes: DictAccess*}(
    contract_address, revert_log_end: RevertLogEntry*
) {
    alloc_locals;

    local state_entry: StateEntry*;

    %{ PrepareStateEntryForRevert %}

    let class_hash = state_entry.class_hash;
    let storage_ptr = state_entry.storage_ptr;
    with class_hash, storage_ptr, revert_log_end {
        revert_contract_changes();
    }

    dict_update{dict_ptr=contract_state_changes}(
        key=contract_address,
        prev_value=cast(state_entry, felt),
        new_value=cast(
            new StateEntry(class_hash=class_hash, storage_ptr=storage_ptr, nonce=state_entry.nonce),
            felt,
        ),
    );

    // `revert_contract_changes()` stops where
    // `revert_log_end[0].selector == CHANGE_CONTRACT_ENTRY`.
    tempvar next_contract_address = revert_log_end[0].value;

    if (next_contract_address == CONTRACT_ADDRESS_UPPER_BOUND) {
        // Finish backward processing: this entry marks the beginning of the revert log.
        return ();
    }

    return handle_revert(contract_address=next_contract_address, revert_log_end=revert_log_end);
}
```
