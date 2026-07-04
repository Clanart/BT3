### Title
Unbounded `send_message_to_l1` Payload Causes Unbounded OS `memcpy` with Fixed Gas Cost — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_send_message_to_l1` function computes `payload_size = request.payload_end - payload_start` from attacker-controlled syscall fields with no upper bound check, then passes this value directly to `memcpy`. The gas cost is a flat `SEND_MESSAGE_TO_L1_GAS_COST = 14470` regardless of payload size. A malicious contract can set `payload_end - payload_start` to an arbitrarily large value without allocating the corresponding memory, causing the OS Cairo program to attempt an unbounded `memcpy` that exhausts the block's step budget, preventing the block from being proven.

---

### Finding Description

In `execute_send_message_to_l1`: [1](#0-0) 

The gas reduction uses a fixed cost with no per-element component: [1](#0-0) 

Then `payload_size` is computed and used directly in `memcpy` with no bound check: [2](#0-1) 

The `payload_start` and `payload_end` fields come from the contract's syscall segment write — they are attacker-controlled felt values. A contract can write `payload_end = payload_start + N` for any `N` without allocating `N` felts of memory. The OS will then attempt to `memcpy` `N` felts, consuming O(N) Cairo VM steps for a fixed gas cost of 14470.

**Contrast with other variable-size syscalls** that correctly include per-element gas costs:

- `execute_deploy`: `DEPLOY_GAS_COST + DEPLOY_CALLDATA_FACTOR_GAS_COST * constructor_calldata_size` [3](#0-2) 

- `execute_meta_tx_v0`: `META_TX_V0_GAS_COST + META_TX_V0_CALLDATA_FACTOR_GAS_COST * calldata_size` [4](#0-3) 

- `execute_keccak`: `KECCAK_GAS_COST + q * KECCAK_ROUND_COST_GAS_COST` [5](#0-4) 

`send_message_to_l1` has no such per-element cost: [6](#0-5) 

**Contrast with bounded array checks** applied elsewhere in the OS:

- Calldata in `execute_entry_point`: [7](#0-6) 

- L1 handler calldata in `transaction_impls`: [8](#0-7) 

No equivalent bound exists for the `send_message_to_l1` payload.

---

### Impact Explanation

The Cairo VM executes `memcpy` in O(len) steps. If `payload_size` exceeds the block's step budget (typically ~10^8–10^9 steps), the OS Cairo program cannot complete, the block proof fails, and no new transactions can be confirmed. This is a **High: Network not being able to confirm new transactions (total network shutdown)** impact.

Even below the step limit, the OS performs O(payload_size) work for a fixed 14470-gas payment, creating a severe fee/accounting imbalance that can be exploited to degrade block throughput.

---

### Likelihood Explanation

Any unprivileged contract deployer can trigger this. The attacker:
1. Deploys a contract whose function writes `payload_end = payload_start + N` (for large N) to the syscall segment — **without allocating N felts** (Cairo VM allows writing pointer values without backing allocation).
2. Submits an invoke transaction calling that function.
3. Pays only 14470 gas for the syscall.

The attack requires no privileged access, no leaked keys, and no trusted-role cooperation. The entry path is: unprivileged contract deployer → invoke transaction → `execute_send_message_to_l1` → unbounded `memcpy` in OS.

---

### Recommendation

1. **Short term**: Add an upper bound check on `payload_size` before the `memcpy`, analogous to the `SIERRA_ARRAY_LEN_BOUND` check applied to calldata:
   ```cairo
   assert [range_check_ptr] = payload_size;
   assert [range_check_ptr + 1] = payload_size + 2 ** 128 - SIERRA_ARRAY_LEN_BOUND;
   let range_check_ptr = range_check_ptr + 2;
   ```

2. **Long term**: Introduce a per-element gas cost for the payload, mirroring the pattern used by `execute_deploy` (`DEPLOY_CALLDATA_FACTOR_GAS_COST`) and `execute_meta_tx_v0` (`META_TX_V0_CALLDATA_FACTOR_GAS_COST`), so that the OS step cost of `memcpy` is always covered by the charged gas.

---

### Proof of Concept

1. Attacker deploys a contract with a function that:
   - Writes `payload_start = addr` and `payload_end = addr + 10^9` to the syscall segment (no memory allocation needed — just pointer arithmetic on felt values).
   - Issues a `send_message_to_l1` syscall with `to_address = any_l1_address`.
2. Attacker submits an invoke transaction calling this function with sufficient gas to pass `reduce_syscall_gas_and_write_response_header` (14470 gas).
3. The OS executes `execute_send_message_to_l1`:
   - `payload_size = 10^9` (no bound check).
   - `memcpy(..., len=10^9)` begins executing.
4. The OS Cairo program exhausts the block step limit before `memcpy` completes.
5. The block proof fails → no block can be confirmed → **network halt**.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L301-303)
```text
    let specific_base_gas_cost = (
        META_TX_V0_GAS_COST + META_TX_V0_CALLDATA_FACTOR_GAS_COST * calldata_size
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L466-467)
```text
    let specific_base_gas_cost = DEPLOY_GAS_COST + DEPLOY_CALLDATA_FACTOR_GAS_COST *
        constructor_calldata_size;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L941-941)
```text
    let required_gas = KECCAK_GAS_COST + q * KECCAK_ROUND_COST_GAS_COST;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L1351-1353)
```text
    let success = reduce_syscall_gas_and_write_response_header(
        total_gas_cost=SEND_MESSAGE_TO_L1_GAS_COST, request_struct_size=SendMessageToL1Request.SIZE
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L1359-1370)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants.cairo (L116-116)
```text
const SEND_MESSAGE_TO_L1_GAS_COST = 14470;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L222-224)
```text
    assert [range_check_ptr] = calldata_size;
    assert [range_check_ptr + 1] = calldata_size + 2 ** 128 - SIERRA_ARRAY_LEN_BOUND;
    let range_check_ptr = range_check_ptr + 2;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L485-485)
```text
    assert_nn_le(tx_execution_context.calldata_size, SIERRA_ARRAY_LEN_BOUND - 1);
```
