### Title
Reverted L1 Handler Skips `consume_l1_to_l2_message`, Permanently Freezing In-Flight L1→L2 Funds - (File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo)

### Summary
In `execute_l1_handler_transaction`, when the hint `%{ IsReverted %}` marks an L1 handler as reverted, the function returns early and never calls `consume_l1_to_l2_message`. Because that call is the sole mechanism by which the OS records a message as consumed in `outputs.messages_to_l2`, the L1 core contract never learns the message was processed. The L1→L2 message remains permanently pending on L1 with no built-in retry or cancellation path inside the OS, freezing any ETH/ERC-20 funds that were locked with the message.

### Finding Description
`execute_l1_handler_transaction` checks `is_reverted` immediately after `%{ StartTx %}`:

```cairo
local is_reverted;
%{ IsReverted %}
// Skip the execution step for reverted transaction.
if (is_reverted != FALSE) {
    %{ EndTx %}
    return ();          // ← early return, no consume_l1_to_l2_message
}
```

Only in the non-reverted branch does the code reach:

```cairo
// Consume L1-to-L2 message.
consume_l1_to_l2_message(execution_context=tx_execution_context, nonce=nonce);
```

`consume_l1_to_l2_message` writes a `MessageToL2Header` entry into `outputs.messages_to_l2`. The L1 StarkNet core contract verifies this output segment to decide which L1→L2 messages to mark as consumed. If the entry is absent, the message is never marked consumed on L1, yet the sequencer has already "processed" the transaction and moved on. The accompanying TODO comment acknowledges the incompleteness:

> "currently, the contract state is not fetched for reverted L1 handlers. Once block hash is supported, we should fetch the contract state for them as well."

This confirms the reverted-L1-handler path is intentionally incomplete, leaving message consumption unimplemented.

### Impact Explanation
Any ETH or ERC-20 tokens locked in the L1 StarkNet core contract as part of the L1→L2 message remain locked indefinitely. The L1 contract will not release them because it never receives proof that the message was consumed. There is no automatic retry inside the OS. Manual cancellation (if available at the L1 contract level) requires a separate privileged or time-locked operation and is not guaranteed. This constitutes **permanent freezing of funds** (Critical).

### Likelihood Explanation
An L1 handler is marked reverted whenever the sequencer determines execution would fail — for example, when the target L2 contract has no `@l1_handler` entry point matching the selector, when the contract is not deployed at the target address, or when gas is exhausted. A user who sends an L1→L2 message to any such address (intentionally or by mistake) triggers this path. No privileged access is required; the L1 message sender is an unprivileged protocol participant.

### Recommendation
Move `consume_l1_to_l2_message` **before** the `is_reverted` early-return guard, so the message is always recorded in `outputs.messages_to_l2` regardless of execution outcome. This mirrors the correct pattern used for regular invoke transactions, where fee charging and state bookkeeping occur even for reverted executions. Separately, resolve the TODO about fetching contract state for reverted L1 handlers so the OS output is complete and verifiable.

### Proof of Concept
1. User calls the L1 StarkNet core contract's `sendMessageToL2`, targeting an L2 address that has no `@l1_handler` for the given selector, and attaches ETH.
2. The sequencer picks up the L1 handler transaction and, because execution would fail (entry point not found), sets `is_reverted = TRUE` via `%{ IsReverted %}`.
3. `execute_l1_handler_transaction` hits the early-return branch at lines 387–390 and returns without calling `consume_l1_to_l2_message`.
4. The OS proof is generated; `outputs.messages_to_l2` contains no entry for this message.
5. The L1 core contract verifies the proof and does not mark the message consumed.
6. The ETH remains locked in the L1 contract. The user has no in-protocol way to recover it; the message is permanently stuck. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L381-390)
```text
    alloc_locals;

    %{ StartTx %}
    local is_reverted;
    %{ IsReverted %}
    // Skip the execution step for reverted transaction.
    if (is_reverted != FALSE) {
        %{ EndTx %}
        return ();
    }
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/output.cairo (L60-76)
```text
// An L1 to L2 message header, the message payload is concatenated to the end of the header.
struct MessageToL2Header {
    // The L1 address of the contract sending the message.
    from_address: felt,
    // The L2 address of the contract receiving the message.
    to_address: felt,
    nonce: felt,
    selector: felt,
    payload_size: felt,
}

// Holds all the information that StarkNet's OS needs to output.
// TODO(Yoni, 1/1/2026): rename to OsMessages.
struct OsCarriedOutputs {
    messages_to_l1: MessageToL1Header*,
    messages_to_l2: MessageToL2Header*,
}
```
