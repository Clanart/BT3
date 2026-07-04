### Title
L1→L2 Message Not Consumed When L1 Handler Is Reverted, Permanently Locking Bridged Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

In `execute_l1_handler_transaction`, the call to `consume_l1_to_l2_message` — which records the L1→L2 message in the OS output so L1 can mark it as consumed — is placed **inside** the non-reverted execution branch. When the sequencer marks an L1 handler as reverted (`is_reverted != FALSE`), the function returns early and `consume_l1_to_l2_message` is never called. The message therefore never appears in the OS output, L1 never marks it as consumed, and any funds bridged with that message are permanently locked in the L1 bridge contract.

---

### Finding Description

`execute_l1_handler_transaction` reads a hint-provided `is_reverted` flag and, if true, returns immediately:

```cairo
%{ StartTx %}
local is_reverted;
%{ IsReverted %}
// Skip the execution step for reverted transaction.
if (is_reverted != FALSE) {
    %{ EndTx %}
    return ();
}
``` [1](#0-0) 

Only when `is_reverted == FALSE` does execution continue to the critical call:

```cairo
// Consume L1-to-L2 message.
consume_l1_to_l2_message(execution_context=tx_execution_context, nonce=nonce);
``` [2](#0-1) 

`consume_l1_to_l2_message` writes the message header to `outputs.messages_to_l2`:

```cairo
assert [outputs.messages_to_l2] = MessageToL2Header(
    from_address=[execution_context.calldata],
    to_address=execution_info.contract_address,
    nonce=nonce,
    selector=execution_info.selector,
    payload_size=payload_size,
);
``` [3](#0-2) 

This `messages_to_l2` segment is serialized into the OS output by `serialize_messages`:

```cairo
let messages_to_l2_segment_size = (
    final_carried_outputs.messages_to_l2 - initial_carried_outputs.messages_to_l2
);
serialize_word(messages_to_l2_segment_size);
relocate_segment(src_ptr=initial_carried_outputs.messages_to_l2, dest_ptr=output_ptr);
``` [4](#0-3) 

The L1 verifier contract reads this output to determine which L1→L2 messages have been consumed. If a message is absent from the output, L1 never marks it as consumed, and the bridged assets remain locked in the L1 bridge contract indefinitely.

Contrast this with `execute_invoke_function_transaction`, where `charge_fee` is called **unconditionally** after the reverted/non-reverted branch — demonstrating that the pattern of performing critical accounting outside the revert guard is known and intentional for other transaction types, but was not applied here:

```cairo
// Charge fee.
charge_fee(block_context=block_context, tx_execution_context=updated_tx_execution_context);
``` [5](#0-4) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

When a user deposits tokens from L1 to L2 via a bridge, the L1 contract locks the tokens and emits an L1→L2 message. If the corresponding L2 L1 handler reverts (e.g., due to out-of-gas, a contract bug, or malformed calldata), `consume_l1_to_l2_message` is skipped. The message is absent from the OS output. L1 never marks the message as consumed. The locked tokens in the L1 bridge contract cannot be released through the normal flow and are permanently frozen.

---

### Likelihood Explanation

L1 handler reverts are a normal, expected protocol event (out-of-gas, contract panics, invalid calldata). Any L1→L2 message whose handler reverts triggers this path. An unprivileged user can deliberately craft a message whose calldata causes the target L2 contract to revert, locking their own or others' bridged funds. No privileged access is required — only the ability to send an L1→L2 message, which is a public protocol operation.

---

### Recommendation

Move `consume_l1_to_l2_message` **outside** the `is_reverted` guard, analogous to how `charge_fee` is placed outside the revert branch in `execute_invoke_function_transaction`. The message must be recorded in the OS output regardless of whether the handler execution succeeded or failed, so that L1 can always mark the message as consumed and release or refund the associated funds:

```cairo
// Consume L1-to-L2 message unconditionally (before checking is_reverted).
consume_l1_to_l2_message(execution_context=tx_execution_context, nonce=nonce);

if (is_reverted != FALSE) {
    %{ EndTx %}
    return ();
}

// ... rest of execution ...
```

---

### Proof of Concept

1. User calls the L1 bridge contract, depositing 100 ETH. The bridge locks the ETH and sends an L1→L2 message targeting an L2 contract's `handle_deposit` entry point.
2. The L2 `handle_deposit` handler reverts (e.g., the L2 contract has a bug or the user sends calldata that triggers a panic).
3. The sequencer sets `is_reverted = 1` for this L1 handler transaction.
4. `execute_l1_handler_transaction` hits the early-return branch at line 387–390; `consume_l1_to_l2_message` is never called.
5. `outputs.messages_to_l2` does not advance; the message is absent from the OS output serialized by `serialize_messages`.
6. L1 verifies the proof. The message is not in the consumed-messages list. L1 never calls the bridge's release/refund logic.
7. The 100 ETH remains locked in the L1 bridge contract with no protocol-level mechanism to recover it through the normal flow. [6](#0-5)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L361-361)
```text
    charge_fee(block_context=block_context, tx_execution_context=updated_tx_execution_context);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L374-452)
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
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L502-508)
```text
    assert [outputs.messages_to_l2] = MessageToL2Header(
        from_address=[execution_context.calldata],
        to_address=execution_info.contract_address,
        nonce=nonce,
        selector=execution_info.selector,
        payload_size=payload_size,
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
