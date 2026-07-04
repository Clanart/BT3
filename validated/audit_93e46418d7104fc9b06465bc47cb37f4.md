### Title
Unbounded `send_message_to_l1` Payload Causes Disproportionate OS Proof-Generation Work — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `send_message_to_l1` syscall handler in the StarkNet OS Cairo program deducts a **fixed** gas cost regardless of the L1 message payload size, while the OS then performs an **unbounded** `memcpy` proportional to that payload. An unprivileged contract deployer or transaction sender can craft a contract that emits a very large L1 message payload at fixed gas cost, forcing the OS to perform O(payload\_size) work during proof generation. This can make a block unprovable, halting the network.

---

### Finding Description

In `syscall_impls.cairo`, the `send_message_to_l1` handler first deducts a single fixed constant `SEND_MESSAGE_TO_L1_GAS_COST`:

```cairo
let success = reduce_syscall_gas_and_write_response_header(
    total_gas_cost=SEND_MESSAGE_TO_L1_GAS_COST, request_struct_size=SendMessageToL1Request.SIZE
);
``` [1](#0-0) 

After the fixed gas deduction, the payload size is derived from attacker-controlled pointer arithmetic with **no upper-bound assertion**, and then a `memcpy` of that size is performed unconditionally:

```cairo
tempvar payload_start = request.payload_start;
tempvar payload_size = request.payload_end - payload_start;

assert [outputs.messages_to_l1] = MessageToL1Header(
    from_address=contract_address, to_address=request.to_address, payload_size=payload_size
);
memcpy(
    dst=outputs.messages_to_l1 + MessageToL1Header.SIZE, src=payload_start, len=payload_size
);
``` [2](#0-1) 

This is in direct contrast to other variable-size syscalls in the same file, which charge **proportionally** to their data size. For example, `execute_meta_tx_v0` charges `META_TX_V0_GAS_COST + META_TX_V0_CALLDATA_FACTOR_GAS_COST * calldata_size`: [3](#0-2) 

And `execute_deploy` charges `DEPLOY_GAS_COST + DEPLOY_CALLDATA_FACTOR_GAS_COST * constructor_calldata_size`: [4](#0-3) 

The `send_message_to_l1` handler is the only variable-payload syscall that does **not** follow this proportional-cost pattern and has **no explicit bound assertion** on `payload_size` before the `memcpy`.

---

### Impact Explanation

**High — Network not being able to confirm new transactions (total network shutdown).**

The StarkNet OS Cairo program is executed by the prover to generate a validity proof for each block. If a block contains a transaction whose `send_message_to_l1` payload is very large, the OS must execute the unbounded `memcpy` during proof generation. Because the gas charged to the transaction is fixed (independent of payload size), the prover is forced to do O(payload\_size) Cairo VM steps that are not accounted for in the block's gas budget. A sufficiently large payload can:

1. Exhaust the Cairo VM's memory segment during OS execution.
2. Cause the proof generation to exceed the prover's step/memory limits.
3. Make the block permanently unprovable, stalling the chain.

---

### Likelihood Explanation

**Medium.** Any unprivileged user can deploy a contract that calls `send_message_to_l1` with a large payload array. The attacker only needs to pay the fixed `SEND_MESSAGE_TO_L1_GAS_COST` for the syscall itself; the cost of writing the payload data to contract memory is bounded by the transaction gas limit, but the OS-level `memcpy` work is not. The attack is deterministic and requires no special privileges, leaked keys, or network-level access.

---

### Recommendation

Apply a proportional gas cost to `send_message_to_l1` that scales with `payload_size`, consistent with the pattern already used by `execute_meta_tx_v0` and `execute_deploy`. Specifically, replace the fixed `SEND_MESSAGE_TO_L1_GAS_COST` with:

```cairo
let specific_gas_cost = SEND_MESSAGE_TO_L1_GAS_COST +
    SEND_MESSAGE_TO_L1_PAYLOAD_FACTOR_GAS_COST * payload_size;
```

Additionally, add an explicit upper-bound assertion on `payload_size` before the `memcpy`, analogous to the `SIERRA_ARRAY_LEN_BOUND` checks applied to calldata in other handlers:

```cairo
assert_nn_le(payload_size, MAX_L1_MESSAGE_PAYLOAD_SIZE - 1);
``` [2](#0-1) 

---

### Proof of Concept

1. Deploy a Sierra contract with an `__execute__` entry point that:
   - Allocates a large felt array (e.g., 500,000 elements) in its memory.
   - Calls `send_message_to_l1` with `payload_start` pointing to the array start and `payload_end` pointing to the array end.
2. Submit an `invoke` transaction calling this contract. The transaction pays only the fixed `SEND_MESSAGE_TO_L1_GAS_COST` for the syscall.
3. The sequencer/blockifier accepts the transaction (gas limit satisfied).
4. When the prover runs the OS to generate the block proof, the OS executes `memcpy` for 500,000 elements — work that was never charged to the transaction gas.
5. With a sufficiently large payload, the OS Cairo VM exhausts its memory or step budget, the proof cannot be generated, and the block is permanently stalled, halting the network.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L301-306)
```text
    let specific_base_gas_cost = (
        META_TX_V0_GAS_COST + META_TX_V0_CALLDATA_FACTOR_GAS_COST * calldata_size
    );
    let (success, remaining_gas) = reduce_syscall_base_gas(
        specific_base_gas_cost=specific_base_gas_cost, request_struct_size=MetaTxV0Request.SIZE
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L466-470)
```text
    let specific_base_gas_cost = DEPLOY_GAS_COST + DEPLOY_CALLDATA_FACTOR_GAS_COST *
        constructor_calldata_size;
    let (success, remaining_gas) = reduce_syscall_base_gas(
        specific_base_gas_cost=specific_base_gas_cost, request_struct_size=DeployRequest.SIZE
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L1351-1357)
```text
    let success = reduce_syscall_gas_and_write_response_header(
        total_gas_cost=SEND_MESSAGE_TO_L1_GAS_COST, request_struct_size=SendMessageToL1Request.SIZE
    );
    if (success == FALSE) {
        // Not enough gas to execute the syscall.
        return ();
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L1359-1373)
```text
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
```
