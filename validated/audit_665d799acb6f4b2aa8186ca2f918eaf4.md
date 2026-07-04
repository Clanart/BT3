### Title
Flat Gas Cost for `send_message_to_l1` Does Not Account for Payload Size, Enabling Unbounded `memcpy` in OS Proof Generation — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_send_message_to_l1` function in the StarkNet OS charges a flat, payload-size-independent gas cost (`SEND_MESSAGE_TO_L1_GAS_COST = 14470`) but then unconditionally executes a `memcpy` whose length is entirely attacker-controlled (`payload_size = request.payload_end - payload_start`). The OS-level copy cost is never charged to the transaction. An unprivileged contract caller can force the OS prover to perform arbitrarily large memory copies per transaction at a fixed gas price, degrading or halting block proving.

---

### Finding Description

In `syscall_impls.cairo`, `execute_send_message_to_l1` first deducts a **flat** gas cost:

```cairo
let success = reduce_syscall_gas_and_write_response_header(
    total_gas_cost=SEND_MESSAGE_TO_L1_GAS_COST, request_struct_size=SendMessageToL1Request.SIZE
);
```

`SEND_MESSAGE_TO_L1_GAS_COST` is defined as a single constant with no per-element factor:

```
const SEND_MESSAGE_TO_L1_GAS_COST = 14470;
```

After the flat deduction, the function computes an attacker-controlled `payload_size` and copies it unconditionally into the OS output segment:

```cairo
tempvar payload_start = request.payload_start;
tempvar payload_size = request.payload_end - payload_start;
...
memcpy(
    dst=outputs.messages_to_l1 + MessageToL1Header.SIZE, src=payload_start, len=payload_size
);
```

There is **no upper-bound assertion on `payload_size`** before the `memcpy`. The same pattern exists in the deprecated syscall path:

```cairo
memcpy(
    dst=outputs.messages_to_l1 + MessageToL1Header.SIZE,
    src=syscall.payload_ptr,
    len=syscall.payload_size,
);
```

Contrast this with `execute_deploy` and `execute_meta_tx_v0`, which correctly apply a **per-element factor** to their variable-length inputs:

```
const DEPLOY_GAS_COST = 147120;
const DEPLOY_CALLDATA_FACTOR_GAS_COST = 4850;
// used as: DEPLOY_GAS_COST + DEPLOY_CALLDATA_FACTOR_GAS_COST * constructor_calldata_size

const META_TX_V0_GAS_COST = 167950;
const META_TX_V0_CALLDATA_FACTOR_GAS_COST = 4850;
```

`send_message_to_l1` has no analogous factor constant, making its gas model inconsistent with the rest of the syscall suite.

---

### Impact Explanation

The StarkNet OS is a Cairo program executed by the prover for every block. Every Cairo step in the OS consumes prover resources (trace cells, memory). The `memcpy` in `execute_send_message_to_l1` runs one Cairo step per felt copied. Because the gas charged to the transaction is flat and does not scale with `payload_size`, an attacker can:

1. Pay a fixed, minimal gas cost (14470 units).
2. Force the OS to execute O(N) additional Cairo steps during proof generation for an N-felt payload.

By including many such transactions in a block, the attacker inflates the OS trace size beyond what the gas budget implies, potentially making the block unprovable within the prover's resource limits. This maps to **High — Network not being able to confirm new transactions (total network shutdown)**.

---

### Likelihood Explanation

The entry path requires only an unprivileged contract that calls `send_message_to_l1` with a large payload. No privileged role, leaked key, or external dependency is needed. Any deployed contract (Cairo 1 or CairoZero) can issue this syscall. The deprecated path has no gas accounting at all for payload size, making it even easier to exploit from a CairoZero contract. Likelihood is **High**.

---

### Recommendation

Apply a per-element gas factor to `send_message_to_l1`, mirroring the pattern used by `execute_deploy` and `execute_meta_tx_v0`:

```cairo
const SEND_MESSAGE_TO_L1_GAS_COST = <base>;
const SEND_MESSAGE_TO_L1_PAYLOAD_FACTOR_GAS_COST = <per_felt_cost>;

// In execute_send_message_to_l1:
let specific_gas_cost = SEND_MESSAGE_TO_L1_GAS_COST +
    SEND_MESSAGE_TO_L1_PAYLOAD_FACTOR_GAS_COST * payload_size;
let (success, remaining_gas) = reduce_syscall_base_gas(
    specific_base_gas_cost=specific_gas_cost,
    request_struct_size=SendMessageToL1Request.SIZE
);
```

Additionally, add an explicit `assert_nn_le(payload_size, MAX_PAYLOAD_SIZE)` guard before the `memcpy` in both the new and deprecated syscall paths.

---

### Proof of Concept

**Root cause — flat gas cost:** [1](#0-0) 

**Root cause — unbounded `memcpy` in new syscall path:** [2](#0-1) 

**Root cause — unbounded `memcpy` in deprecated syscall path:** [3](#0-2) 

**Contrast — deploy correctly applies a per-element factor:** [4](#0-3) [5](#0-4) 

**Attack path:**
1. Attacker deploys a contract (Cairo 1 or CairoZero).
2. Contract calls `send_message_to_l1` with `payload_end - payload_start = N` for a large N.
3. Transaction pays only `SEND_MESSAGE_TO_L1_GAS_COST = 14470` gas for the syscall, regardless of N.
4. During OS execution for proof generation, `execute_send_message_to_l1` runs `memcpy(..., len=N)`, consuming N Cairo steps at OS level — uncharged to the transaction.
5. Repeating across many transactions in a block inflates the OS trace beyond provable limits, halting block confirmation.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants.cairo (L105-106)
```text
const DEPLOY_GAS_COST = 147120;
const DEPLOY_CALLDATA_FACTOR_GAS_COST = 4850;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants.cairo (L116-116)
```text
const SEND_MESSAGE_TO_L1_GAS_COST = 14470;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L466-470)
```text
    let specific_base_gas_cost = DEPLOY_GAS_COST + DEPLOY_CALLDATA_FACTOR_GAS_COST *
        constructor_calldata_size;
    let (success, remaining_gas) = reduce_syscall_base_gas(
        specific_base_gas_cost=specific_base_gas_cost, request_struct_size=DeployRequest.SIZE
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L1359-1367)
```text
    tempvar payload_start = request.payload_start;
    tempvar payload_size = request.payload_end - payload_start;

    assert [outputs.messages_to_l1] = MessageToL1Header(
        from_address=contract_address, to_address=request.to_address, payload_size=payload_size
    );
    memcpy(
        dst=outputs.messages_to_l1 + MessageToL1Header.SIZE, src=payload_start, len=payload_size
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo (L700-704)
```text
    memcpy(
        dst=outputs.messages_to_l1 + MessageToL1Header.SIZE,
        src=syscall.payload_ptr,
        len=syscall.payload_size,
    );
```
