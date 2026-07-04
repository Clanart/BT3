### Title
Deploy Syscall Unconditionally Reports Success on Constructor Revert, Enabling Permanent Fund Locking — (File: `execution/syscall_impls.cairo`)

---

### Summary

The `execute_deploy` syscall handler in `syscall_impls.cairo` hardcodes `failure_flag=0` in its response regardless of whether the deployed contract's constructor succeeded or reverted. Calling contracts (e.g., factory patterns) cannot detect constructor failures and may subsequently transfer funds to an address where no contract was actually deployed, permanently locking those funds.

---

### Finding Description

In `execute_deploy`, after calling `deploy_contract`, the response header is unconditionally written with `failure_flag=0`:

```cairo
// TODO(Yoni, 1/1/2026): support failures.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
``` [1](#0-0) 

The `deploy_contract` function is invoked with the outer `revert_log` as an implicit argument. When the constructor reverts, the revert log mechanism in `revert.cairo` rolls back all state changes — including the `CHANGE_CLASS_ENTRY` that set the contract's `class_hash` — leaving the target address with `class_hash=0` (undeployed). [2](#0-1) 

Despite this rollback, the syscall response always signals success. The calling contract receives `failure_flag=0` and `contract_address` in the `DeployResponse`, with no indication that the constructor failed. [3](#0-2) 

The `deploy_contract` function returns only `(retdata_size, retdata)` — no `is_reverted` flag — so `execute_deploy` has no mechanism to distinguish a successful constructor from a reverted one. [4](#0-3) 

---

### Impact Explanation

**Critical — Permanent Freezing of Funds.**

A factory contract that deploys a sub-contract and then transfers ERC-20 tokens to the returned `contract_address` will send those tokens to an address with `class_hash=0`. In StarkNet, ERC-20 `transfer` writes to the token contract's storage keyed by recipient address; the transfer succeeds regardless of whether the recipient is deployed. However, since no contract exists at that address (the constructor reverted and state was rolled back), there is no entry point to call to retrieve the tokens. The funds are permanently locked with no recovery path.

---

### Likelihood Explanation

Factory patterns — contracts that deploy child contracts and immediately fund them — are a standard DeFi primitive. An unprivileged user can trigger this path by:

1. Calling a factory contract with constructor `calldata` crafted to cause the constructor to revert (e.g., invalid initialization parameters, a failing assertion, or an out-of-gas condition).
2. The OS reports success; the factory funds the address.
3. Funds are locked.

No privileged access is required. The attacker only needs to be able to invoke a factory contract that uses the `deploy` syscall followed by a fund transfer — a pattern explicitly analogous to `topUp()` / `reserveEthAllocations()` in the original report. [5](#0-4) 

---

### Recommendation

Implement proper failure propagation in `execute_deploy`. The `deploy_contract` function should return an `is_reverted` flag (analogous to how `select_execute_entry_point_func` returns `is_reverted` in `contract_call_helper`). When `is_reverted != 0`, set `failure_flag=1` in the `ResponseHeader` and write the revert reason as a `FailureReason` object, consistent with how `contract_call_helper` handles reverts: [6](#0-5) 

This allows calling contracts to detect constructor failures and avoid sending funds to undeployed addresses.

---

### Proof of Concept

1. **Setup**: A factory contract `F` implements a function `deploy_and_fund(calldata, amount)` that calls the `deploy` syscall with the provided `calldata`, then calls `transfer(deployed_address, amount)` on the fee token.

2. **Trigger**: An unprivileged user calls `F.deploy_and_fund(malicious_calldata, 1000)` where `malicious_calldata` causes the target constructor to revert (e.g., `assert 0 = 1` in the constructor body).

3. **OS execution**:
   - `execute_deploy` calls `deploy_contract`; the constructor reverts.
   - The revert log rolls back the `CHANGE_CLASS_ENTRY`, leaving `target_address.class_hash = 0`.
   - `execute_deploy` writes `ResponseHeader(gas=remaining_gas, failure_flag=0)`. [7](#0-6) 

4. **Factory reaction**: `F` reads `failure_flag=0`, concludes deployment succeeded, and calls `transfer(target_address, 1000)` on the ERC-20 fee token contract.

5. **Result**: 1000 tokens are credited to `target_address` in the ERC-20 storage. Since `target_address` has `class_hash=0`, no contract exists there to call. The tokens are permanently frozen with no recovery mechanism — a direct analog to the `ReachFactory` fund-locking vulnerability.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L413-426)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L452-470)
```text
func execute_deploy{
    range_check_ptr,
    syscall_ptr: felt*,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    revert_log: RevertLogEntry*,
    outputs: OsCarriedOutputs*,
}(block_context: BlockContext*, caller_execution_context: ExecutionContext*) {
    alloc_locals;
    let request = cast(syscall_ptr + RequestHeader.SIZE, DeployRequest*);
    local constructor_calldata_start: felt* = request.constructor_calldata_start;
    local constructor_calldata_size = request.constructor_calldata_end - constructor_calldata_start;

    let specific_base_gas_cost = DEPLOY_GAS_COST + DEPLOY_CALLDATA_FACTOR_GAS_COST *
        constructor_calldata_size;
    let (success, remaining_gas) = reduce_syscall_base_gas(
        specific_base_gas_cost=specific_base_gas_cost, request_struct_size=DeployRequest.SIZE
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L527-540)
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L541-554)
```text
    let response = cast(syscall_ptr, DeployResponse*);
    // Advance syscall pointer to the next syscall.
    let syscall_ptr = syscall_ptr + DeployResponse.SIZE;

    %{ CheckNewDeployResponse %}

    // Write the response.
    relocate_segment(src_ptr=response.constructor_retdata_start, dest_ptr=retdata);
    assert [response] = DeployResponse(
        contract_address=contract_address,
        constructor_retdata_start=retdata,
        constructor_retdata_end=retdata + retdata_size,
    );

```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/revert.cairo (L86-91)
```text

    if (selector == CHANGE_CLASS_ENTRY) {
        // Change class entry.
        let class_hash = revert_log_end[0].value;
        return revert_contract_changes();
    }
```
