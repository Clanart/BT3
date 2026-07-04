### Title
Reverted L1 Handler Transactions Bypass `consume_l1_to_l2_message`, Permanently Freezing L1 Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

In `execute_l1_handler_transaction`, when the hint `%{ IsReverted %}` sets `is_reverted != FALSE`, the function returns immediately without calling `consume_l1_to_l2_message`. This means the L1-to-L2 message is never written to `outputs.messages_to_l2`. The L1 StarkNet core contract uses this output segment to determine which messages were consumed; if a message is absent, the L1 contract never marks it as consumed, permanently locking any funds attached to that message.

---

### Finding Description

`execute_l1_handler_transaction` checks `is_reverted` at the very top of its body, before any message-consumption logic:

```cairo
local is_reverted;
%{ IsReverted %}
// Skip the execution step for reverted transaction.
if (is_reverted != FALSE) {
    %{ EndTx %}
    return ();          // ← early exit, consume_l1_to_l2_message never called
}
``` [1](#0-0) 

The only place that records the consumed message into the OS output is `consume_l1_to_l2_message`, called later in the non-reverted path:

```cairo
// Consume L1-to-L2 message.
consume_l1_to_l2_message(execution_context=tx_execution_context, nonce=nonce);
``` [2](#0-1) 

`consume_l1_to_l2_message` writes a `MessageToL2Header` entry into `outputs.messages_to_l2`:

```cairo
assert [outputs.messages_to_l2] = MessageToL2Header(
    from_address=[execution_context.calldata],
    to_address=execution_info.contract_address,
    nonce=nonce,
    selector=execution_info.selector,
    payload_size=payload_size,
);
``` [3](#0-2) 

The `messages_to_l2` segment is serialized into the OS proof output and submitted to the L1 StarkNet core contract via `serialize_messages`:

```cairo
let messages_to_l2_segment_size = (
    final_carried_outputs.messages_to_l2 - initial_carried_outputs.messages_to_l2
);
serialize_word(messages_to_l2_segment_size);
relocate_segment(src_ptr=initial_carried_outputs.messages_to_l2, dest_ptr=output_ptr);
``` [4](#0-3) 

The L1 contract uses this segment to mark messages as consumed. A message absent from the segment is never marked consumed on L1.

Critically, `check_is_reverted` in the production OS is a **no-op** — it imposes zero Cairo-level constraint on the `is_reverted` value:

```cairo
func check_is_reverted(is_reverted: felt) {
    return ();
}
``` [5](#0-4) 

This contrasts with invoke transactions, where even a reverted execution still charges fees, increments the nonce, and completes all accounting. For L1 handlers, the reverted path skips all of this — including the mandatory message-consumption record.

The TODO comment in the same function acknowledges the incomplete handling:

```
// TODO(Yoni): currently, the contract state is not fetched for reverted L1 handlers.
//   Once block hash is supported, we should fetch the contract state for them as well.
``` [6](#0-5) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

When a user sends an L1-to-L2 message (e.g., a deposit bridging ETH or ERC-20 tokens), the funds are locked in the L1 StarkNet core contract until the OS proof shows the message in `messages_to_l2`. If the corresponding L1 handler is marked reverted by the sequencer, the message is never written to the output, the L1 contract never marks it consumed, and the funds remain locked with no on-chain mechanism to release them through the normal protocol flow.

---

### Likelihood Explanation

Any L1 message sender is an unprivileged protocol participant. A message whose target L2 contract handler reverts (due to a bug, an upgrade, or a deliberately crafted payload) will trigger this path. The sequencer sets `is_reverted` via a hint with no Cairo constraint enforcing correctness, so a malicious or faulty sequencer can also set this flag arbitrarily for any L1 handler, freezing any pending L1-to-L2 message funds.

---

### Recommendation

Move `consume_l1_to_l2_message` (and the transaction hash computation) **before** the `is_reverted` early-return branch, so that the message is always recorded in `outputs.messages_to_l2` regardless of execution outcome. The execution step itself can still be skipped for reverted handlers, but the message-consumption record must be emitted unconditionally — mirroring how invoke transactions always complete fee/nonce accounting even when reverted.

---

### Proof of Concept

1. User calls the L1 StarkNet core contract's `sendMessageToL2`, locking 1 ETH as a deposit to an L2 bridge contract.
2. The sequencer includes the corresponding L1 handler transaction in a block and sets `is_reverted = 1` (e.g., because the L2 bridge handler reverts, or because the sequencer is malicious).
3. `execute_l1_handler_transaction` hits the early-return at line 389 before `consume_l1_to_l2_message` is called.
4. The OS proof is generated; `outputs.messages_to_l2` does not contain the message.
5. `serialize_messages` serializes the empty segment; the L1 contract processes the proof and does not mark the message as consumed.
6. The 1 ETH remains locked in the L1 contract indefinitely — no protocol path exists to release it through normal message consumption.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L392-393)
```text
    // TODO(Yoni): currently, the contract state is not fetched for reverted L1 handlers.
    //   Once block hash is supported, we should fetch the contract state for them as well.
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L443-444)
```text
    // Consume L1-to-L2 message.
    consume_l1_to_l2_message(execution_context=tx_execution_context, nonce=nonce);
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo (L20-22)
```text
func check_is_reverted(is_reverted: felt) {
    return ();
}
```
