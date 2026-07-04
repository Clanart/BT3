### Title
Deprecated Syscall `contract_call_helper` Panics on Callee Revert Instead of Returning Error Response — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo`)

---

### Summary

The `contract_call_helper` function in the deprecated syscall handler uses a hard `assert is_reverted = 0` instead of writing a failure response when a called contract reverts. This is the direct analog of M-05: a function that is expected to handle errors gracefully (return a sentinel/error value) instead panics, causing the OS execution to abort and the block to become unprovable.

---

### Finding Description

In `deprecated_execute_syscalls.cairo`, `contract_call_helper` dispatches to `select_execute_entry_point_func`, which returns `is_reverted` as a first-class value. The new (Cairo 1) syscall path in `syscall_impls.cairo` handles this correctly — it writes a `failure_flag=1` response header and appends `ERROR_ENTRY_POINT_FAILED` to the retdata. The deprecated path does not:

```cairo
// deprecated_execute_syscalls.cairo, contract_call_helper
let remaining_gas = DEFAULT_INITIAL_GAS_COST;
with remaining_gas {
    let (is_reverted, retdata_size, retdata, _is_deprecated) = select_execute_entry_point_func(
        block_context=block_context, execution_context=execution_context
    );
}
%{ CheckSyscallResponse %}
relocate_segment(src_ptr=call_response.retdata, dest_ptr=retdata);

// The deprecated call syscalls do not support reverts.
assert is_reverted = 0;   // <-- PANICS if callee reverts
```

When `is_reverted = 1` (i.e., the callee — which may be a Cairo 1 contract — reverts), the Cairo VM fails with an assertion error. This aborts OS execution entirely, making the block unprovable.

Compare with the correct handling in the new syscall path (`syscall_impls.cairo`, `contract_call_helper`):

```cairo
if (is_reverted != FALSE) {
    assert retdata[retdata_size] = ERROR_ENTRY_POINT_FAILED;
    tempvar retdata_size = retdata_size + 1;
}
...
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
```

The deprecated path has no equivalent error-response branch.

The same pattern appears in `execute_deploy_syscall` in `deprecated_execute_syscalls.cairo`: the `deploy_contract` call's return value is discarded with no revert check, and the response is written unconditionally before the constructor runs.

---

### Impact Explanation

**High — Network not being able to confirm new transactions (total network shutdown).**

If a Cairo 0 (deprecated) contract issues a `call_contract` syscall to a Cairo 1 contract that reverts, the OS Cairo program will abort at `assert is_reverted = 0`. The block containing that transaction becomes unprovable. If the sequencer's blockifier handles the revert by marking the outer transaction as failed (rather than also aborting), a discrepancy exists: the sequencer considers the block valid, but the OS cannot produce a proof for it. This halts block finalization.

---

### Likelihood Explanation

The attacker-controlled entry path is fully permissionless:

1. **Deploy a Cairo 1 contract** whose target function unconditionally reverts (e.g., `panic_with_felt252('x')`).
2. **Deploy a Cairo 0 contract** that calls the Cairo 1 contract via the `call_contract` syscall.
3. **Submit an invoke transaction** targeting the Cairo 0 contract.
4. The sequencer's blockifier executes the transaction; if it handles the inner revert by marking the outer transaction as failed (not aborting), it includes the transaction in the block.
5. The OS Cairo program processes the block and hits `assert is_reverted = 0` → OS aborts → block is unprovable → network halt.

Cairo 0 contracts calling Cairo 1 contracts is an explicitly supported cross-version interaction (the comment in `contract_call_helper` even notes "the callee may be of version 1.0"), making this scenario realistic.

---

### Recommendation

Replace the hard assertion with the same error-response pattern used in the new syscall path. When `is_reverted != 0`, write a failure response to `call_response` (e.g., set `retdata_size=0`, `retdata=cast(0, felt*)`, and mark the response with a failure indicator) and return normally, rather than asserting. This mirrors the fix recommended in M-05: use graceful error handling instead of propagating a panic to the caller.

---

### Proof of Concept

```
// Cairo 1 contract (always reverts)
#[starknet::contract]
mod Reverter {
    #[external(v0)]
    fn trigger(_self: @ContractState) {
        panic_with_felt252('forced revert');
    }
}

// Cairo 0 contract (calls the reverter)
@external
func call_reverter{...}(reverter_address: felt) {
    let (retdata_len: felt, retdata: felt*) = call_contract(
        contract_address=reverter_address,
        function_selector=TRIGGER_SELECTOR,
        calldata_size=0,
        calldata=cast(0, felt*),
    );
    return ();
}
```

Submitting an invoke transaction to `call_reverter` causes the OS to reach:

```cairo
assert is_reverted = 0;   // is_reverted == 1 → OS aborts
```

in `contract_call_helper` at line 109 of `deprecated_execute_syscalls.cairo`, making the block unprovable. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo (L96-113)
```text
    // Set enough gas for this call to succeed.
    // This is needed since the caller contract is of version 0 and has no notion of gas, and
    // the callee may be of version 1.0.
    let remaining_gas = DEFAULT_INITIAL_GAS_COST;
    with remaining_gas {
        let (is_reverted, retdata_size, retdata, _is_deprecated) = select_execute_entry_point_func(
            block_context=block_context, execution_context=execution_context
        );
    }
    %{ CheckSyscallResponse %}
    relocate_segment(src_ptr=call_response.retdata, dest_ptr=retdata);

    // The deprecated call syscalls do not support reverts.
    assert is_reverted = 0;

    assert [call_response] = CallContractResponse(retdata_size=retdata_size, retdata=retdata);
    return ();
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo (L229-305)
```text
func execute_deploy_syscall{
    range_check_ptr,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    revert_log: RevertLogEntry*,
    outputs: OsCarriedOutputs*,
}(block_context: BlockContext*, caller_execution_context: ExecutionContext*, syscall_ptr: Deploy*) {
    alloc_locals;
    local caller_execution_info: ExecutionInfo* = caller_execution_context.execution_info;
    local caller_address = caller_execution_info.contract_address;

    let request = syscall_ptr.request;
    // Verify deploy_from_zero is either 0 (FALSE) or 1 (TRUE).
    assert request.deploy_from_zero * (request.deploy_from_zero - 1) = 0;
    // Set deployer_address to 0 if request.deploy_from_zero is TRUE.
    let deployer_address = (1 - request.deploy_from_zero) * caller_address;

    let selectable_builtins = &builtin_ptrs.selectable;
    let hash_ptr = selectable_builtins.pedersen;
    with hash_ptr {
        let (contract_address) = get_contract_address(
            salt=request.contract_address_salt,
            class_hash=request.class_hash,
            constructor_calldata_size=request.constructor_calldata_size,
            constructor_calldata=request.constructor_calldata,
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

    // Fill the syscall response, before contract_address is revoked.
    assert syscall_ptr.response = DeployResponse(
        contract_address=contract_address,
        constructor_retdata_size=0,
        constructor_retdata=cast(0, felt*),
    );

    tempvar constructor_execution_context = new ExecutionContext(
        entry_point_type=ENTRY_POINT_TYPE_CONSTRUCTOR,
        class_hash=request.class_hash,
        calldata_size=request.constructor_calldata_size,
        calldata=request.constructor_calldata,
        execution_info=new ExecutionInfo(
            block_info=caller_execution_info.block_info,
            tx_info=caller_execution_info.tx_info,
            caller_address=caller_address,
            contract_address=contract_address,
            selector=CONSTRUCTOR_ENTRY_POINT_SELECTOR,
        ),
        deprecated_tx_info=caller_execution_context.deprecated_tx_info,
    );

    // Set enough gas for this call to succeed; see the comment in 'contract_call_helper'.
    let remaining_gas = DEFAULT_INITIAL_GAS_COST;
    with remaining_gas {
        deploy_contract(
            block_context=block_context, constructor_execution_context=constructor_execution_context
        );
    }

    return ();
}
```

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
