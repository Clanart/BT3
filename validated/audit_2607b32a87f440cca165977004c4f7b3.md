### Title
`execute_deploy` Syscall Silently Ignores Constructor Failure, Always Reports Success - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_deploy` syscall handler in the StarkNet OS unconditionally writes `failure_flag=0` (success) into the syscall response header, regardless of whether the deployed contract's constructor actually succeeded or reverted. This is directly analogous to the reported bug: a failure condition is computed but silently discarded, and execution continues as if no failure occurred. The sibling handler `contract_call_helper` (used for `call_contract` and `library_call`) correctly propagates the `is_reverted` flag into the response.

---

### Finding Description

In `syscall_impls.cairo`, `execute_deploy` calls `deploy_contract` (which runs the constructor entry point and can revert), then unconditionally writes `failure_flag=0` into the response header:

```cairo
// execute_deploy — lines 527–539
with remaining_gas {
    let (retdata_size, retdata) = deploy_contract(
        block_context=block_context, constructor_execution_context=constructor_execution_context
    );
}
// ...
// Write the response header.
// TODO(Yoni, 1/1/2026): support failures.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);  // <== hardcoded 0
``` [1](#0-0) 

The `failure_flag=0` is hardcoded. The TODO comment is an explicit in-code acknowledgment that failure handling is unimplemented.

By contrast, `contract_call_helper` — which handles `call_contract` and `library_call` — correctly reads the `is_reverted` return value from `select_execute_entry_point_func` and propagates it:

```cairo
// contract_call_helper — lines 413–433
with remaining_gas {
    let (is_reverted, retdata_size, retdata, _is_deprecated) = select_execute_entry_point_func(...);
}
if (is_reverted != FALSE) {
    assert retdata[retdata_size] = ERROR_ENTRY_POINT_FAILED;
    ...
}
// Write the response header.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);  // <== correct
``` [2](#0-1) 

The `deploy_contract` function is called with `revert_log` and `remaining_gas` as implicit arguments, confirming it participates in the revert mechanism and can fail. The same function is called in `execute_deploy_account_transaction` with a freshly initialized `revert_log`, confirming the constructor can revert: [3](#0-2) 

The `EntryPointReturnValues` struct explicitly defines `failure_flag` as the revert indicator, and `execute_entry_point` returns `is_reverted` to callers: [4](#0-3) 

---

### Impact Explanation

When a contract invokes the `deploy` syscall and the constructor reverts:

1. The OS writes `failure_flag=0` into the syscall response — the calling contract's Sierra code reads this and concludes the deploy **succeeded**.
2. The constructor's state changes are rolled back via the revert log, leaving the deployed contract in an uninitialized state (or non-existent if the class-hash write is also reverted).
3. The calling contract, believing the deploy succeeded, may store the returned contract address and immediately transfer funds to it.
4. Those funds are sent to a contract whose constructor-initialized state is absent. Depending on the contract's logic, the funds may be permanently unrecoverable — **permanent freezing of funds (Critical)**.

This is a state-transition correctness flaw: the OS output certifies a successful deploy that did not actually succeed, corrupting the calling contract's view of the world in a provably incorrect way.

---

### Likelihood Explanation

Any Sierra/Cairo contract that:
- Calls the `deploy` syscall, **and**
- Branches on the returned `failure_flag` to decide whether to proceed (e.g., send funds, store the address, emit events)

is affected. This is a standard and common pattern for factory contracts. The attacker-controlled entry path is straightforward: deploy a contract whose constructor is designed to revert (e.g., by passing crafted calldata), then observe that the calling factory contract proceeds as if the deploy succeeded.

---

### Recommendation

Replace the hardcoded `failure_flag=0` in `execute_deploy` with the actual revert status returned by `deploy_contract`. Align the implementation with `contract_call_helper`, which correctly propagates `is_reverted`:

```cairo
// After calling deploy_contract, capture is_reverted and use it:
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
```

The `deploy_contract` function's return signature should be extended to return `is_reverted` (similar to `select_execute_entry_point_func`), or the revert status should be inferred from the revert log state before and after the call.

---

### Proof of Concept

1. Contract A (factory) calls `deploy(class_hash=X, constructor_calldata=[bad_arg])` where the constructor of class X reverts when given `bad_arg`.
2. The OS executes `execute_deploy` → calls `deploy_contract` → constructor reverts → revert log entries written → constructor state changes undone.
3. OS writes `ResponseHeader(gas=..., failure_flag=0)` — success — to the syscall buffer.
4. Contract A reads `failure_flag=0`, concludes deploy succeeded, stores the returned `contract_address`, and calls `transfer(contract_address, amount)` on the fee token.
5. Funds arrive at `contract_address`, which is either non-existent or uninitialized. No withdrawal function is accessible. Funds are permanently frozen.

The root cause is exclusively in `execute_deploy` at: [5](#0-4)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L413-433)
```text
    with remaining_gas {
        let (is_reverted, retdata_size, retdata, _is_deprecated) = select_execute_entry_point_func(
            block_context=block_context, execution_context=execution_context
        );
    }

    if (is_reverted != FALSE) {
        // Append `ERROR_ENTRY_POINT_FAILED` to the retdata.
        assert retdata[retdata_size] = ERROR_ENTRY_POINT_FAILED;
        tempvar retdata_size = retdata_size + 1;
    } else {
        ap += 2;  // Align the stack to avoid revoked references.
        tempvar retdata_size = retdata_size;
    }

    let response_header = cast(syscall_ptr, ResponseHeader*);
    let syscall_ptr = syscall_ptr + ResponseHeader.SIZE;

    // Write the response header.
    with_attr error_message("Predicted gas costs are inconsistent with the actual execution.") {
        assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L638-646)
```text
    with remaining_gas {
        // The constructor entry point runs with a validate call context.
        cap_remaining_gas(max_gas=VALIDATE_MAX_SIERRA_GAS);
        let pre_constructor_gas = remaining_gas;
        let revert_log = init_revert_log();
        deploy_contract{revert_log=revert_log}(
            block_context=block_context, constructor_execution_context=constructor_execution_context
        );
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L67-74)
```text
struct EntryPointReturnValues {
    gas_builtin: felt,
    syscall_ptr: felt*,
    // The failure_flag is 0 if the execution succeeded and 1 if it failed.
    failure_flag: felt,
    retdata_start: felt*,
    retdata_end: felt*,
}
```
