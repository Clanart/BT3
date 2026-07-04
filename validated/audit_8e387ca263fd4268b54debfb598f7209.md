### Title
Unhandled `is_reverted` Return Value in `deploy_contract` Causes OS Panic on Constructor Failure - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo`)

---

### Summary

`deploy_contract` calls `select_execute_entry_point_func` which returns an `is_reverted` success/failure indicator. Instead of propagating this failure to the caller (so the `deploy` syscall response can set `failure_flag=1`), the code asserts `is_reverted = 0`. A reverting constructor causes a Cairo assertion failure, panicking the OS and making the block unprovable.

---

### Finding Description

In `deploy_contract.cairo`, the constructor is invoked via `select_execute_entry_point_func`, which returns `is_reverted`:

```cairo
let (is_reverted, retdata_size, retdata, _is_deprecated) = select_execute_entry_point_func(
    block_context=block_context, execution_context=constructor_execution_context
);
// ...
// The deprecated deploy syscalls do not support reverts.
assert is_reverted = 0;
```

The comment "The deprecated deploy syscalls do not support reverts" reveals the assertion was written for the deprecated path. However, `deploy_contract` is also called from the **new** `execute_deploy` syscall in `syscall_impls.cairo`, where the developer explicitly acknowledges the gap:

```cairo
// Write the response header.
// TODO(Yoni, 1/1/2026): support failures.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
```

The `failure_flag` is hardcoded to `0` (success) and the `is_reverted` value from the constructor execution is never propagated into the syscall response. Instead, if `is_reverted == 1`, the `assert is_reverted = 0` at line 91 of `deploy_contract.cairo` causes the Cairo VM to panic, aborting the entire block execution.

This is the direct analog to the external report: a function returns a success/failure indicator (`is_reverted`) that is not handled — the caller (`deploy_contract`) asserts success unconditionally rather than propagating the failure, causing a silent-but-fatal OS crash instead of a graceful syscall failure response.

---

### Impact Explanation

When the OS panics due to `assert is_reverted = 0` failing, the Cairo execution trace becomes invalid and the block proof cannot be generated. Any block containing a transaction that triggers a reverting constructor via the `deploy` syscall will be permanently unprovable, halting the network's ability to confirm new transactions.

**Impact: High — Network not being able to confirm new transactions (total network shutdown).**

---

### Likelihood Explanation

A contract deployer can craft a constructor that reverts based on a storage value or block-dependent condition. The sequencer simulates transactions before inclusion; however, if the on-chain state diverges from the simulation state between simulation time and execution time (e.g., a storage slot read by the constructor is modified by a preceding transaction in the same block), the constructor may succeed in simulation but revert during OS execution. This is a realistic race condition in any block with multiple transactions touching shared state. Additionally, the TODO comment dated `1/1/2026` confirms this is a known unresolved gap in the production code path.

---

### Recommendation

In `deploy_contract`, replace the hard assertion with a conditional return that propagates the failure:

```cairo
// Instead of: assert is_reverted = 0;
if (is_reverted != 0) {
    return (is_reverted=1, retdata_size=retdata_size, retdata=retdata);
}
return (is_reverted=0, retdata_size=retdata_size, retdata=retdata);
```

Update `deploy_contract`'s return signature to include `is_reverted`. In `execute_deploy` (`syscall_impls.cairo`), use the returned `is_reverted` to set `failure_flag` in the `ResponseHeader` instead of hardcoding `0`.

---

### Proof of Concept

1. Deploy a contract whose constructor reads a storage slot `S` and panics if `S == 1`.
2. In the same block, include two transactions: (a) a write setting `S = 1`, and (b) a transaction calling the `deploy` syscall for the above contract.
3. The sequencer simulates (b) before (a) is applied, so `S == 0` and the constructor succeeds in simulation — the transaction is included.
4. During OS block execution, (a) executes first, setting `S = 1`. Then (b) executes: the constructor reads `S == 1` and reverts, returning `is_reverted = 1`.
5. `deploy_contract.cairo` line 91 executes `assert is_reverted = 0` → assertion fails → OS panics → block is unprovable → network halt. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L28-29)
```text
// TODO(Yoni, 1/1/2027): handle failures.
func deploy_contract{
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L82-92)
```text
    let (is_reverted, retdata_size, retdata, _is_deprecated) = select_execute_entry_point_func(
        block_context=block_context, execution_context=constructor_execution_context
    );

    // Entries before this point belong to the deployed contract.
    assert [revert_log] = RevertLogEntry(selector=CHANGE_CONTRACT_ENTRY, value=contract_address);
    let revert_log = &revert_log[1];

    // The deprecated deploy syscalls do not support reverts.
    assert is_reverted = 0;
    return (retdata_size=retdata_size, retdata=retdata);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L527-539)
```text
    with remaining_gas {
        let (retdata_size, retdata) = deploy_contract(
            block_context=block_context, constructor_execution_context=constructor_execution_context
        );
    }

    // TODO(Yoni, 1/1/2026): consider sharing code with call_contract_helper.
    let response_header = cast(syscall_ptr, ResponseHeader*);
    let syscall_ptr = syscall_ptr + ResponseHeader.SIZE;

    // Write the response header.
    // TODO(Yoni, 1/1/2026): support failures.
    assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
```
