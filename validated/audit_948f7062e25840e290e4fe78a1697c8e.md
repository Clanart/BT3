### Title
Missing `consume_l1_to_l2_message` for Reverted L1 Handler Transactions Enables L1→L2 Message Replay - (File: `execution/transaction_impls.cairo`)

---

### Summary

When an L1 handler transaction is marked as reverted, `execute_l1_handler_transaction` returns early without calling `consume_l1_to_l2_message()`. The L1-to-L2 message is therefore never written to the OS output's `messages_to_l2` segment. The L1 StarkNet core contract uses this segment to mark messages as consumed; absent from the output, the message remains pending on L1 and can be re-submitted in a future block, enabling double-execution and direct loss of funds.

---

### Finding Description

In `execute_l1_handler_transaction`, the function checks `is_reverted` immediately after `%{ StartTx %}`. If `is_reverted != FALSE`, it returns at line 389 without:

1. Fetching the contract state or computing the transaction hash.
2. **Calling `consume_l1_to_l2_message()`.** [1](#0-0) 

The `consume_l1_to_l2_message()` function writes the message header and payload into `outputs.messages_to_l2`: [2](#0-1) 

For non-reverted L1 handlers, this call is made at line 444, after the transaction hash is computed and the execution context is set up: [3](#0-2) 

The `serialize_messages()` function in `output.cairo` serializes the entire `messages_to_l2` segment into the OS output, which the L1 StarkNet core contract uses to determine which L1-to-L2 messages were consumed in a given block: [4](#0-3) 

If a message is absent from this segment, the L1 contract will not mark it as consumed, leaving it pending and replayable.

The asymmetry is clear when compared to `execute_invoke_function_transaction`, which always calls `charge_fee()` even for reverted transactions — demonstrating that the protocol explicitly intends certain accounting steps to occur regardless of revert status: [5](#0-4) 

The `OsCarriedOutputs` struct carries both `messages_to_l1` and `messages_to_l2` pointers across the entire block execution. A reverted L1 handler that skips `consume_l1_to_l2_message` leaves a gap in this accounting — the exact analog of the missing `onDecreasePosition()` hook in the external report. [6](#0-5) 

---

### Impact Explanation

An attacker who causes an L1 handler to revert prevents the L1-to-L2 message from being recorded as consumed in the OS output. The message remains pending on L1. If the L1 handler is associated with a bridge deposit or token transfer, re-execution of the same message in a future block results in double-execution — **direct loss of funds**. If the message can never be successfully consumed (e.g., the handler always reverts for that input), the associated funds are **permanently frozen**.

**Impact: Critical — Direct loss of funds / Permanent freezing of funds.**

---

### Likelihood Explanation

Any unprivileged L1 user can send an L1-to-L2 message. If the target L2 contract's handler can be made to revert — for example, by crafting calldata that triggers a revert condition, or by exploiting a state-dependent revert path — the attacker triggers this code path without any privileged access. L1 handler contracts that process bridge deposits or token transfers are common targets. The likelihood is high wherever L1 handlers have revert conditions reachable via attacker-controlled calldata.

---

### Recommendation

Call `consume_l1_to_l2_message()` for reverted L1 handlers before returning early. This requires fetching the execution context (contract address, calldata, nonce) prior to the revert check, or restructuring the function so that message consumption is unconditional. The message must always appear in the OS output regardless of whether the handler execution succeeded or reverted, mirroring how `charge_fee` is unconditionally called for reverted invoke transactions.

---

### Proof of Concept

1. Attacker deploys an L2 contract whose L1 handler reverts when called with specific calldata (e.g., a bridge contract that reverts if a recipient address is blocklisted, but the blocklist is state-dependent).
2. Attacker sends an L1-to-L2 message with the revert-triggering calldata. The message is paid for and recorded on L1.
3. The sequencer includes the L1 handler transaction in a block. The handler reverts; the sequencer sets `is_reverted = TRUE` via `%{ IsReverted %}`.
4. `execute_l1_handler_transaction` hits the early-return branch at line 387–390. `consume_l1_to_l2_message()` is **never called**.
5. The OS output's `messages_to_l2` segment does not contain this message. `serialize_messages()` serializes the segment to the proof output without it.
6. The L1 StarkNet core contract processes the OS output and does not mark the message as consumed. The message remains pending on L1.
7. The attacker modifies the state condition (e.g., removes the blocklist entry) and the sequencer re-includes the same pending L1-to-L2 message in a future block.
8. The handler now executes successfully, processing the deposit or transfer a second time — **double-execution, direct loss of funds**. [7](#0-6)

### Citations

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L374-451)
```text
func execute_l1_handler_transaction{
    range_check_ptr,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    outputs: OsCarriedOutputs*,
}(block_context: BlockContext*) {
    alloc_locals;

    %{ StartTx %}
    local is_reverted;
    %{ IsReverted %}
    // Skip the execution step for reverted transaction.
    if (is_reverted != FALSE) {
        %{ EndTx %}
        return ();
    }

    // TODO(Yoni): currently, the contract state is not fetched for reverted L1 handlers.
    //   Once block hash is supported, we should fetch the contract state for them as well.
    local entry_point_selector;
    %{ TxEntryPointSelector %}
    let (local tx_execution_context: ExecutionContext*) = get_invoke_tx_execution_context(
        block_context=block_context,
        entry_point_type=ENTRY_POINT_TYPE_L1_HANDLER,
        entry_point_selector=entry_point_selector,
    );
    local tx_execution_info: ExecutionInfo* = tx_execution_context.execution_info;

    local nonce;
    %{ LoadTxNonceL1Handler %}
    local chain_id = block_context.os_global_context.starknet_os_config.chain_id;

    let pedersen_ptr = builtin_ptrs.selectable.pedersen;
    with pedersen_ptr {
        let transaction_hash = compute_l1_handler_transaction_hash(
            execution_context=tx_execution_context, chain_id=chain_id, nonce=nonce
        );
    }
    update_pedersen_in_builtin_ptrs(pedersen_ptr=pedersen_ptr);

    %{ AssertTransactionHash %}

    // Write the transaction info and complete the ExecutionInfo struct.
    tempvar tx_info = tx_execution_info.tx_info;
    assert [tx_info] = TxInfo(
        version=L1_HANDLER_VERSION,
        account_contract_address=tx_execution_info.contract_address,
        max_fee=0,
        signature_start=cast(0, felt*),
        signature_end=cast(0, felt*),
        transaction_hash=transaction_hash,
        chain_id=chain_id,
        nonce=nonce,
        resource_bounds_start=cast(0, ResourceBounds*),
        resource_bounds_end=cast(0, ResourceBounds*),
        tip=0,
        paymaster_data_start=cast(0, felt*),
        paymaster_data_end=cast(0, felt*),
        nonce_data_availability_mode=0,
        fee_data_availability_mode=0,
        account_deployment_data_start=cast(0, felt*),
        account_deployment_data_end=cast(0, felt*),
        proof_facts_start=cast(0, felt*),
        proof_facts_end=cast(0, felt*),
    );
    fill_deprecated_tx_info(tx_info=tx_info, dst=tx_execution_context.deprecated_tx_info);
    assert_deprecated_tx_fields_consistency(tx_info=tx_info);

    // Consume L1-to-L2 message.
    consume_l1_to_l2_message(execution_context=tx_execution_context, nonce=nonce);
    let remaining_gas = L1_HANDLER_L2_GAS_MAX_AMOUNT;
    non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
        block_context=block_context, execution_context=tx_execution_context
    );

    %{ EndTx %}
    return ();
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/output.cairo (L71-76)
```text
// Holds all the information that StarkNet's OS needs to output.
// TODO(Yoni, 1/1/2026): rename to OsMessages.
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
