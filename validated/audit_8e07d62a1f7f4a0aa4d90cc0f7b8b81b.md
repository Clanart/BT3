### Title
L2→L1 Messages Not Rolled Back During Reverted Sub-Calls — (File: `execution/revert.cairo`)

---

### Summary

The `handle_revert` function in `revert.cairo` only reverts storage writes and class-hash changes when a sub-call is reverted. It does **not** roll back L2→L1 messages that were emitted via `send_message_to_l1` during the reverted execution. Because the `outputs.messages_to_l1` pointer is an implicit argument that advances as messages are written, and `handle_revert` never receives or resets that pointer, any L2→L1 message written before the revert permanently remains in the OS output segment. The L1 core contract will process those messages and release funds, while the corresponding L2 state changes (e.g., token burns) have been rolled back.

---

### Finding Description

**Vulnerability class:** fee/accounting bug — a carried-output state variable (`outputs.messages_to_l1`) is not updated (rolled back) during a specific operation (sub-call revert), causing the OS to attest to messages that should not exist.

**Root cause — `revert.cairo`**

The `RevertLogEntry` struct defines exactly three entry kinds:

```
struct RevertLogEntry {
    selector: felt,   // CHANGE_CONTRACT_ENTRY | CHANGE_CLASS_ENTRY | storage_key
    value: felt,
}
``` [1](#0-0) 

There is no entry kind for an L2→L1 message. `handle_revert` iterates the log backwards and only restores storage slots and class hashes: [2](#0-1) 

Its only implicit argument is `contract_state_changes`; `outputs` is never touched: [3](#0-2) 

**Contrast with tracked operations**

`execute_storage_write` appends a `RevertLogEntry` so the write can be undone: [4](#0-3) 

`execute_replace_class` similarly appends a `CHANGE_CLASS_ENTRY`: [5](#0-4) 

`execute_send_message_to_l1` is dispatched from `execute_syscalls` which carries `outputs: OsCarriedOutputs*` as an implicit argument: [6](#0-5) 

Because `outputs` is an implicit argument that Cairo automatically threads through every callee, `execute_send_message_to_l1` advances `outputs.messages_to_l1` when it writes the `MessageToL1Header`. That advance is **never reversed** by `handle_revert`.

**How the OS output is consumed**

`serialize_messages` computes the segment size as the difference between the final and initial `messages_to_l1` pointers and relocates the entire segment into the proof output: [7](#0-6) 

Every entry in that segment — including entries written by reverted sub-calls — is attested to by the STARK proof and forwarded to the L1 core contract for processing.

---

### Impact Explanation

**Critical — Direct loss of funds.**

An L2→L1 message is the mechanism by which L2 bridge contracts instruct the L1 bridge to release locked ETH or ERC-20 tokens. If such a message survives a revert:

- The L2 storage changes that should accompany the withdrawal (e.g., burning the user's L2 tokens) are rolled back by `handle_revert`.
- The L2→L1 message is still included in the proven OS output.
- The L1 bridge contract, upon seeing the proven message, releases the corresponding L1 funds.

The net result is that L1 funds are released without any L2 tokens being burned — a direct, permanent loss of funds from the L1 bridge.

---

### Likelihood Explanation

Any unprivileged contract deployer can trigger this path:

1. Deploy a contract `Exploit` that (a) calls `send_message_to_l1` with a crafted payload targeting an L1 bridge, then (b) deliberately panics.
2. Call `Exploit` from a wrapper contract that catches the inner-call failure (the `is_reverted` flag returned by `contract_call_helper`) and returns success.
3. The outer invoke transaction succeeds; `handle_revert` rolls back `Exploit`'s storage changes; the L2→L1 message remains in the output.

The only prerequisite is that the L1 bridge processes messages whose `from_address` matches a contract the attacker controls or can impersonate on L2. For bridges that accept messages from any L2 address (or where the attacker can front-run a legitimate bridge call), exploitation is straightforward.

---

### Recommendation

Add a new `RevertLogEntry` kind — e.g., `CHANGE_MESSAGES_TO_L1_ENTRY` — that records the `messages_to_l1` pointer value before each `send_message_to_l1` call. Extend `handle_revert` (and its implicit-argument list) to accept `outputs: OsCarriedOutputs*` and restore the pointer to its pre-call value when processing this entry kind. This mirrors exactly how storage writes are already undone.

---

### Proof of Concept

**Step-by-step:**

1. Attacker deploys `ExploitBridge` on L2. Its only entry point:
   - Calls `send_message_to_l1(l1_bridge_address, [withdrawal_amount])`.
   - Then executes `assert 1 = 0` (panic / revert).

2. Attacker deploys `Caller` on L2. Its `__execute__` entry point:
   - Calls `ExploitBridge` via `call_contract`.
   - Checks the returned `is_reverted` flag; if set, returns success anyway.

3. Attacker submits an invoke transaction targeting `Caller.__execute__`.

4. The sequencer executes the block:
   - `ExploitBridge` writes `MessageToL1Header` → `outputs.messages_to_l1` advances.
   - `ExploitBridge` panics → `handle_revert` rolls back storage; `outputs.messages_to_l1` is **not** reset.
   - `Caller` returns success; the transaction is included in the block.

5. The OS produces a STARK proof. `serialize_messages` includes the `ExploitBridge` message in the proven output.

6. The L1 core contract verifies the proof and calls `processMessages` on the L1 bridge with the attacker's withdrawal message.

7. The L1 bridge releases funds to the attacker's L1 address. No L2 tokens were burned.

**Observable invariant violation:**

```
messages_to_l1_segment_size_in_proof  >  messages_to_l1_segment_size_after_correct_revert
```

The proven segment contains a message that should have been absent, causing the L1 bridge to over-release funds.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/revert.cairo (L5-24)
```text
const CONTRACT_ADDRESS_UPPER_BOUND = 2 ** 251;
const CHANGE_CONTRACT_ENTRY = CONTRACT_ADDRESS_UPPER_BOUND;
const CHANGE_CLASS_ENTRY = CHANGE_CONTRACT_ENTRY + 1;

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L670-671)
```text
    assert [revert_log] = RevertLogEntry(selector=storage_key, value=prev_value);
    let revert_log = &revert_log[1];
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L912-913)
```text
    assert [revert_log] = RevertLogEntry(selector=CHANGE_CLASS_ENTRY, value=state_entry.class_hash);
    let revert_log = &revert_log[1];
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L331-341)
```text
    if (selector == SEND_MESSAGE_TO_L1_SELECTOR) {
        execute_send_message_to_l1(
            contract_address=execution_context.execution_info.contract_address
        );
        %{ OsLoggerExitSyscall %}
        return execute_syscalls(
            block_context=block_context,
            execution_context=execution_context,
            syscall_ptr_end=syscall_ptr_end,
        );
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/output.cairo (L179-196)
```text
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

```
