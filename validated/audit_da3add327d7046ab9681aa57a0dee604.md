### Title
Missing `constructor_calldata_size` Bounds Check in `execute_deploy` Syscall Inconsistent with `prepare_constructor_execution_context` — (File: `execution/syscall_impls.cairo`)

---

### Summary

The top-level deploy-account transaction path (`prepare_constructor_execution_context`) enforces `assert_nn_le(constructor_calldata_size, SIERRA_ARRAY_LEN_BOUND - 1)` before creating an `ExecutionContext`. The `execute_deploy` syscall handler in `syscall_impls.cairo` computes `constructor_calldata_size` from attacker-controlled pointer arithmetic with no equivalent bounds check. The same omission exists for `calldata_size` in `execute_call_contract` and `execute_library_call`. An unprivileged contract can exploit this inconsistency to supply an out-of-range felt value as a calldata length to the OS, causing proof failure and a potential network halt or chain split.

---

### Finding Description

**Validated path — `prepare_constructor_execution_context`** in `transaction_impls.cairo`:

```cairo
local constructor_calldata_size;
local constructor_calldata: felt*;
%{ PrepareConstructorExecution %}
assert_nn_le(constructor_calldata_size, SIERRA_ARRAY_LEN_BOUND - 1);   // line 534
``` [1](#0-0) 

This guarantees `constructor_calldata_size` is a valid u32 value (0 ≤ size ≤ 2³²−1) before the `ExecutionContext` is built.

**Unvalidated path — `execute_deploy` syscall** in `syscall_impls.cairo`:

```cairo
local constructor_calldata_start: felt* = request.constructor_calldata_start;
local constructor_calldata_size = request.constructor_calldata_end - constructor_calldata_start;
// ← no assert_nn_le here
``` [2](#0-1) 

The `constructor_calldata_size` is immediately used to build an `ExecutionContext`: [3](#0-2) 

Because Cairo felt arithmetic is modular (mod the Stark prime P), if a contract writes `constructor_calldata_end < constructor_calldata_start` into the syscall segment, the OS computes `constructor_calldata_size ≈ P − k`, a value astronomically larger than `SIERRA_ARRAY_LEN_BOUND − 1 = 2³²−1`.

The same inconsistency exists for `calldata_size` in `execute_call_contract` (line 205) and `execute_library_call` (line 261), both of which compute `calldata_end − calldata_start` without a bounds check, while the top-level invoke path enforces the check at line 485:

```cairo
assert_nn_le(tx_execution_context.calldata_size, SIERRA_ARRAY_LEN_

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L531-534)
```text
    local constructor_calldata_size;
    local constructor_calldata: felt*;
    %{ PrepareConstructorExecution %}
    assert_nn_le(constructor_calldata_size, SIERRA_ARRAY_LEN_BOUND - 1);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L463-464)
```text
    local constructor_calldata_start: felt* = request.constructor_calldata_start;
    local constructor_calldata_size = request.constructor_calldata_end - constructor_calldata_start;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L512-525)
```text
    tempvar constructor_execution_context = new ExecutionContext(
        entry_point_type=ENTRY_POINT_TYPE_CONSTRUCTOR,
        class_hash=request.class_hash,
        calldata_size=constructor_calldata_size,
        calldata=constructor_calldata_start,
        execution_info=new ExecutionInfo(
            block_info=caller_execution_info.block_info,
            tx_info=caller_execution_info.tx_info,
            caller_address=caller_address,
            contract_address=contract_address,
            selector=CONSTRUCTOR_ENTRY_POINT_SELECTOR,
        ),
        deprecated_tx_info=caller_execution_context.deprecated_tx_info,
    );
```
