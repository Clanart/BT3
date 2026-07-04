### Title
L2→L1 Messages Emitted in Reverted Sub-Calls Are Not Rolled Back — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/revert.cairo`)

---

### Summary

The StarkNet OS revert mechanism (`handle_revert`) undoes storage writes and class-hash changes when a sub-call reverts, but it never restores the `outputs.messages_to_l1` pointer. Any L2→L1 message appended by `execute_send_message_to_l1` during a reverted sub-call is permanently included in the block's OS output and will be processed on L1, while the corresponding L2 state changes (e.g., token burns) are rolled back. This creates a direct double-spend path reachable by any unprivileged transaction sender.

---

### Finding Description

**Revert log scope — `revert.cairo`**

`handle_revert` processes the revert log backwards and calls `revert_contract_changes`. The log entries it recognises are:

| Entry type | Selector value | Effect |
|---|---|---|
| Contract separator | `CHANGE_CONTRACT_ENTRY` | Switch active contract |
| Class change | `CHANGE_CLASS_ENTRY` | Restore old class hash |
| Storage write | any storage key | Restore old storage value | [1](#0-0) 

`outputs` (`OsCarriedOutputs*`) is **never** passed to `handle_revert` and is **not** part of the revert log. There is no mechanism to roll back the `messages_to_l1` pointer. [2](#0-1) 

**Message emission — `syscall_impls.cairo`**

`execute_send_message_to_l1` advances `outputs.messages_to_l1` unconditionally whenever the syscall has sufficient gas: [3](#0-2) 

**Sub-call revert path — `syscall_impls.cairo`**

`contract_call_helper` calls `select_execute_entry_point_func` with `outputs` as an implicit argument. After the call returns (whether reverted or not), the `outputs` pointer reflects whatever messages were appended during the sub-call. `handle_revert` is invoked for storage/class rollback but `outputs` is left advanced: [4](#0-3) 

**Top-level execute cannot revert, but sub-calls can**

`execute_invoke_function_transaction` uses `non_reverting_select_execute_entry_point_func` for the top-level `__execute__` call (which asserts `is_reverted = 0`). Sub-calls made via `call_contract` use the reverting variant, so sub-calls can revert while the outer transaction succeeds. [5](#0-4) 

---

### Impact Explanation

**Impact: Critical — Direct loss of funds.**

An L2 bridge contract that follows the common pattern:

1. Validate caller balance
2. Call `send_message_to_l1` (withdrawal message to L1)
3. Burn/deduct tokens from caller's storage

is exploitable. An attacker crafts a call that causes step 3 to revert (e.g., by supplying calldata that triggers an assertion failure after the message is sent, or by exhausting gas precisely after the message syscall). The OS output includes the L2→L1 withdrawal message (step 2 is not rolled back), while the L2 token balance is restored (step 3 is rolled back via the revert log). The L1 bridge contract processes the withdrawal, releasing real funds, while the attacker retains their L2 tokens — a direct double-spend.

---

### Likelihood Explanation

**Likelihood: High.**

- Any unprivileged user can deploy a contract and submit an invoke transaction.
- The attacker only needs to find (or construct) a call path where `send_message_to_l1` executes before a revertible state update.
- Many real bridge patterns send the L2→L1 message before or alongside the token burn; the ordering is not enforced by the OS.
- No privileged role, leaked key, or network-level attack is required.

---

### Recommendation

The `outputs` pointer (specifically `messages_to_l1`) must be saved before entering a sub-call and restored if the sub-call reverts. Concretely:

1. Add `outputs_before` and `outputs_after` fields to the revert log, or save/restore the `outputs` pointer inside `select_execute_entry_point_func` when `is_reverted != 0`.
2. Alternatively, record each `send_message_to_l1` call in the revert log (analogous to storage writes) so `handle_revert` can truncate the message segment.

This mirrors how Ethereum reverts all side-effects — including logs — when a sub-call reverts.

---

### Proof of Concept

1. Attacker deploys `MaliciousCaller` on L2.
2. Attacker submits an invoke transaction calling `MaliciousCaller.__execute__`.
3. `MaliciousCaller.__execute__` calls `BridgeContract.withdraw(amount=X)` via `call_contract`.
4. Inside `BridgeContract.withdraw`:
   - `send_message_to_l1(to_address=L1Bridge, payload=[attacker_l1_addr, X])` — message appended to `outputs.messages_to_l1`. [6](#0-5) 
   - `storage_write(balance_key, balance - X)` — token burn recorded in revert log. [7](#0-6) 
   - Attacker triggers a revert (e.g., crafted calldata causes `assert 0 = 1`).
5. `handle_revert` runs: restores `balance_key` to `balance` (token burn undone). `outputs.messages_to_l1` is **not** restored. [1](#0-0) 
6. The OS block output contains the withdrawal message. L1 bridge releases `X` ETH/tokens to `attacker_l1_addr`.
7. Attacker retains `X` tokens on L2 (balance restored) and receives `X` tokens on L1 — net gain of `X` tokens at the bridge's expense.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L638-686)
```text
func execute_storage_write{
    range_check_ptr,
    syscall_ptr: felt*,
    contract_state_changes: DictAccess*,
    revert_log: RevertLogEntry*,
}(contract_address: felt) {
    alloc_locals;
    let request = cast(syscall_ptr + RequestHeader.SIZE, StorageWriteRequest*);

    // Reduce gas.
    let success = reduce_syscall_gas_and_write_response_header(
        total_gas_cost=STORAGE_WRITE_GAS_COST, request_struct_size=StorageWriteRequest.SIZE
    );
    if (success == FALSE) {
        // Not enough gas to execute the syscall.
        return ();
    }

    local prev_value: felt;
    local state_entry: StateEntry*;
    %{ WriteSyscallResult %}

    // Update the contract's storage.
    static_assert StorageWriteRequest.SIZE == 3;
    assert request.reserved = 0;
    tempvar storage_ptr = state_entry.storage_ptr;
    tempvar storage_key = request.key;
    assert [storage_ptr] = DictAccess(
        key=storage_key, prev_value=prev_value, new_value=request.value
    );
    let storage_ptr = storage_ptr + DictAccess.SIZE;

    assert [revert_log] = RevertLogEntry(selector=storage_key, value=prev_value);
    let revert_log = &revert_log[1];

    // Update the state.
    dict_update{dict_ptr=contract_state_changes}(
        key=contract_address,
        prev_value=cast(state_entry, felt),
        new_value=cast(
            new StateEntry(
                class_hash=state_entry.class_hash, storage_ptr=storage_ptr, nonce=state_entry.nonce
            ),
            felt,
        ),
    );

    return ();
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L1346-1374)
```text
func execute_send_message_to_l1{range_check_ptr, syscall_ptr: felt*, outputs: OsCarriedOutputs*}(
    contract_address: felt
) {
    alloc_locals;
    let request = cast(syscall_ptr + RequestHeader.SIZE, SendMessageToL1Request*);
    let success = reduce_syscall_gas_and_write_response_header(
        total_gas_cost=SEND_MESSAGE_TO_L1_GAS_COST, request_struct_size=SendMessageToL1Request.SIZE
    );
    if (success == FALSE) {
        // Not enough gas to execute the syscall.
        return ();
    }

    tempvar payload_start = request.payload_start;
    tempvar payload_size = request.payload_end - payload_start;

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

    return ();
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L338-365)
```text
    local is_reverted;
    %{ IsReverted %}
    check_is_reverted(is_reverted);
    if (is_reverted == FALSE) {
        // Execute only non-reverted transactions.
        with remaining_gas {
            cap_remaining_gas(max_gas=EXECUTE_MAX_SIERRA_GAS);
            non_reverting_select_execute_entry_point_func(
                block_context=block_context, execution_context=updated_tx_execution_context
            );
        }
    } else {
        // Align the stack with the `if` branch to avoid revoked references.
        tempvar range_check_ptr = range_check_ptr;
        tempvar remaining_gas = remaining_gas;
        tempvar builtin_ptrs = builtin_ptrs;
        tempvar contract_state_changes = contract_state_changes;
        tempvar contract_class_changes = contract_class_changes;
        tempvar outputs = outputs;
        tempvar _dummy_return_value: non_reverting_select_execute_entry_point_func.Return;
    }

    // Charge fee.
    charge_fee(block_context=block_context, tx_execution_context=updated_tx_execution_context);

    %{ EndTx %}

    return ();
```
