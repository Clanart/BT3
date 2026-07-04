### Title
Flat Gas Cost for `send_message_to_l1` Payload Allows Unbounded OS Prover Work — (File: `execution/syscall_impls.cairo`)

### Summary

`execute_send_message_to_l1` in the StarkNet OS charges a fixed flat gas cost (`SEND_MESSAGE_TO_L1_GAS_COST = 14470`) regardless of the message payload size, while performing an O(payload_size) `memcpy` in the OS Cairo program. There is no upper bound check on `payload_size`. A malicious contract can craft a maximally large payload, forcing the OS to perform millions of Cairo steps for a trivially small gas charge, potentially causing the block's OS execution to exceed the prover's step capacity and halt the network.

### Finding Description

In `execute_send_message_to_l1`:

```cairo
func execute_send_message_to_l1{range_check_ptr, syscall_ptr: felt*, outputs: OsCarriedOutputs*}(
    contract_address: felt
) {
    alloc_locals;
    let request = cast(syscall_ptr + RequestHeader.SIZE, SendMessageToL1Request*);
    let success = reduce_syscall_gas_and_write_response_header(
        total_gas_cost=SEND_MESSAGE_TO_L1_GAS_COST,   // flat: 14470
        request_struct_size=SendMessageToL1Request.SIZE
    );
    if (success == FALSE) { return (); }

    tempvar payload_start = request.payload_start;
    tempvar payload_size = request.payload_end - payload_start;  // attacker-controlled, no bound check

    assert [outputs.messages_to_l1] = MessageToL1Header(..., payload_size=payload_size);
    memcpy(                                            // O(payload_size) OS steps
        dst=outputs.messages_to_l1 + MessageToL1Header.SIZE,
        src=payload_start,
        len=payload_size
    );
    ...
}
``` [1](#0-0) 

The gas charged is the constant `SEND_MESSAGE_TO_L1_GAS_COST = 14470` — a flat value with no per-element factor for the payload. [2](#0-1) 

By contrast, `deploy` and `meta_tx_v0` both charge a per-element calldata factor to account for OS processing work: [3](#0-2) 

The `payload_size` is derived from `request.payload_end - request.payload_start`, both of which are pointers set by the calling contract. No `assert_nn_le` or similar bound check is applied to `payload_size` before the `memcpy`, unlike other array inputs in the OS: [4](#0-3) [5](#0-4) 

### Impact Explanation

The StarkNet OS is a Cairo program whose execution is proven by a STARK prover. The prover has a maximum provable step count. The OS's `memcpy` loop costs one Cairo step per felt copied. A contract that sends a message with N felts of payload forces the OS to execute N additional steps for only 14470 gas charged.

With `EXECUTE_MAX_SIERRA_GAS = 1100000000` and `STEP_GAS_COST = 100`, a contract can write up to ~11 million felts to memory within its gas budget. Calling `send_message_to_l1` with all of them as payload forces the OS to execute ~11 million extra `memcpy` steps for a flat 14470 gas charge. This disproportionate OS step consumption can push the block's total OS step count beyond the prover's capacity, making the block unprovable. The sequencer uses gas as its proxy for OS work; because the gas accounting is wrong, the sequencer's gas-based block limits do not prevent this. The result is **network not being able to confirm new transactions (total network shutdown)**. [6](#0-5) 

### Likelihood Explanation

Any unprivileged contract deployer can deploy a contract that writes a maximally large array to memory and calls `send_message_to_l1` with it as the payload. No special privilege, leaked key, or operator access is required. The attacker only needs to pay the L2 gas for writing the payload to memory (bounded by their gas budget) and the flat 14470 gas for the syscall. This is a low-cost, reachable attack path.

### Recommendation

1. **Add a per-element gas factor for the payload**, analogous to `DEPLOY_CALLDATA_FACTOR_GAS_COST`:
   ```cairo
   let specific_gas_cost = SEND_MESSAGE_TO_L1_GAS_COST + SEND_MESSAGE_TO_L1_PAYLOAD_FACTOR_GAS_COST * payload_size;
   ```
2. **Add an explicit upper bound check on `payload_size`** before the `memcpy`:
   ```cairo
   assert_nn_le(payload_size, SIERRA_ARRAY_LEN_BOUND - 1);
   ```

### Proof of Concept

1. Deploy a Cairo 1 contract with the following logic in its `__execute__` entrypoint:
   ```rust
   // Write ~10_000_000 felts to an array (costs ~1B gas, within EXECUTE_MAX_SIERRA_GAS)
   let mut payload: Array<felt252> = ArrayTrait::new();
   let mut i: u32 = 0;
   loop {
       if i == 10_000_000 { break; }
       payload.append(0x1337);
       i += 1;
   };
   // Call send_message_to_l1 — OS charges only flat 14470 gas
   // but must memcpy 10_000_000 felts in the OS Cairo program
   starknet::send_message_to_l1_syscall(
       to_address: 0xdeadbeef,
       payload: payload.span()
   ).unwrap();
   ```
2. Submit this transaction to the sequencer. The sequencer sees a gas cost within limits (the contract's gas budget is respected). The OS, however, must execute ~10 million `memcpy` steps to process the payload, far exceeding the expected OS step budget for a transaction of this gas cost.
3. If the block's total OS step count exceeds the prover's capacity, the block cannot be proven, halting the network.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L1346-1374)
```text
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants.cairo (L89-92)
```text
const DEFAULT_INITIAL_GAS_COST = 10000000000;
const VALIDATE_MAX_SIERRA_GAS = 100000000;
const EXECUTE_MAX_SIERRA_GAS = 1100000000;
const DEFAULT_INITIAL_GAS_COST_NO_L2 = VALIDATE_MAX_SIERRA_GAS + EXECUTE_MAX_SIERRA_GAS;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants.cairo (L106-118)
```text
const DEPLOY_CALLDATA_FACTOR_GAS_COST = 4850;
const GET_BLOCK_HASH_GAS_COST = 10840;
const GET_CLASS_HASH_AT_GAS_COST = 10000;
const GET_EXECUTION_INFO_GAS_COST = 12640;
const LIBRARY_CALL_GAS_COST = 89160;
const REPLACE_CLASS_GAS_COST = 10670;
// TODO(Yoni, 1/1/2026): take into account Patricia updates and dict squash.
const STORAGE_READ_GAS_COST = 18070;
const STORAGE_WRITE_GAS_COST = 44970;
const EMIT_EVENT_GAS_COST = 10000;
const SEND_MESSAGE_TO_L1_GAS_COST = 14470;
const META_TX_V0_GAS_COST = 167950;
const META_TX_V0_CALLDATA_FACTOR_GAS_COST = 4850;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L485-485)
```text
    assert_nn_le(tx_execution_context.calldata_size, SIERRA_ARRAY_LEN_BOUND - 1);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L534-534)
```text
    assert_nn_le(constructor_calldata_size, SIERRA_ARRAY_LEN_BOUND - 1);
```
