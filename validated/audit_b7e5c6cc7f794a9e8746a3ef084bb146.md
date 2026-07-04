### Title
Reverted L1 Handler Skips `consume_l1_to_l2_message`, Permanently Freezing L1 Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`execute_l1_handler_transaction` returns early when `is_reverted != FALSE` without calling `consume_l1_to_l2_message`. This means the L1-to-L2 message is never written to `outputs.messages_to_l2`, so the OS proof never signals to the L1 StarkNet core contract that the message was processed. The L1 contract therefore never marks the message as consumed, permanently freezing the ETH or tokens that were locked when the message was sent.

---

### Finding Description

In `execute_l1_handler_transaction`, the very first thing the function does after `StartTx` is check `is_reverted`:

```cairo
// transaction_impls.cairo L383-390
%{ StartTx %}
local is_reverted;
%{ IsReverted %}
// Skip the execution step for reverted transaction.
if (is_reverted != FALSE) {
    %{ EndTx %}
    return ();
}
``` [1](#0-0) 

When the branch is taken, the function returns immediately. The code that follows — including the critical call to `consume_l1_to_l2_message` — is entirely skipped:

```cairo
// transaction_impls.cairo L443-448 (never reached for reverted handlers)
consume_l1_to_l2_message(execution_context=tx_execution_context, nonce=nonce);
let remaining_gas = L1_HANDLER_L2_GAS_MAX_AMOUNT;
non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
    block_context=block_context, execution_context=tx_execution_context
);
``` [2](#0-1) 

`consume_l1_to_l2_message` is the sole function that writes an entry into `outputs.messages_to_l2`:

```cairo
// transaction_impls.cairo L502-517
assert [outputs.messages_to_l2] = MessageToL2Header(
    from_address=[execution_context.calldata],
    to_address=execution_info.contract_address,
    nonce=nonce,
    selector=execution_info.selector,
    payload_size=payload_size,
);
...
let (outputs) = os_carried_outputs_new(
    messages_to_l1=outputs.messages_to_l1,
    messages_to_l2=outputs.messages_to_l2 + MessageToL2Header.SIZE + ...,
);
``` [3](#0-2) 

`outputs.messages_to_l2` is then serialized by `serialize_messages` in `output.cairo` and included in the OS proof output that the L1 StarkNet core contract reads to determine which L1-to-L2 messages have been consumed:

```cairo
// output.cairo L188-195
let messages_to_l2_segment_size = (
    final_carried_outputs.messages_to_l2 - initial_carried_outputs.messages_to_l2
);
serialize_word(messages_to_l2_segment_size);
relocate_segment(src_ptr=initial_carried_outputs.messages_to_l2, dest_ptr=output_ptr);
let output_ptr = cast(final_carried_outputs.messages_to_l2, felt*);
``` [4](#0-3) 

Because the reverted L1 handler never writes to `messages_to_l2`, the L1 StarkNet core contract never receives proof that the message was consumed. The message remains in the "pending" state on L1 indefinitely, and the ETH or ERC-20 tokens locked for that message cannot be released through the normal flow.

The developer comment at line 392 acknowledges the incomplete handling of reverted L1 handlers (`"// TODO(Yoni): currently, the contract state is not fetched for reverted L1 handlers"`), but the missing `consume_l1_to_l2_message` call is a separate, more severe omission that was not flagged. [5](#0-4) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

When a user (or protocol) sends an L1-to-L2 message (e.g., a bridge deposit), the L1 StarkNet core contract locks the corresponding ETH or tokens and records the message hash. Those funds are released only when the OS proof includes the message in `messages_to_l2`, signalling that the L2 handler consumed it. If the L2 handler reverts and the OS omits the message from its output, the L1 contract never marks the message as consumed. The locked funds are permanently inaccessible through the normal protocol path. While StarkNet has a message-cancellation mechanism with a multi-day timeout, that is an emergency escape hatch, not a substitute for correct protocol behavior, and it requires the original L1 sender to initiate it — which may not always be possible (e.g., if the sender is a contract that does not implement cancellation).

---

### Likelihood Explanation

**High.**

L1 handler transactions revert for many ordinary reasons: the target contract does not implement the expected selector, the handler logic panics due to unexpected calldata, the contract has been upgraded and the handler signature changed, or gas is exhausted. None of these require attacker coordination. Any such revert silently drops the message from the OS output. An adversary can also deliberately craft an L1-to-L2 message targeting a contract whose handler is known to revert, causing the message to be permanently stuck.

---

### Recommendation

Even when an L1 handler transaction is reverted, the OS must still record the message consumption. The fix is to move the execution-context setup and `consume_l1_to_l2_message` call **before** the `is_reverted` branch, so the message is always written to `outputs.messages_to_l2` regardless of whether the handler succeeded. The entry-point execution itself should remain conditional on `is_reverted`. This mirrors the correct pattern used in `finalizeVaultEndedWithdrawals` in the reference report: sub-steps that affect external state (L1 message accounting) must not be skipped by a shortcut path.

---

### Proof of Concept

1. Alice sends 1 ETH from L1 to L2 via the StarkNet bridge, targeting contract `C` with selector `deposit`. The L1 core contract locks 1 ETH and records the message hash.
2. Contract `C` on L2 has been upgraded; its `deposit` handler now reverts unconditionally.
3. The sequencer picks up the L1 handler transaction, executes it, observes a revert, and sets `is_reverted = TRUE`.
4. `execute_l1_handler_transaction` hits the early-return branch at line 387–390 and returns without calling `consume_l1_to_l2_message`.
5. The OS proof's `messages_to_l2` segment does not contain Alice's message.
6. The L1 StarkNet core contract processes the proof and does not mark Alice's message as consumed.
7. Alice's 1 ETH remains locked in the L1 contract. The normal withdrawal path is permanently blocked. Alice must wait for the multi-day cancellation timeout and initiate a cancellation from L1 — if her L1 sender contract even supports it. [6](#0-5)

### Citations

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L502-517)
```text
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/output.cairo (L188-195)
```text
    let messages_to_l2_segment_size = (
        final_carried_outputs.messages_to_l2 - initial_carried_outputs.messages_to_l2
    );
    serialize_word(messages_to_l2_segment_size);

    // Relocate 'messages_to_l2_segment' to the correct place in the output segment.
    relocate_segment(src_ptr=initial_carried_outputs.messages_to_l2, dest_ptr=output_ptr);
    let output_ptr = cast(final_carried_outputs.messages_to_l2, felt*);
```
