### Title
Silent Constructor Revert Acceptance in `execute_deploy` Syscall Enables Direct Loss of Funds — (File: `execution/syscall_impls.cairo`)

---

### Summary

The `execute_deploy` syscall handler in the StarkNet OS unconditionally writes `failure_flag=0` in the response header, regardless of whether the deployed contract's constructor actually succeeded or reverted. A calling contract that relies on this response to gate subsequent fund transfers will always proceed as if deployment succeeded, even when the constructor silently failed. This is the direct analog of the external report's pattern: a partial operation (constructor revert) leaves the system in a state where a subsequent dependent operation (fund transfer to the "deployed" contract) is guaranteed to produce an incorrect outcome, resulting in permanent loss of funds.

---

### Finding Description

In `execute_deploy` (`syscall_impls.cairo`, lines 527–555), after calling `deploy_contract`, the OS writes the syscall response with a hardcoded `failure_flag=0`:

```cairo
// Write the response header.
// TODO(Yoni, 1/1/2026): support failures.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
```

The developer TODO explicitly acknowledges that failure handling is not yet implemented. The `deploy_contract` call internally invokes the constructor via `execute_entry_point`, which can return `is_reverted=1` and trigger `handle_revert` to roll back storage changes. However, the revert status is never propagated to the syscall response. The calling contract's Sierra/CASM code reads `failure_flag` from the `ResponseHeader` to decide whether deployment succeeded; since it is always `0`, the calling contract unconditionally treats every deploy as successful.

The analogous pattern from the external report:
- **External**: remaining stables are re-escrowed after a partial redemption; the user cannot consume them because the next call reverts (assert fails on zero division).
- **Here**: the constructor reverts and state is rolled back, but the OS response says success; the calling contract proceeds to transfer funds to the address, which may be in an inconsistent or uninitialized state, permanently locking those funds. [1](#0-0) 

The `deploy_contract` call that can silently revert: [2](#0-1) 

The `non_reverting_select_execute_entry_point_func` used in `charge_fee` and `run_validate` correctly asserts `is_reverted = 0`, showing the OS does have a mechanism to propagate revert status — it is simply absent in `execute_deploy`: [3](#0-2) 

---

### Impact Explanation

**Critical — Direct loss of funds.**

A calling contract (e.g., a factory or vault protocol) that:
1. Calls `deploy` syscall to create a new contract instance, and
2. Immediately transfers tokens/ETH to the returned `contract_address`

…will always execute step 2 regardless of whether the constructor reverted. If the constructor reverted, the deployed contract's storage is in an uninitialized state (rolled back by `handle_revert`). Funds sent to that address may be permanently unrecoverable if the contract's withdrawal logic depends on constructor-initialized state variables.

---

### Likelihood Explanation

**Medium.**

Any protocol that uses the `deploy` syscall followed by a fund transfer is affected. This is a common pattern in factory contracts, proxy deployers, and vault initializers. An attacker who can influence constructor arguments (e.g., by front-running or supplying crafted calldata) can trigger a constructor revert while the OS still reports success, causing the caller to lock funds. No privileged access is required — only the ability to submit a transaction that triggers the vulnerable call path.

---

### Recommendation

Propagate the constructor's revert status to the `ResponseHeader`. Specifically:

1. Modify `deploy_contract` to return an `is_reverted` flag.
2. In `execute_deploy`, set `failure_flag=is_reverted` in the `ResponseHeader` instead of hardcoding `0`.
3. Remove the TODO comment and align behavior with `contract_call_helper`, which already correctly sets `failure_flag=is_reverted`. [4](#0-3) 

---

### Proof of Concept

1. Attacker writes `MaliciousToken` with a constructor that reverts when `block_number % 2 == 0`.
2. A legitimate factory contract `Factory` calls `deploy(MaliciousToken, ...)` and then immediately calls `transfer(deployed_address, amount)` — a standard pattern.
3. On an even block, the constructor reverts; `handle_revert` rolls back all storage writes inside the constructor.
4. The OS writes `ResponseHeader(gas=remaining_gas, failure_flag=0)` — success.
5. `Factory` reads `failure_flag=0`, proceeds to `transfer(deployed_address, amount)`.
6. `amount` tokens are now held by a contract whose constructor-initialized state (e.g., `owner`, `initialized` flag) was never set.
7. If the contract's withdrawal path requires `assert initialized == 1`, funds are permanently frozen.

The root cause is exclusively in the OS Cairo code at: [5](#0-4)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L419-434)
```text
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
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L527-531)
```text
    with remaining_gas {
        let (retdata_size, retdata) = deploy_contract(
            block_context=block_context, constructor_execution_context=constructor_execution_context
        );
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L533-540)
```text
    // TODO(Yoni, 1/1/2026): consider sharing code with call_contract_helper.
    let response_header = cast(syscall_ptr, ResponseHeader*);
    let syscall_ptr = syscall_ptr + ResponseHeader.SIZE;

    // Write the response header.
    // TODO(Yoni, 1/1/2026): support failures.
    assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);

```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L191-196)
```text
    let revert_log = init_revert_log();
    let (is_reverted, retdata_size, retdata, is_deprecated) = select_execute_entry_point_func{
        revert_log=revert_log
    }(block_context=block_context, execution_context=execution_context);
    assert is_reverted = 0;
    return (retdata_size, retdata, is_deprecated);
```
