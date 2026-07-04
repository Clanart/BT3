### Title
`execute_deploy` Syscall Unconditionally Reports Success, Masking Constructor Failures and Enabling Permanent Fund Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_deploy` syscall implementation in the StarkNet OS always writes `failure_flag=0` to the response header, regardless of whether the deployed contract's constructor succeeded or reverted. A caller contract that relies on this response to decide whether to transfer funds to the newly deployed address will be misled into sending tokens to an address whose constructor state was rolled back, permanently locking those funds.

---

### Finding Description

In `execute_deploy` (`syscall_impls.cairo`, lines 452–556), after calling `deploy_contract`, the OS writes the syscall response header with a hardcoded `failure_flag=0`:

```cairo
// Write the response header.
// TODO(Yoni, 1/1/2026): support failures.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
``` [1](#0-0) 

The TODO comment explicitly acknowledges that failure propagation is not implemented. Compare this to the analogous `contract_call_helper` function, which correctly propagates the revert status:

```cairo
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
``` [2](#0-1) 

`deploy_contract` is called with `revert_log` as an implicit argument, meaning the OS revert mechanism is active during constructor execution. When a constructor reverts, the revert log rolls back the constructor's storage changes — including potentially the class hash assignment — leaving the target address with `class_hash=0` (no deployed code). However, because `failure_flag` is always 0, the calling contract receives a success response and may proceed to transfer tokens to that address. [3](#0-2) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

A caller contract that uses the `deploy` syscall and then transfers tokens to the returned `contract_address` (a standard DeFi pattern: factory deploys a vault, then seeds it) will send those tokens to an address with `class_hash=0` if the constructor reverted. An address with no class hash has no entry points; no function can be called on it. Any ERC-20 tokens or STRK transferred to that address are permanently unrecoverable, matching the "permanent freezing of funds" impact category.

---

### Likelihood Explanation

**Medium.** The trigger conditions are:

1. A contract uses the `deploy` syscall (common in factory patterns, vault deployers, proxy deployers).
2. The constructor of the deployed contract reverts — this can happen due to out-of-gas (an attacker can manipulate the gas budget passed to the constructor), an explicit revert in the constructor logic, or a failed assertion.
3. The caller contract sends tokens to the returned address after the deploy call, trusting the `failure_flag=0` response.

An unprivileged transaction sender can craft a transaction that invokes a factory contract, deliberately providing a gas budget that causes the constructor to run out of gas mid-execution, triggering the revert path while the caller still receives a success response.

---

### Recommendation

Replace the hardcoded `failure_flag=0` with the actual revert status returned from `deploy_contract`, mirroring the pattern used in `contract_call_helper`. The `deploy_contract` function should be updated to return an `is_reverted` flag, and `execute_deploy` should write:

```cairo
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
```

This resolves the TODO and aligns `execute_deploy` with the existing revert-propagation contract established by `contract_call_helper`.

---

### Proof of Concept

1. Attacker submits an invoke transaction calling a factory contract `F`.
2. `F` calls the `deploy` syscall targeting class `C`, passing a gas budget just below what `C`'s constructor requires.
3. `C`'s constructor runs out of gas and reverts; the OS revert log rolls back all state changes including the class hash assignment for the new address `addr`.
4. The OS writes `ResponseHeader(gas=0, failure_flag=0)` — success — to the syscall response.
5. `F` reads `failure_flag=0`, concludes deployment succeeded, and calls `transfer(addr, amount)` on the fee token contract to seed the new vault.
6. `addr` has `class_hash=0`; the transfer succeeds at the ERC-20 level (storage updated), but `addr` has no code to ever move those tokens out.
7. Tokens are permanently frozen at `addr`. [4](#0-3)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L428-434)
```text
    let response_header = cast(syscall_ptr, ResponseHeader*);
    let syscall_ptr = syscall_ptr + ResponseHeader.SIZE;

    // Write the response header.
    with_attr error_message("Predicted gas costs are inconsistent with the actual execution.") {
        assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L452-556)
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
    if (success == FALSE) {
        // Not enough gas to execute the syscall.
        return ();
    }

    local caller_execution_info: ExecutionInfo* = caller_execution_context.execution_info;
    local caller_address = caller_execution_info.contract_address;

    // Verify deploy_from_zero is either 0 (FALSE) or 1 (TRUE).
    tempvar deploy_from_zero = request.deploy_from_zero;
    assert deploy_from_zero * (deploy_from_zero - 1) = 0;
    // Set deployer_address to 0 if request.deploy_from_zero is TRUE.
    let deployer_address = (1 - deploy_from_zero) * caller_address;

    let selectable_builtins = &builtin_ptrs.selectable;
    let hash_ptr = selectable_builtins.pedersen;
    with hash_ptr {
        let (contract_address) = get_contract_address(
            salt=request.contract_address_salt,
            class_hash=request.class_hash,
            constructor_calldata_size=constructor_calldata_size,
            constructor_calldata=constructor_calldata_start,
            deployer_address=deployer_address,
        );
    }
    tempvar builtin_ptrs = new BuiltinPointers(
        selectable=SelectableBuiltins(
            pedersen=hash_ptr,
            range_check=selectable_builtins.range_check,
            ecdsa=selectable_builtins.ecdsa,
            bitwise=selectable_builtins.bitwise,
            ec_op=selectable_builtins.ec_op,
            poseidon=selectable_builtins.poseidon,
            segment_arena=selectable_builtins.segment_arena,
            range_check96=selectable_builtins.range_check96,
            add_mod=selectable_builtins.add_mod,
            mul_mod=selectable_builtins.mul_mod,
        ),
        non_selectable=builtin_ptrs.non_selectable,
    );

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

    return ();
}
```
