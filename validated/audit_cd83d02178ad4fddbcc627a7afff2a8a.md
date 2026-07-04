### Title
L2→L1 Messages Emitted by Reverted Sub-Calls Are Not Rolled Back from OS Output — (`execution/syscall_impls.cairo`)

---

### Summary

When a sub-call reverts inside a transaction, the OS revert mechanism only rolls back storage writes and class-hash changes. L2→L1 messages (`send_message_to_l1` syscalls) that were emitted by the reverted sub-call are **not** removed from `outputs.messages_to_l1`. They remain in the OS output, are proven on-chain, and can be consumed by L1 contracts — even though the corresponding L2 state changes were fully undone.

---

### Finding Description

**Revert log covers only storage and class-hash changes.**

`handle_revert` in `revert.cairo` processes the revert log backwards and only handles two entry types:

- `CHANGE_CLASS_ENTRY` — reverts a `replace_class` call
- storage-key entries — reverts a `storage_write` call [1](#0-0) 

There is no entry type for L2→L1 messages. The `outputs: OsCarriedOutputs*` implicit argument is never saved or restored during revert processing.

**`contract_call_helper` does not roll back `outputs` on revert.**

When a sub-call reverts, `contract_call_helper` receives `is_reverted = 1` from `select_execute_entry_point_func`. It appends an error to the return data and writes the failure flag to the response header, but it takes no action to restore the `outputs` pointer: [2](#0-1) 

The `outputs` implicit argument — which carries the advancing `messages_to_l1` pointer — is left at whatever position it reached during the reverted sub-call's execution.

**`execute_send_message_to_l1` advances `outputs.messages_to_l1` unconditionally.**

`execute_syscalls` dispatches `SEND_MESSAGE_TO_L1_SELECTOR` to `execute_send_message_to_l1`, passing `outputs` as an implicit argument: [3](#0-2) 

The function writes a `MessageToL1Header` into the `outputs.messages_to_l1` segment and advances the pointer (following the same pattern as `consume_l1_to_l2_message`): [4](#0-3) 

Because the pointer is never restored after a sub-call revert, the message written by the reverted callee persists in the segment.

**`serialize_messages` serialises the full segment including orphaned messages.**

The OS output serialisation computes the segment size as the difference between the final and initial `messages_to_l1` pointers and relocates the entire segment to the output: [5](#0-4) 

Any message written by a reverted sub-call is therefore included in the proven output and becomes consumable on L1.

---

### Impact Explanation

**Critical — Direct loss of funds.**

The L1 StarkNet core contract verifies the STARK proof and uses `messages_to_l1` to allow L1 contracts to consume messages. A message that appears in the proven output can be consumed regardless of whether the L2 state change that was supposed to accompany it (e.g., burning a bridged token) was actually committed.

Concretely: an attacker who controls an L2 contract that is trusted by an L1 bridge can:

1. Call `send_message_to_l1` to instruct the L1 bridge to release ETH/ERC-20 tokens.
2. Immediately revert the sub-call (e.g., by calling a function that always fails).
3. The L2 token burn is rolled back; the L2 balance is restored.
4. The L2→L1 message survives in the OS output.
5. The attacker consumes the message on L1 and receives the bridged asset.

The attacker ends up with both the L2 tokens and the L1 asset — a direct double-spend.

---

### Likelihood Explanation

Any unprivileged transaction sender can deploy a contract and trigger this flow. No privileged role, leaked key, or external dependency is required. The only prerequisite is that the attacker controls an L2 contract whose address is trusted by some L1 contract (e.g., a custom bridge or any L1 contract that acts on L2→L1 messages from user-deployed contracts). The StarkNet native ETH bridge is the highest-value target, but any L1 contract that processes L2→L1 messages is affected.

---

### Recommendation

Track L2→L1 messages in the revert log, or snapshot and restore the `outputs.messages_to_l1` pointer when entering and exiting a revertible sub-call in `contract_call_helper`. Specifically:

- Before calling `select_execute_entry_point_func`, save `outputs.messages_to_l1`.
- If `is_reverted != FALSE`, restore `outputs` to the saved pointer before returning, discarding any messages written by the reverted callee.

This mirrors the existing pattern for storage writes, where the revert log records the previous value so it can be restored.

---

### Proof of Concept

1. Attacker deploys `MaliciousBridge` on L2 with:
   - `exploit()`: calls `send_message_to_l1(l1_bridge, withdraw_payload)`, then calls `self.always_revert()`.
   - `always_revert()`: always panics.
2. Attacker submits an invoke transaction calling `MaliciousBridge.exploit()`.
3. OS execution:
   - `execute_send_message_to_l1` runs → `MessageToL1Header` written to `outputs.messages_to_l1`; pointer advanced.
   - `always_revert()` sub-call reverts → `handle_revert` rolls back storage changes; `outputs` pointer **not** restored.
   - Top-level `exploit()` catches the inner failure and returns successfully.
4. Block is proven. The OS output contains the L2→L1 message.
5. On L1, the attacker calls `l1_bridge.withdraw()` with the message proof → ETH released.
6. On L2, no token burn occurred (the storage write was reverted). Attacker holds both L2 tokens and L1 ETH.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/revert.cairo (L75-101)
```text
func revert_contract_changes{
    class_hash: felt, storage_ptr: DictAccess*, revert_log_end: RevertLogEntry*
}() {
    alloc_locals;
    let revert_log_end = &revert_log_end[-1];

    tempvar selector = revert_log_end[0].selector;
    if (selector == CHANGE_CONTRACT_ENTRY) {
        // Change contract entries are handled by the caller.
        return ();
    }

    if (selector == CHANGE_CLASS_ENTRY) {
        // Change class entry.
        let class_hash = revert_log_end[0].value;
        return revert_contract_changes();
    }

    // Storage write entry.
    let storage_key = selector;
    let value = revert_log_end[0].value;
    local prev_value;
    %{ ReadStorageKeyForRevert %}
    assert storage_ptr[0] = DictAccess(key=storage_key, prev_value=prev_value, new_value=value);
    %{ WriteStorageKeyForRevert %}
    let storage_ptr = &storage_ptr[1];
    return revert_contract_changes();
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L402-449)
```text
// Executes the entry point and writes the corresponding response to the syscall_ptr.
// Assumes that syscall_ptr points at the response header.
func contract_call_helper{
    range_check_ptr,
    syscall_ptr: felt*,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    revert_log: RevertLogEntry*,
    outputs: OsCarriedOutputs*,
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L331-341)
```text
    if (selector == SEND_MESSAGE_TO_L1_SELECTOR) {
        execute_send_message_to_l1(
            contract_address=execution_context.execution_info.contract_address
        );
        %{ OsLoggerExitSyscall %}
        return execute_syscalls(
            block_context=block_context,
            execution_context=execution_context,
            syscall_ptr_end=syscall_ptr_end,
        );
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L491-518)
```text
func consume_l1_to_l2_message{outputs: OsCarriedOutputs*}(
    execution_context: ExecutionContext*, nonce: felt
) {
    assert_not_zero(execution_context.calldata_size);
    // The payload is the calldata without the from_address argument (which is the first).
    let payload: felt* = execution_context.calldata + 1;
    tempvar payload_size = execution_context.calldata_size - 1;

    tempvar execution_info = execution_context.execution_info;

    // Write the given transaction to the output.
    assert [outputs.messages_to_l2] = MessageToL2Header(
        from_address=[execution_context.calldata],
        to_address=execution_info.contract_address,
        nonce=nonce,
        selector=execution_info.selector,
        payload_size=payload_size,
    );

    let message_payload = cast(outputs.messages_to_l2 + MessageToL2Header.SIZE, felt*);
    memcpy(dst=message_payload, src=payload, len=payload_size);

    let (outputs) = os_carried_outputs_new(
        messages_to_l1=outputs.messages_to_l1,
        messages_to_l2=outputs.messages_to_l2 + MessageToL2Header.SIZE +
        outputs.messages_to_l2.payload_size,
    );
    return ();
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
