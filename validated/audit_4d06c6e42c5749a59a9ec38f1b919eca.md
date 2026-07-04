### Title
Missing Upper-Bound Check on `payload_size` in `send_message_to_l1` Syscall Handlers Allows OS Prover Resource Exhaustion — (File: `execution/syscall_impls.cairo`, `execution/deprecated_execute_syscalls.cairo`)

---

### Summary

Both the Cairo 1 and deprecated Cairo 0 implementations of the `send_message_to_l1` syscall accept an attacker-controlled `payload_size` with no upper-bound validation. The OS unconditionally `memcpy`s the full payload into the output segment, spending OS prover steps that are not accounted for in the flat `SEND_MESSAGE_TO_L1_GAS_COST`. A malicious contract can craft a transaction that forces the OS to exhaust its step budget while processing the block, causing the block to fail to prove and halting the network.

---

### Finding Description

**Cairo 1 path — `execute_send_message_to_l1` in `syscall_impls.cairo`:**

`payload_size` is computed as `request.payload_end - request.payload_start` (a felt subtraction of two attacker-supplied pointers) and is immediately used in `memcpy` with no upper-bound assertion:

```cairo
tempvar payload_start = request.payload_start;
tempvar payload_size = request.payload_end - payload_start;   // no bound check

assert [outputs.messages_to_l1] = MessageToL1Header(
    from_address=contract_address, to_address=request.to_address, payload_size=payload_size
);
memcpy(
    dst=outputs.messages_to_l1 + MessageToL1Header.SIZE, src=payload_start, len=payload_size
);
``` [1](#0-0) 

**Deprecated Cairo 0 path — `execute_deprecated_syscalls` in `deprecated_execute_syscalls.cairo`:**

`syscall.payload_size` is a raw felt written by the contract into the syscall segment and is used directly in `memcpy` with no bound check:

```cairo
let syscall = [cast(syscall_ptr, SendMessageToL1SysCall*)];
assert [outputs.messages_to_l1] = MessageToL1Header(
    from_address=..., to_address=syscall.to_address, payload_size=syscall.payload_size,
);
memcpy(
    dst=outputs.messages_to_l1 + MessageToL1Header.SIZE,
    src=syscall.payload_ptr,
    len=syscall.payload_size,   // no bound check
);
``` [2](#0-1) 

**The gas cost is flat and does not scale with payload size:**

```cairo
const SEND_MESSAGE_TO_L1_GAS_COST = 14470;
``` [3](#0-2) 

Compare this to `DEPLOY_CALLDATA_FACTOR_GAS_COST`, which correctly scales with calldata size:

```cairo
const DEPLOY_CALLDATA_FACTOR_GAS_COST = 4850;
``` [4](#0-3) 

The `memcpy` inside the OS program costs one OS prover step per element copied. These OS steps are drawn from the OS's own step budget — a separate resource from the contract's L2 gas budget. There is no constant or assertion anywhere in the OS that caps `payload_size`: [5](#0-4) 

For contrast, other size-sensitive paths in the same codebase do enforce bounds — e.g., `calldata_size` is checked with `assert_nn_le(tx_execution_context.calldata_size, SIERRA_ARRAY_LEN_BOUND - 1)`: [6](#0-5) 

No equivalent guard exists for `payload_size` in either the Cairo 1 or deprecated syscall handler.

---

### Impact Explanation

**High — Network not being able to confirm new transactions (total network shutdown).**

The OS prover step budget is a block-level resource shared across all transactions in the block. The `memcpy` of an oversized L2→L1 message payload consumes OS steps proportional to `payload_size`, but these steps are not charged to the transaction's L2 gas. A malicious contract can write a payload large enough (bounded only by its own gas budget for the writes, not by any OS-enforced cap) to exhaust the OS step budget for the block. When the OS runs out of steps, the block cannot be proven. The sequencer cannot retroactively remove the offending transaction from an already-executed block, causing a proving failure and halting the network's ability to confirm new transactions.

---

### Likelihood Explanation

Any unprivileged contract deployer can deploy a contract that calls `send_message_to_l1` with a maximally large payload. The attack requires only a standard invoke transaction — no privileged access, no leaked keys, no operator cooperation. The deprecated path (Cairo 0) is particularly exposed because Cairo 0 contracts have no per-transaction gas limit analogous to `EXECUTE_MAX_SIERRA_GAS`; their execution is bounded only by the block's step limit, making it easier to write a large payload without triggering a gas-out-of-funds revert. The flat `SEND_MESSAGE_TO_L1_GAS_COST` means the attacker pays the same fee regardless of payload size, making repeated exploitation cheap.

---

### Recommendation

1. Add an explicit upper-bound constant for the maximum allowed L2→L1 message payload size (e.g., `MAX_L2_TO_L1_MSG_PAYLOAD_SIZE`).
2. Assert this bound in both `execute_send_message_to_l1` (Cairo 1 path) and the deprecated `SendMessageToL1` handler before the `memcpy`.
3. Make `SEND_MESSAGE_TO_L1_GAS_COST` scale with payload size (add a per-element factor analogous to `DEPLOY_CALLDATA_FACTOR_GAS_COST`) so that OS prover step consumption is proportional to the gas charged.

---

### Proof of Concept

1. Deploy a Cairo 1 contract with the following logic in its `__execute__` function:
   ```
   // Write 10,000,000 felt values to a buffer.
   // Call send_message_to_l1(to_address=<any>, payload=buffer[0..10_000_000]).
   ```
2. Submit an invoke transaction calling this contract with `max_l2_gas` set to `EXECUTE_MAX_SIERRA_GAS`.
3. The contract writes ~10,000,000 felt values to the syscall payload segment (spending ~10^9 gas for the writes).
4. The OS executes `execute_send_message_to_l1`: `payload_size = 10_000_000` passes unchecked; `memcpy` copies 10,000,000 elements into the OS output, consuming 10,000,000 OS prover steps with no gas deduction.
5. If the block's OS step budget is exhausted by this (or a combination of such transactions), the block fails to prove, halting the network. [7](#0-6) [8](#0-7)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L1345-1374)
```text
// Sends a message to L1.
func execute_send_message_to_l1{range_check_ptr, syscall_ptr: felt*, outputs: OsCarriedOutputs*}(
    contract_address: felt
) {
    alloc_locals;
    let request = cast(syscall_ptr + RequestHeader.SIZE, SendMessageToL1Request*);
    let success = reduce_syscall_gas_and_write_response_header(
        total_gas_cost=SEND_MESSAGE_TO_L1_GAS_COST, request_struct_size=SendMessageToL1Request.SIZE
    );
    if (success == FALSE) {
        // Not enough gas to execute the syscall.
        return ();
    }

    tempvar payload_start = request.payload_start;
    tempvar payload_size = request.payload_end - payload_start;

    assert [outputs.messages_to_l1] = MessageToL1Header(
        from_address=contract_address, to_address=request.to_address, payload_size=payload_size
    );
    memcpy(
        dst=outputs.messages_to_l1 + MessageToL1Header.SIZE, src=payload_start, len=payload_size
    );
    let (outputs) = os_carried_outputs_new(
        messages_to_l1=outputs.messages_to_l1 + MessageToL1Header.SIZE + payload_size,
        messages_to_l2=outputs.messages_to_l2,
    );

    return ();
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo (L693-709)
```text
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants.cairo (L106-106)
```text
const DEPLOY_CALLDATA_FACTOR_GAS_COST = 4850;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants.cairo (L116-116)
```text
const SEND_MESSAGE_TO_L1_GAS_COST = 14470;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L485-485)
```text
    assert_nn_le(tx_execution_context.calldata_size, SIERRA_ARRAY_LEN_BOUND - 1);
```
