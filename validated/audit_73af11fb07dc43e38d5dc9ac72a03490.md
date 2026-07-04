### Title
L2→L1 Messages from Reverted Sub-Calls Not Rolled Back in Revert Log — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/revert.cairo`)

---

### Summary

The StarkNet OS revert log mechanism tracks storage writes and class hash changes for rollback on sub-call reversion, but it has **no entry type for L2→L1 messages**. When a sub-call reverts, `handle_revert` restores storage and class state, but the `outputs.messages_to_l1` pointer — already advanced by `send_message_to_l1` syscalls made during the reverted call — is never reset. These phantom messages are permanently included in the block output and can be consumed on L1.

---

### Finding Description

**Root cause — `revert.cairo`, lines 9–24:**

The `RevertLogEntry` struct supports exactly three entry types:

```cairo
// 1. contract address separator: [CHANGE_CONTRACT_ENTRY, contact_address]
// 2. change class entry:         [CHANGE_CLASS_ENTRY, old_class_hash]
// 3. storage write entry:        [storage_key, old_value]
struct RevertLogEntry {
    selector: felt,
    value: felt,
}
```

There is no entry type for L2→L1 messages. The `handle_revert` function (`revert.cairo`, lines 37–71) only processes these three types; it restores `class_hash` and `storage_ptr` but has no mechanism to roll back the `outputs.messages_to_l1` pointer.

**Propagation — `syscall_impls.cairo`, lines 404–449 (`contract_call_helper`):**

```cairo
func contract_call_helper{
    ...
    outputs: OsCarriedOutputs*,
}(remaining_gas: felt, block_context: BlockContext*, execution_context: ExecutionContext*) {
    with remaining_gas {
        let (is_reverted, retdata_size, retdata, _is_deprecated) = select_execute_entry_point_func(
            block_context=block_context, execution_context=execution_context
        );
    }
    // `outputs` is NOT reset here even when is_reverted != FALSE.
    ...
}
```

`outputs` is an implicit argument threaded through `select_execute_entry_point_func`. Any `send_message_to_l1` syscalls executed during the sub-call advance `outputs.messages_to_l1`. After the sub-call returns with `is_reverted != FALSE`, `contract_call_helper` writes the failure response but **never restores the pre-call value of `outputs`**. The advanced pointer is returned to the caller as the new canonical `outputs`.

**Emission path — `execute_syscalls.cairo`, lines 331–341:**

```cairo
if (selector == SEND_MESSAGE_TO_L1_SELECTOR) {
    execute_send_message_to_l1(
        contract_address=execution_context.execution_info.contract_address
    );
    ...
}
```

`execute_send_message_to_l1` takes `outputs: OsCarriedOutputs*` as an implicit argument and advances `outputs.messages_to_l1`. Because `outputs` is implicit, the advancement is visible to all callers up the stack.

**Block output — `output.cairo`, lines 176–197 (`serialize_messages`):**

The final block output serializes the entire range `[initial_carried_outputs.messages_to_l1, final_carried_outputs.messages_to_l1)`. Messages written during reverted sub-calls fall inside this range and are serialized unconditionally.

---

### Impact Explanation

L2→L1 messages included in the proven block output are accepted as valid by the L1 StarkNet core contract. Any L1 contract (e.g., a token bridge) that consumes messages from a specific L2 contract address will process these phantom messages. An attacker who controls an L2 contract trusted by an L1 bridge can emit a withdrawal message, revert the sub-call, and still have the message accepted on L1 — constituting **direct loss of funds** (Critical).

---

### Likelihood Explanation

**Medium.** The attack is reachable by any unprivileged transaction sender who can deploy an L2 contract. Exploitation requires that the attacker's L2 contract address is trusted by an L1 consumer (e.g., a bridge the attacker deployed or a bridge that accepts messages from arbitrary L2 addresses). No privileged role, leaked key, or external dependency compromise is required. The on-chain proof guarantees the block is valid, so L1 has no way to distinguish phantom messages from legitimate ones.

---

### Recommendation

Add a message-rollback mechanism to the revert log. Before writing an L2→L1 message, record the current `outputs.messages_to_l1` pointer (or a sentinel entry) in the revert log. In `handle_revert`, restore the `outputs.messages_to_l1` pointer to its pre-call value when processing a reverted sub-call. Alternatively, save and explicitly restore the `outputs` pointer in `contract_call_helper` when `is_reverted != FALSE`, analogous to how `contract_state_changes` is restored via the revert log.

---

### Proof of Concept

1. Attacker deploys L2 contract `MaliciousContract` at address `A`. An L1 bridge trusts messages from `A`.
2. Attacker sends an invoke transaction calling `MaliciousContract.__execute__`.
3. Inside `__execute__`, `MaliciousContract` calls `send_message_to_l1(to_address=L1_BRIDGE, payload=[WITHDRAW, victim_amount])`, advancing `outputs.messages_to_l1`.
4. `MaliciousContract` then calls a sub-contract that panics, causing the sub-call to revert.
5. `handle_revert` (`revert.cairo:37`) restores storage/class state but does **not** reset `outputs.messages_to_l1`.
6. The outer transaction succeeds; the block is proven and submitted to L1.
7. `serialize_messages` (`output.cairo:176`) includes the phantom withdrawal message in the proven output.
8. The L1 bridge processes the message and releases `victim_amount` to the attacker. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/revert.cairo (L9-24)
```text
// Represents an entry of the revert log, which can be either:
// 1. contract address separator:
//   [CHANGE_CONTRACT_ENTRY, contact_address] - indicates that the preceding entries in the log
//   refer to the given `contract_address`.
// 2. change class entry - used to revert changes of class hash (due to deploy or replace_class):
//   [CHANGE_CLASS_ENTRY, old_class_hash]
// 3. storage write entry - used to revert changes to the contract's storage:
//   [storage_key, old_value]
//
// The first entry of the revert log is [CHANGE_CONTRACT_ENTRY, CONTRACT_ADDRESS_UPPER_BOUND].
struct RevertLogEntry {
    // Either the storage key, CHANGE_CONTRACT_ENTRY or CHANGE_CLASS_ENTRY.
    selector: felt,
    // The relevant (old) value.
    value: felt,
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L404-449)
```text
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/output.cairo (L176-197)
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
```
