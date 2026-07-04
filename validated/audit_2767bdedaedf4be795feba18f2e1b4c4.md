### Title
Deploy Syscall Always Reports Success Regardless of Constructor Revert — (`File: execution/syscall_impls.cairo`)

### Summary
The `execute_deploy` function in `syscall_impls.cairo` unconditionally writes `failure_flag=0` to the syscall response, regardless of whether the constructor execution succeeded or failed. A calling contract can never detect a deploy failure, and will proceed as if the deployment succeeded even when the constructor reverted and the contract was never actually initialized.

### Finding Description

In `execute_deploy`, after calling `deploy_contract`, the response header is written with a hardcoded `failure_flag=0`:

```cairo
// Write the response header.
// TODO(Yoni, 1/1/2026): support failures.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
``` [1](#0-0) 

The `deploy_contract` call returns only `(retdata_size, retdata)` — there is no `is_reverted` return value checked, and no failure path exists:

```cairo
with remaining_gas {
    let (retdata_size, retdata) = deploy_contract(
        block_context=block_context, constructor_execution_context=constructor_execution_context
    );
}
``` [2](#0-1) 

Compare this with `contract_call_helper`, which correctly propagates the revert flag:

```cairo
let (is_reverted, retdata_size, retdata, _is_deprecated) = select_execute_entry_point_func(...);
...
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
``` [3](#0-2) 

The `execute_entry_point` function does support reverts — when `is_reverted=1`, it calls `handle_revert` to roll back state changes: [4](#0-3) 

So the OS correctly rolls back the constructor's state changes internally, but then lies to the calling contract by reporting `failure_flag=0`. The calling contract has no way to distinguish a successful deploy from a reverted one.

### Impact Explanation

**Critical — Permanent freezing of funds.**

A contract that calls the `deploy` syscall and then transfers tokens to the newly deployed address (a standard factory pattern) will send funds to an address whose constructor reverted. Because the OS rolled back the constructor's state changes, the deployed address has no initialized class state (or no class hash at all). Any subsequent call to that address will fail. Tokens sent to it via the ERC-20 fee token contract's storage are permanently inaccessible, as there is no class at that address capable of executing a withdrawal.

### Likelihood Explanation

Factory contracts that deploy child contracts and immediately fund them are a standard DeFi pattern. Any such contract on StarkNet is affected. An attacker can craft a class whose constructor reverts under attacker-controlled conditions (e.g., based on a storage value the attacker can manipulate), then induce a victim factory contract to deploy it and send funds to the resulting address. The entry path requires only an unprivileged transaction sender.

### Recommendation

Implement failure propagation in `execute_deploy` analogous to `contract_call_helper`. The `deploy_contract` function should return an `is_reverted` flag, and `execute_deploy` should write `failure_flag=is_reverted` to the response header. The existing TODO comment (`// TODO(Yoni, 1/1/2026): support failures.`) confirms this is a known gap that must be closed.

### Proof of Concept

1. Declare a class `MaliciousChild` whose constructor unconditionally reverts.
2. Deploy a `Factory` contract that:
   - Calls the `deploy` syscall with `MaliciousChild`'s class hash.
   - Reads `response.failure_flag` — always `0` due to the bug.
   - Transfers `N` tokens to `response.contract_address`.
3. Observe: `MaliciousChild`'s constructor reverted; the OS rolled back its state. The address has no class. The `N` tokens are now permanently frozen at that address, with no callable entry point to recover them. [5](#0-4)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L309-320)
```text
    if (is_reverted != FALSE) {
        handle_revert(
            contract_address=execution_context.execution_info.contract_address,
            revert_log_end=revert_log,
        );
        // Restore the original revert log and outputs.
        let revert_log = orig_revert_log;
        let outputs = orig_outputs;
        return (
            is_reverted=is_reverted, retdata_size=retdata_end - retdata_start, retdata=retdata_start
        );
    }
```
