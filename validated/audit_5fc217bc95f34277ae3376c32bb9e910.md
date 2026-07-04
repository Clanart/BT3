### Title
Missing `consume_l1_to_l2_message` for Reverted L1 Handler Transactions Enables Message Replay — (`execution/transaction_impls.cairo`)

### Summary

`execute_l1_handler_transaction` returns early for reverted transactions without recording the L1-to-L2 message as consumed in the OS output. This is the direct analog of the external report's pattern: a specific execution path omits the mandatory accounting update that other paths correctly perform.

### Finding Description

In `execute_l1_handler_transaction`, when the sequencer hint `IsReverted` is non-zero, the function exits immediately: [1](#0-0) 

```cairo
%{ StartTx %}
local is_reverted;
%{ IsReverted %}
// Skip the execution step for reverted transaction.
if (is_reverted != FALSE) {
    %{ EndTx %}
    return ();
}
```

For the non-reverted path, the function correctly calls `consume_l1_to_l2_message`, which writes the message into `outputs.messages_to_l2`: [2](#0-1) 

```cairo
// Consume L1-to-L2 message.
consume_l1_to_l2_message(execution_context=tx_execution_context, nonce=nonce);
```

`consume_l1_to_l2_message` writes a `MessageToL2Header` entry into `outputs.messages_to_l2` and advances the output pointer: [3](#0-2) 

This output is what the L1 `StarknetCore` contract reads to mark messages as consumed. When the reverted path skips this call, the message is **absent from the OS output**, so the L1 contract never marks it consumed — it remains in the "pending" state on L1.

The TODO comment in the same function acknowledges the incomplete handling of reverted L1 handlers: [4](#0-3) 

```cairo
// TODO(Yoni): currently, the contract state is not fetched for reverted L1 handlers.
//   Once block hash is supported, we should fetch the contract state for them as well.
```

By contrast, all account transaction types (`execute_invoke_function_transaction`, `execute_deploy_account_transaction`, `execute_declare_transaction`) always perform their mandatory accounting steps (nonce increment, fee charge) regardless of revert status. [5](#0-4) 

### Impact Explanation

**Direct loss of funds (Critical).**

Attack sequence:
1. Attacker sends an L1→L2 message (e.g., a bridge deposit locking 100 ETH on L1 to mint 100 tokens on L2).
2. The L2 handler reverts (e.g., contract is paused, or attacker crafts calldata that triggers a revert).
3. The OS output omits the message → L1 `StarknetCore` leaves it as unconsumed/pending.
4. Attacker invokes the L1 cancellation flow and recovers the 100 ETH from L1 (after the cancellation delay).
5. The message is still present in the L2 sequencer queue. When conditions change (contract unpaused), the sequencer replays it.
6. The handler succeeds: 100 tokens are minted on L2.
7. Attacker holds 100 ETH (recovered from L1) **and** 100 tokens (minted on L2) — a direct double-spend against the bridge protocol.

### Likelihood Explanation

Any unprivileged user who can send an L1-to-L2 message is a valid attacker. Causing a handler revert is straightforward: send malformed calldata, target a paused contract, or exploit any revert condition in the handler. The cancellation mechanism on L1 is a standard, publicly documented feature. No privileged access is required.

### Recommendation

Call `consume_l1_to_l2_message` for **all** L1 handler transactions, including reverted ones, before returning. The message consumption must be recorded in the OS output unconditionally, just as fee charging is unconditional for account transactions. The reverted path should be restructured to:

1. Load the execution context and nonce.
2. Call `consume_l1_to_l2_message` (recording the message as consumed).
3. Skip the actual entry-point execution.
4. Return.

This mirrors the pattern used for reverted invoke transactions, which still charge fees even when execution is skipped.

### Proof of Concept

```
1. Deploy a bridge contract on L2 with an l1_handler that mints tokens.
2. Send an L1→L2 message with 100 ETH locked on L1.
3. Pause the L2 bridge contract so the handler reverts.
4. Observe: OS output for the block does NOT contain the message in messages_to_l2.
5. L1 StarknetCore: message status remains PENDING (not consumed).
6. Invoke L1 cancellation after the delay → recover 100 ETH.
7. Unpause the L2 bridge. Sequencer replays the pending message.
8. Handler succeeds → 100 tokens minted on L2.
9. Net result: attacker holds 100 ETH + 100 tokens.
```

The root cause is at: [1](#0-0)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L360-365)
```text
    // Charge fee.
    charge_fee(block_context=block_context, tx_execution_context=updated_tx_execution_context);

    %{ EndTx %}

    return ();
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L383-390)
```text
    %{ StartTx %}
    local is_reverted;
    %{ IsReverted %}
    // Skip the execution step for reverted transaction.
    if (is_reverted != FALSE) {
        %{ EndTx %}
        return ();
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L392-394)
```text
    // TODO(Yoni): currently, the contract state is not fetched for reverted L1 handlers.
    //   Once block hash is supported, we should fetch the contract state for them as well.
    local entry_point_selector;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L443-448)
```text
    // Consume L1-to-L2 message.
    consume_l1_to_l2_message(execution_context=tx_execution_context, nonce=nonce);
    let remaining_gas = L1_HANDLER_L2_GAS_MAX_AMOUNT;
    non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
        block_context=block_context, execution_context=tx_execution_context
    );
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
