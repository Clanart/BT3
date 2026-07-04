### Title
Reverted L1 Handler Skips `consume_l1_to_l2_message`, Permanently Freezing L1 Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`execute_l1_handler_transaction` contains an early return guard at lines 387–390 that is intended to skip the **execution** step for reverted L1 handler transactions. However, this same early return also skips `consume_l1_to_l2_message` (line 444), which is the accounting step that writes the L1-to-L2 message into the OS output (`outputs.messages_to_l2`). The L1 StarkNet core contract uses this output to mark messages as consumed. When a reverted L1 handler's message is absent from the output, the L1 contract never marks it consumed, leaving the ETH or tokens locked in the L1 contract permanently.

---

### Finding Description

In `execute_l1_handler_transaction`:

```cairo
func execute_l1_handler_transaction{...}(block_context: BlockContext*) {
    alloc_locals;

    %{ StartTx %}
    local is_reverted;
    %{ IsReverted %}
    // Skip the execution step for reverted transaction.
    if (is_reverted != FALSE) {
        %{ EndTx %}
        return ();          // <-- EARLY RETURN skips everything below
    }

    // ... setup, hash computation, tx_info fill ...

    // Consume L1-to-L2 message.
    consume_l1_to_l2_message(execution_context=tx_execution_context, nonce=nonce);  // SKIPPED
    non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(...); // SKIPPED
    ...
}
```

The guard at lines 387–390 conflates two independent concerns:

1. **Execution skip** — correct; a reverted handler should not re-execute.
2. **Message consumption** — incorrect; the L1-to-L2 message must still be recorded in the OS output regardless of execution outcome.

`consume_l1_to_l2_message` (lines 491–519) writes a `MessageToL2Header` entry into `outputs.messages_to_l2`:

```cairo
assert [outputs.messages_to_l2] = MessageToL2Header(
    from_address=[execution_context.calldata],
    to_address=execution_info.contract_address,
    nonce=nonce,
    selector=execution_info.selector,
    payload_size=payload_size,
);
```

This segment is later serialized by `serialize_messages` in `output.cairo` (lines 188–195) and submitted to the L1 StarkNet core contract as proof of which messages were processed. The L1 contract iterates this list to mark messages consumed. If a message is absent, it is never marked consumed and the associated funds remain locked in the L1 contract.

Compare with the invoke function transaction path (lines 338–365), where a reverted transaction still proceeds past the revert check to `charge_fee` — the two concerns (execution and fee) are correctly separated. No such separation exists for L1 handlers.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

L1-to-L2 messages commonly carry ETH or ERC-20 tokens (e.g., bridge deposits). These assets are locked in the L1 StarkNet core contract until the corresponding message is marked consumed. When the OS output omits the message for a reverted L1 handler, the L1 contract never receives the signal to mark it consumed. The funds remain locked with no on-chain mechanism to release them through normal protocol operation.

---

### Likelihood Explanation

Any L1 message sender (fully unprivileged) can trigger this path:

- A user sends an L1-to-L2 message (e.g., a bridge deposit) whose calldata causes the target L2 contract's `l1_handler` to revert — due to a contract bug, an out-of-gas condition, or deliberately crafted invalid calldata.
- The sequencer sets `is_reverted = TRUE` via the `%{ IsReverted %}` hint.
- The OS takes the early return, omitting the message from `outputs.messages_to_l2`.
- The L1 contract never marks the message consumed; the deposited ETH/tokens are frozen.

This is reachable on any L2 contract whose L1 handler can be made to revert, which is a realistic and common condition (e.g., contract bugs, insufficient gas, invalid state).

---

### Recommendation

Separate the execution-skip concern from the message-consumption concern, mirroring the fix pattern from the referenced report. Move `consume_l1_to_l2_message` before the revert guard (or restructure so it always executes), and only skip the `non_reverting_select_execute_entry_point_func` call when reverted:

```cairo
func execute_l1_handler_transaction{...}(block_context: BlockContext*) {
    alloc_locals;

    %{ StartTx %}
    local is_reverted;
    %{ IsReverted %}

    // ... setup, hash computation, tx_info fill (must happen for all paths) ...

    // Always consume the L1-to-L2 message, regardless of revert status.
    consume_l1_to_l2_message(execution_context=tx_execution_context, nonce=nonce);

    // Only execute if not reverted.
    if (is_reverted == FALSE) {
        let remaining_gas = L1_HANDLER_L2_GAS_MAX_AMOUNT;
        non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
            block_context=block_context, execution_context=tx_execution_context
        );
    }

    %{ EndTx %}
    return ();
}
```

---

### Proof of Concept

1. Alice deploys a bridge contract on L2 whose `l1_handler` reverts when the payload contains a zero amount.
2. Bob (attacker or innocent user) sends an L1-to-L2 message from the L1 StarkNet core contract with 1 ETH and a zero-amount payload targeting Alice's bridge.
3. The sequencer includes the L1 handler transaction in a block and sets `is_reverted = TRUE` (because the handler reverts).
4. `execute_l1_handler_transaction` hits the guard at line 387 and returns at line 389, skipping `consume_l1_to_l2_message`.
5. The OS output's `messages_to_l2` segment does not contain Bob's message.
6. `serialize_messages` (output.cairo lines 188–195) serializes the segment to L1 without Bob's message.
7. The L1 StarkNet core contract processes the output and never marks Bob's message consumed.
8. Bob's 1 ETH remains locked in the L1 contract indefinitely with no protocol-level release path.

**Root cause line:** `transaction_impls.cairo` line 387–390 — the early return that skips `consume_l1_to_l2_message` at line 444. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/output.cairo (L188-197)
```text
    let messages_to_l2_segment_size = (
        final_carried_outputs.messages_to_l2 - initial_carried_outputs.messages_to_l2
    );
    serialize_word(messages_to_l2_segment_size);

    // Relocate 'messages_to_l2_segment' to the correct place in the output segment.
    relocate_segment(src_ptr=initial_carried_outputs.messages_to_l2, dest_ptr=output_ptr);
    let output_ptr = cast(final_carried_outputs.messages_to_l2, felt*);

    return ();
```
