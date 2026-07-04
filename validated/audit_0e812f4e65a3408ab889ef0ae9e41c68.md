### Title
Reverted Sub-Call `send_message_to_l1` Outputs Are Not Rolled Back, Enabling Phantom L1 Messages — (File: `execution/syscall_impls.cairo`, `execution/revert.cairo`)

---

### Summary

The StarkNet OS revert mechanism rolls back `contract_state_changes` (storage writes, class changes) via an explicit revert log, but does **not** roll back the `outputs` implicit argument (`OsCarriedOutputs`, which carries `messages_to_l1`). When a sub-call emits an L2→L1 message via `send_message_to_l1` and then reverts, the message is permanently written into the OS output segment and committed to L1, while the corresponding L2 state changes are undone. This creates an exploitable inconsistency between L2 state and L1 messages.

---

### Finding Description

**Root cause — `execute_send_message_to_l1` advances `outputs` with no revert path:**

In `syscall_impls.cairo`, `execute_send_message_to_l1` writes the message header and payload directly into the `outputs.messages_to_l1` segment and advances the pointer: [1](#0-0) 

The `outputs` variable is an implicit argument threaded through the entire call stack. Once advanced, there is no mechanism to restore it.

**Root cause — `contract_call_helper` does not restore `outputs` on revert:**

`contract_call_helper` calls `select_execute_entry_point_func` (which internally runs the callee's syscalls, including any `send_message_to_l1` calls). If the callee reverts (`is_reverted != FALSE`), the function writes a failure response but does **not** restore `outputs` to its pre-call value: [2](#0-1) 

**Root cause — the revert log covers only state changes, not message outputs:**

`revert.cairo` defines `RevertLogEntry` as tracking only storage writes (`[storage_key, old_value]`) and class changes (`CHANGE_CLASS_ENTRY`). The `handle_revert` function processes only `contract_state_changes`: [3](#0-2) [4](#0-3) 

`outputs` (`OsCarriedOutputs`) is never mentioned in the revert log and is never restored.

**The `OsCarriedOutputs` struct carries both message queues:** [5](#0-4) 

These are serialized directly into the OS output segment and committed to L1: [6](#0-5) 

**The `execute_call_contract` syscall exposes this to any caller:** [7](#0-6) 

A caller contract receives `failure_flag=1` in the response header and can choose to continue execution, allowing the top-level transaction to succeed while the phantom message remains in the output.

---

### Impact Explanation

**Critical — Direct loss of funds.**

The OS output's `messages_to_l1` segment is consumed by the L1 StarkNet core contract, which records message hashes and allows L1 contracts to call `consumeMessageFromL2`. If a phantom message encodes a bridge withdrawal (e.g., "release X tokens to address Y"), the L1 bridge will process the withdrawal without any corresponding L2 token burn. Funds are drained from L1 with no L2 debit — a direct, permanent loss of funds.

---

### Likelihood Explanation

**High.** The attack requires only:
1. Deploying two contracts (no privileged access).
2. Crafting a sub-call that emits a `send_message_to_l1` and then reverts.
3. The outer contract handling the `failure_flag=1` response and continuing.

This is fully within the capability of any unprivileged L2 transaction sender. The attack is deterministic and does not require race conditions, timing, or operator cooperation.

---

### Recommendation

Before each sub-call in `contract_call_helper`, save the current `outputs` pointer. If the callee reverts (`is_reverted != FALSE`), restore `outputs` to the saved value, discarding any messages emitted during the reverted call. This is directly analogous to how `contract_state_changes` are rolled back via the revert log — the same two-phase pattern (record pre-state, restore on failure) must be applied to `outputs`.

Concretely: add `outputs` to the set of resources tracked by the revert mechanism, or save/restore the `messages_to_l1` and `messages_to_l2` pointers around every `contract_call_helper` invocation when `is_reverted` is true.

---

### Proof of Concept

1. Attacker deploys **Contract B** (inner): calls `send_message_to_l1` with a crafted withdrawal payload (e.g., `to_address = L1_bridge`, `payload = [attacker_l1_address, amount]`), then explicitly reverts.
2. Attacker deploys **Contract A** (outer): calls Contract B via `call_contract`, reads `failure_flag=1` from the response header, and returns successfully.
3. Attacker submits an invoke transaction calling Contract A.
4. OS executes: Contract A → Contract B → `send_message_to_l1` (advances `outputs.messages_to_l1`) → Contract B reverts → `handle_revert` undoes Contract B's storage changes but **does not restore `outputs`** → Contract A continues → transaction succeeds.
5. OS output includes the phantom message from Contract B in `messages_to_l1`.
6. L1 StarkNet core records the message hash as available.
7. Attacker calls `consumeMessageFromL2` on the L1 bridge, triggering a withdrawal.
8. L1 bridge releases funds to the attacker's L1 address. No L2 tokens were burned. Funds are permanently lost from the L1 bridge.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L170-234)
```text
func execute_call_contract{
    range_check_ptr,
    syscall_ptr: felt*,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    revert_log: RevertLogEntry*,
    outputs: OsCarriedOutputs*,
}(block_context: BlockContext*, caller_execution_context: ExecutionContext*) {
    let request = cast(syscall_ptr + RequestHeader.SIZE, CallContractRequest*);
    let (success, remaining_gas) = reduce_syscall_base_gas(
        specific_base_gas_cost=CALL_CONTRACT_GAS_COST, request_struct_size=CallContractRequest.SIZE
    );
    if (success == FALSE) {
        // Not enough gas to execute the syscall.
        return ();
    }
    if (request.selector == EXECUTE_ENTRY_POINT_SELECTOR) {
        write_failure_response(remaining_gas=remaining_gas, failure_felt=ERROR_INVALID_ARGUMENT);
        return ();
    }

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

    // Entries before this point belong to the callee.
    assert [revert_log] = RevertLogEntry(
        selector=CHANGE_CONTRACT_ENTRY, value=request.contract_address
    );
    let revert_log = &revert_log[1];

    return ();
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L412-449)
```text
}(remaining_gas: felt, block_context: BlockContext*, execution_context: ExecutionContext*) {
    with remaining_gas {
        let (is_reverted, retdata_size, retdata, _is_deprecated) = select_execute_entry_point_func(
            block_context=block_context, execution_context=execution_context
        );
    }

    if (is_reverted != FALSE) {
        // Append `ERROR_ENTRY_POINT_FAILED` to the retdata.
        assert retdata[retdata_size] = ERROR_ENTRY_POINT_FAILED;
        tempvar retdata_size = retdata_size + 1;
    } else {
        ap += 2;  // Align the stack to avoid revoked references.
        tempvar retdata_size = retdata_size;
    }

    let response_header = cast(syscall_ptr, ResponseHeader*);
    let syscall_ptr = syscall_ptr + ResponseHeader.SIZE;

    // Write the response header.
    with_attr error_message("Predicted gas costs are inconsistent with the actual execution.") {
        assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
    }

    let response = cast(syscall_ptr, CallContractResponse*);
    // Advance syscall pointer to the next syscall.
    let syscall_ptr = syscall_ptr + CallContractResponse.SIZE;

    %{ CheckNewCallContractResponse %}

    // Write the response.
    relocate_segment(src_ptr=response.retdata_start, dest_ptr=retdata);
    assert [response] = CallContractResponse(
        retdata_start=retdata, retdata_end=retdata + retdata_size
    );

    return ();
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L1362-1371)
```text
    assert [outputs.messages_to_l1] = MessageToL1Header(
        from_address=contract_address, to_address=request.to_address, payload_size=payload_size
    );
    memcpy(
        dst=outputs.messages_to_l1 + MessageToL1Header.SIZE, src=payload_start, len=payload_size
    );
    let (outputs) = os_carried_outputs_new(
        messages_to_l1=outputs.messages_to_l1 + MessageToL1Header.SIZE + payload_size,
        messages_to_l2=outputs.messages_to_l2,
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/revert.cairo (L19-24)
```text
struct RevertLogEntry {
    // Either the storage key, CHANGE_CONTRACT_ENTRY or CHANGE_CLASS_ENTRY.
    selector: felt,
    // The relevant (old) value.
    value: felt,
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/revert.cairo (L37-60)
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

```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/output.cairo (L73-76)
```text
struct OsCarriedOutputs {
    messages_to_l1: MessageToL1Header*,
    messages_to_l2: MessageToL2Header*,
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/output.cairo (L176-198)
```text
func serialize_messages{output_ptr: felt*}(
    initial_carried_outputs: OsCarriedOutputs*, final_carried_outputs: OsCarriedOutputs*
) {
    let messages_to_l1_segment_size = (
        final_carried_outputs.messages_to_l1 - initial_carried_outputs.messages_to_l1
    );
    serialize_word(messages_to_l1_segment_size);

    // Relocate 'messages_to_l1_segment' to the correct place in the output segment.
    relocate_segment(src_ptr=initial_carried_outputs.messages_to_l1, dest_ptr=output_ptr);
    let output_ptr = cast(final_carried_outputs.messages_to_l1, felt*);

    let messages_to_l2_segment_size = (
        final_carried_outputs.messages_to_l2 - initial_carried_outputs.messages_to_l2
    );
    serialize_word(messages_to_l2_segment_size);

    // Relocate 'messages_to_l2_segment' to the correct place in the output segment.
    relocate_segment(src_ptr=initial_carried_outputs.messages_to_l2, dest_ptr=output_ptr);
    let output_ptr = cast(final_carried_outputs.messages_to_l2, felt*);

    return ();
}
```
