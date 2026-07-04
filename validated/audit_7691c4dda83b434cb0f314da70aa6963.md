### Title
Unchecked Felt Subtraction in `execute_send_message_to_l1` Allows Wraparound `payload_size`, Causing Network Halt - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

In `execute_send_message_to_l1`, the `payload_size` is computed as `request.payload_end - payload_start` with no non-negativity check. In Cairo's felt arithmetic, if `payload_end < payload_start` (both are attacker-controlled memory pointers), the subtraction wraps around modulo the field prime P, producing a value near `P ≈ 2^251`. This astronomically large felt is then passed directly to `memcpy` as `len` and written into the OS output as `MessageToL1Header.payload_size`. The result is that the Cairo VM exhausts all available steps attempting to execute the recursive `memcpy`, preventing the block from ever being proven and halting the network.

---

### Finding Description

In `syscall_impls.cairo`, `execute_send_message_to_l1` computes the payload size as a raw felt subtraction:

```cairo
tempvar payload_start = request.payload_start;
tempvar payload_size = request.payload_end - payload_start;   // ← no assert_nn / assert_nn_le

assert [outputs.messages_to_l1] = MessageToL1Header(
    from_address=contract_address, to_address=request.to_address, payload_size=payload_size
);
memcpy(
    dst=outputs.messages_to_l1 + MessageToL1Header.SIZE, src=payload_start, len=payload_size
);
let (outputs) = os_carried_outputs_new(
    messages_to_l1=outputs.messages_to_l1 + MessageToL1Header.SIZE + payload_size,
    ...
);
``` [1](#0-0) 

The `SendMessageToL1Request` struct's `payload_start` and `payload_end` fields are raw felt pointers supplied by the executing contract. A contract written in raw CASM (which any user can declare and deploy on StarkNet) can set `payload_end` to any value, including one less than `payload_start`. When `payload_end < payload_start` as integers, the felt subtraction `payload_end - payload_start` does not revert or clamp to zero — it wraps to `P - (payload_start - payload_end)`, a value near `2^251`.

This is the direct Cairo analog of the Solidity vulnerability: just as `uint256(int256(negative_value))` silently underflows to a huge uint instead of zero, `payload_end - payload_start` in felt silently wraps to a huge felt instead of reverting.

The gas check (`reduce_syscall_gas_and_write_response_header`) runs **before** the payload size is computed and only charges the flat `SEND_MESSAGE_TO_L1_GAS_COST` — it does not account for payload size at all. After the gas check passes, the wrapped `payload_size` is used unconditionally. [2](#0-1) 

Compare with the deprecated syscall path in `deprecated_execute_syscalls.cairo`, which uses a struct field `syscall.payload_size` directly — it does not perform a pointer subtraction and is therefore not affected. [3](#0-2) 

---

### Impact Explanation

**High — Network not being able to confirm new transactions (total network shutdown).**

When `payload_size` wraps to ~`2^251`, `memcpy` is called with `len ≈ 2^251`. Cairo's `memcpy` is a recursive function that decrements `len` by 1 per step. Executing `2^251` steps is computationally impossible; the prover cannot generate a proof for the block. The block is permanently stuck: it cannot be proven, and the network cannot advance past it. Every subsequent block is also blocked, constituting a total network halt.

Additionally, the wrapped `payload_size` is written into `MessageToL1Header.payload_size` in the OS output segment before `memcpy` is called, corrupting the OS output commitment even if the VM were somehow to survive. [4](#0-3) 

---

### Likelihood Explanation

Any unprivileged user can declare a contract class containing raw CASM bytecode. StarkNet allows arbitrary CASM class declarations. A malicious CASM contract can issue a `send_message_to_l1` syscall with `payload_end` set to any felt value less than `payload_start`. No special privilege, leaked key, or operator cooperation is required. The attacker only needs to pay the gas for one transaction. The attack is deterministic and requires no probabilistic assumptions.

---

### Recommendation

Add a non-negativity assertion on `payload_size` immediately after computing it, consistent with how other array-length values are validated elsewhere in the codebase (e.g., `assert_nn` / `assert_nn_le`):

```cairo
tempvar payload_start = request.payload_start;
tempvar payload_size = request.payload_end - payload_start;
assert_nn(payload_size);   // ← add this check
```

Additionally, audit all other pointer-difference computations in the new syscall interface for the same pattern. A similar unchecked subtraction exists for `constructor_calldata_size` in `execute_deploy`:

```cairo
local constructor_calldata_size = request.constructor_calldata_end - constructor_calldata_start;
``` [5](#0-4) 

---

### Proof of Concept

1. Attacker writes a CASM contract with the following logic in its entry point:
   - Allocate two memory cells: set `payload_start = fp + 10`, `payload_end = fp + 5` (so `payload_end < payload_start`).
   - Issue a `send_message_to_l1` syscall with `RequestHeader.gas` set to a value ≥ `SEND_MESSAGE_TO_L1_GAS_COST`, `to_address = <any L1 address>`, `payload_start = fp+10`, `payload_end = fp+5`.

2. Attacker declares this class on StarkNet (permissionless), deploys an instance, and submits an invoke transaction calling the entry point.

3. The sequencer includes the transaction in a block and runs the OS:
   - `reduce_syscall_gas_and_write_response_header` succeeds (gas check passes).
   - `payload_size = (fp+5) - (fp+10) = -5 mod P ≈ P - 5 ≈ 2^251`.
   - `memcpy(..., len=P-5)` begins executing.

4. The Cairo VM cannot complete `2^251` recursive steps. The prover cannot generate a proof. The block is permanently unfinalizeable. The network halts.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L463-467)
```text
    local constructor_calldata_start: felt* = request.constructor_calldata_start;
    local constructor_calldata_size = request.constructor_calldata_end - constructor_calldata_start;

    let specific_base_gas_cost = DEPLOY_GAS_COST + DEPLOY_CALLDATA_FACTOR_GAS_COST *
        constructor_calldata_size;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L1351-1370)
```text
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
