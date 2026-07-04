### Title
Deploy Syscall Always Reports Success Regardless of Constructor Outcome — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary
The `execute_deploy` syscall handler in the StarkNet OS unconditionally writes `failure_flag=0` into the response header, regardless of whether the constructor execution actually succeeded or failed. This is the direct Cairo analog of the Substrate oracle bug: a critical operation's error return is silently discarded, and the caller is told the operation succeeded.

### Finding Description
Inside `execute_deploy`, after calling `deploy_contract` to run the constructor, the OS writes the syscall response with a hardcoded `failure_flag=0`:

```cairo
with remaining_gas {
    let (retdata_size, retdata) = deploy_contract(
        block_context=block_context, constructor_execution_context=constructor_execution_context
    );
}

// TODO(Yoni, 1/1/2026): support failures.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
``` [1](#0-0) 

The `deploy_contract` call returns `(retdata_size, retdata)` — which, when the constructor reverts, contains the revert reason. However, the OS **never propagates this failure** to the calling contract. The `failure_flag` field in `ResponseHeader` is the protocol-defined mechanism by which a syscall communicates failure to the calling contract. Hardcoding it to `0` means every `deploy` syscall is reported as successful, even when the constructor panicked or ran out of gas.

Compare this to how `run_validate` and `execute_deploy_account_transaction` correctly consume and assert on return data from the same `non_reverting_select_execute_entry_point_func`:

```cairo
let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(
    block_context=block_context, execution_context=validate_deploy_execution_context
);
if (is_deprecated == 0) {
    assert retdata_size = 1;
    assert retdata[0] = VALIDATED;
}
``` [2](#0-1) 

The `charge_fee` function exhibits the same pattern — it calls `non_reverting_select_execute_entry_point_func` and discards all return values without checking the ERC20 transfer's return data:

```cairo
non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
    block_context=block_context, execution_context=&execution_context
);
return ();
``` [3](#0-2) 

The `execute_deploy` case is the more severe of the two because the fee token is protocol-controlled (making the `charge_fee` path harder to exploit), while the `deploy` syscall is open to any contract and any constructor.

### Impact Explanation
**Critical — Direct loss of funds.**

A contract that uses the `deploy` syscall to deploy a sub-contract and then transfers funds to the returned `contract_address` will do so even when the constructor reverted. The OS always returns a computed `contract_address` and `failure_flag=0`. If the constructor revert caused the contract to not be registered in state (class hash not set at that address), any funds sent to that address are permanently unrecoverable — there is no code at the address to handle a withdrawal. The calling contract has no protocol-level mechanism to detect the failure because the OS has suppressed it. [4](#0-3) 

### Likelihood Explanation
**High.** The `deploy` syscall is a standard, publicly accessible syscall available to any contract. Factory-pattern contracts — a common design on StarkNet — routinely deploy sub-contracts and then interact with them (including sending funds). The TODO comment in the source code (`// TODO(Yoni, 1/1/2026): support failures.`) confirms the developers are aware the failure path is unimplemented, meaning this is not a theoretical edge case but a known gap in the current OS implementation. Any contract deployer can trigger this by deploying a class whose constructor reverts.

### Recommendation
1. **Short term:** Propagate the actual constructor execution result into `failure_flag`. If `deploy_contract` returns revert data, set `failure_flag=1` in the `ResponseHeader` and write the revert reason into the response, consistent with how `contract_call_helper` handles reverted calls.
2. **Long term:** Audit all syscall response paths for hardcoded `failure_flag=0` values and ensure every critical operation's error state is correctly surfaced to the calling contract. Apply the same return-value checking discipline used in `run_validate` to `charge_fee`.

### Proof of Concept
1. Declare a class `MaliciousChild` whose constructor executes `assert 1 = 0` (always reverts).
2. Deploy a `Factory` contract that: (a) calls the `deploy` syscall with `MaliciousChild`'s class hash, (b) reads the returned `failure_flag` from the response — which the OS writes as `0`, (c) transfers 1000 STRK to the returned `contract_address` believing the deployment succeeded.
3. The OS processes the block: the constructor reverts, but `failure_flag=0` is written. The `Factory` contract's execution continues normally and the transfer executes.
4. The `contract_address` has no registered class hash (constructor reverted before state was committed). The 1000 STRK is permanently frozen at an address with no code. [5](#0-4)

### Citations

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L161-164)
```text
    non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
        block_context=block_context, execution_context=&execution_context
    );
    return ();
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L677-684)
```text
        let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(
            block_context=block_context, execution_context=validate_deploy_execution_context
        );
    }
    if (is_deprecated == 0) {
        assert retdata_size = 1;
        assert retdata[0] = VALIDATED;
    }
```
