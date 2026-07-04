### Title
L1→L2 Message Not Consumed When L1 Handler Transaction Is Reverted — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

In `execute_l1_handler_transaction`, when the transaction is marked as reverted (`is_reverted != FALSE`), the function returns immediately without calling `consume_l1_to_l2_message`. This means the L1→L2 message is never recorded in the OS output as consumed. The L1 StarkNet core contract will therefore never mark the message as consumed, permanently locking any funds associated with the message in the L1 bridge contract.

---

### Finding Description

The `execute_l1_handler_transaction` function in `transaction_impls.cairo` handles the early-exit path for reverted L1 handler transactions as follows:

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

For non-reverted transactions, the function proceeds to call `consume_l1_to_l2_message` (line 444), which writes the message header into `outputs.messages_to_l2`. This output segment is what the L1 StarkNet core contract reads to determine which L1→L2 messages have been processed and should be marked as consumed on L1.

When `is_reverted != FALSE`, the function returns at line 389 **before** `consume_l1_to_l2_message` is ever called. The message is therefore absent from the OS output, and the L1 contract never marks it as consumed.

This is directly analogous to the reference report: a critical operation (L1 handler execution) is processed by the OS, but the required accounting update (message consumption) is silently skipped.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

L1→L2 messages are the mechanism by which assets are bridged from Ethereum to StarkNet (e.g., ERC-20 deposits via token bridges). The flow is:

1. User calls the L1 bridge, which locks tokens and emits an L1→L2 message.
2. The StarkNet OS processes the message via an L1 handler transaction.
3. The OS output records the message as consumed; the L1 bridge marks it consumed on-chain.

If step 3 is skipped because the L1 handler reverted, the L1 bridge never receives confirmation that the message was consumed. The locked tokens remain in the L1 bridge with no path to recovery: the user cannot withdraw them from L1 (the message is not cancelled) and cannot receive them on L2 (the handler reverted). The funds are permanently frozen.

---

### Likelihood Explanation

The `is_reverted` flag is set by the sequencer hint `%{ IsReverted %}`. The code explicitly handles the reverted case for L1 handlers, confirming this is a reachable code path. Any L2 contract that can revert under attacker-controlled input (e.g., a token bridge whose `handle_deposit` function reverts on malformed calldata, an out-of-gas condition, or a contract bug) creates the conditions for this freeze. An unprivileged attacker can trigger this by:

1. Sending an L1→L2 message with calldata crafted to cause the L2 handler to revert.
2. The sequencer marks the transaction as reverted.
3. The OS skips `consume_l1_to_l2_message`.
4. Funds are frozen.

No privileged access is required; only the ability to send an L1→L2 message (a public protocol entry point).

---

### Recommendation

`consume_l1_to_l2_message` must be called for **all** L1 handler transactions, regardless of whether they revert. The message consumption is an accounting invariant that must hold independently of execution outcome, just as a nonce increment must occur even for reverted account transactions. The early-return branch should be restructured to consume the message before returning:

```cairo
if (is_reverted != FALSE) {
    // Still consume the L1->L2 message to prevent replay.
    consume_l1_to_l2_message(execution_context=tx_execution_context, nonce=nonce);
    %{ EndTx %}
    return ();
}
```

---

### Proof of Concept

**Root cause location:** [1](#0-0) 

The early return at line 389 exits before `consume_l1_to_l2_message` is reached.

**The missing call that should have been made:** [2](#0-1) 

`consume_l1_to_l2_message` is only reachable for non-reverted transactions.

**What `consume_l1_to_l2_message` writes to the OS output (the consumed-message record):** [3](#0-2) 

Without this write, the message is absent from `outputs.messages_to_l2`, so the L1 StarkNet core contract never marks it consumed, and the associated funds are permanently locked.

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
