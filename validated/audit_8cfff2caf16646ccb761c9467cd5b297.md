### Title
Unchecked Zero `to_address` in `send_message_to_l1` Syscall Handler Allows Permanent Freezing of Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo`)

---

### Summary

The `SEND_MESSAGE_TO_L1_SELECTOR` handler in `deprecated_execute_syscalls.cairo` writes an L2→L1 message to the OS output without validating that `syscall.to_address` is non-zero. This is the direct Cairo/StarkNet OS analog of the EthBridge `_to` zero-address bug: the OS faithfully commits a message destined for L1 address `0x0` to the proof output, making it permanently unclaimable on L1 and freezing any funds associated with the withdrawal flow.

---

### Finding Description

In `deprecated_execute_syscalls.cairo`, the final branch of `execute_deprecated_syscalls` handles `SEND_MESSAGE_TO_L1_SELECTOR`:

```cairo
// Here the system call must be 'SendMessageToL1'.
assert selector = SEND_MESSAGE_TO_L1_SELECTOR;

let syscall = [cast(syscall_ptr, SendMessageToL1SysCall*)];

assert [outputs.messages_to_l1] = MessageToL1Header(
    from_address=execution_context.execution_info.contract_address,
    to_address=syscall.to_address,          // ← NO zero-check here
    payload_size=syscall.payload_size,
);
``` [1](#0-0) 

The `to_address` field is taken directly from the syscall struct and written into the `MessageToL1Header` that is serialized into the OS proof output. There is no `assert_not_zero(syscall.to_address)` guard anywhere in this path.

By contrast, the OS **does** perform zero/reserved-address checks in analogous critical paths:

- `deploy_contract.cairo` explicitly rejects deployment to `ORIGIN_ADDRESS` (0), `BLOCK_HASH_CONTRACT_ADDRESS`, `ALIAS_CONTRACT_ADDRESS`, and `RESERVED_CONTRACT_ADDRESS` via `assert_not_zero(...)`.
- `consume_l1_to_l2_message` asserts `assert_not_zero(execution_context.calldata_size)` before processing. [2](#0-1) [3](#0-2) 

The `MessageToL1Header` struct definition confirms `to_address` is the L1 recipient:

```cairo
struct MessageToL1Header {
    from_address: felt,   // L2 sender
    to_address: felt,     // L1 recipient  ← can be 0
    payload_size: felt,
}
``` [4](#0-3) 

The message (including its payload) is then serialized into the proof output via `serialize_messages`, which relocates the `messages_to_l1` segment directly into the output segment without any post-hoc address validation: [5](#0-4) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

On L1, the StarkNet core contract stores the Keccak hash of each committed L2→L1 message. To consume a message, the contract at `to_address` must call `consumeMessageFromL2`. If `to_address = 0x0`, no EOA or contract controls that address on L1, so `consumeMessageFromL2` can never be called. The message hash is permanently stored in L1 state but can never be cleared.

In a standard L2→L1 token-bridge withdrawal flow:
1. User calls L2 bridge → bridge burns L2 tokens and calls `send_message_to_l1(to_address=0, payload=[amount, user])`.
2. OS commits the message to the proof output without rejection.
3. L1 state update records the message hash.
4. Tokens are burned on L2; the corresponding L1 claim is permanently unclaimable.

The burned L2 tokens and the locked L1 claim represent a **direct, irreversible loss of user funds**.

---

### Likelihood Explanation

**Medium.** The scenario requires a contract (e.g., a bridge) that does not independently validate `to_address` before calling `send_message_to_l1`. This is precisely the class of bug the external report describes — a missing sanity check that the OS is the last layer to catch. Any L2 contract callable by an unprivileged user that forwards a user-supplied `to_address` into `send_message_to_l1` without validation is a reachable trigger. The OS provides no safety net.

---

### Recommendation

Add an `assert_not_zero` guard on `to_address` before writing the `MessageToL1Header` in both the deprecated and new syscall handlers:

```cairo
// In the SEND_MESSAGE_TO_L1_SELECTOR branch:
assert_not_zero(syscall.to_address);  // Prevent permanent message lock at address 0
assert [outputs.messages_to_l1] = MessageToL1Header(
    from_address=execution_context.execution_info.contract_address,
    to_address=syscall.to_address,
    payload_size=syscall.payload_size,
);
```

Apply the same guard in `syscall_impls.cairo`'s `execute_send_message_to_l1` implementation.

---

### Proof of Concept

1. Deploy a Cairo 0 contract with the following logic:
   ```
   func withdraw{syscall_ptr: felt*}(amount: felt) {
       // Bug: to_address is hardcoded to 0 (or user-supplied without validation)
       send_message_to_l1(to_address=0, payload_size=1, payload=[amount]);
       // burn L2 tokens here
   }
   ```
2. Call `withdraw(1000)` from any account.
3. The OS processes `SEND_MESSAGE_TO_L1_SELECTOR` at lines 691–716 of `deprecated_execute_syscalls.cairo`, writes `MessageToL1Header(from_address=<contract>, to_address=0, payload_size=1)` to the output with no revert.
4. The block is proven and submitted to L1. The StarkNet core contract records the message hash keyed to `to_address=0`.
5. No L1 address can call `consumeMessageFromL2` for this message. The 1000 tokens burned on L2 are permanently lost. [6](#0-5)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo (L691-716)
```text
    assert selector = SEND_MESSAGE_TO_L1_SELECTOR;

    let syscall = [cast(syscall_ptr, SendMessageToL1SysCall*)];

    assert [outputs.messages_to_l1] = MessageToL1Header(
        from_address=execution_context.execution_info.contract_address,
        to_address=syscall.to_address,
        payload_size=syscall.payload_size,
    );
    memcpy(
        dst=outputs.messages_to_l1 + MessageToL1Header.SIZE,
        src=syscall.payload_ptr,
        len=syscall.payload_size,
    );
    let (outputs) = os_carried_outputs_new(
        messages_to_l1=outputs.messages_to_l1 + MessageToL1Header.SIZE +
        outputs.messages_to_l1.payload_size,
        messages_to_l2=outputs.messages_to_l2,
    );
    %{ OsLoggerExitSyscall %}
    return execute_deprecated_syscalls(
        block_context=block_context,
        execution_context=execution_context,
        syscall_size=syscall_size - SendMessageToL1SysCall.SIZE,
        syscall_ptr=syscall_ptr + SendMessageToL1SysCall.SIZE,
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L44-49)
```text
    // Assert that we don't deploy to one of the reserved addresses.
    assert_not_zero(
        (contract_address - ORIGIN_ADDRESS) * (contract_address - BLOCK_HASH_CONTRACT_ADDRESS) * (
            contract_address - ALIAS_CONTRACT_ADDRESS
        ) * (contract_address - RESERVED_CONTRACT_ADDRESS),
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L494-494)
```text
    assert_not_zero(execution_context.calldata_size);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/output.cairo (L52-58)
```text
struct MessageToL1Header {
    // The L2 address of the contract sending the message.
    from_address: felt,
    // The L1 address of the contract receiving the message.
    to_address: felt,
    payload_size: felt,
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
