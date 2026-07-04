### Title
`execute_deploy` Syscall Unconditionally Writes `failure_flag=0`, Silently Masking Constructor Failures — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_deploy` syscall handler in the StarkNet OS always writes `failure_flag=0` in the `ResponseHeader` regardless of whether the deployed contract's constructor actually succeeded or reverted. This is structurally analogous to the reported bug: just as `SpringXVault` calls `IERC20.approve()` without being able to observe the callee's return value (causing silent failure), the OS calls `deploy_contract(...)` which does not return an `is_reverted` flag, and then unconditionally asserts `failure_flag=0`. Any calling contract that relies on `failure_flag` to gate post-deploy logic (e.g., fund transfers) will be deceived into treating a failed deployment as a success.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_deploy` function handles the `deploy` syscall. After calling `deploy_contract(...)`, it writes the response header with a hardcoded `failure_flag=0`:

```cairo
// Write the response header.
// TODO(Yoni, 1/1/2026): support failures.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
``` [1](#0-0) 

The `deploy_contract` call returns only `(retdata_size, retdata)` — it does not expose an `is_reverted` flag:

```cairo
with remaining_gas {
    let (retdata_size, retdata) = deploy_contract(
        block_context=block_context, constructor_execution_context=constructor_execution_context
    );
}
``` [2](#0-1) 

Contrast this with `contract_call_helper` in the same file, which correctly propagates `is_reverted` into `failure_flag`:

```cairo
let (is_reverted, retdata_size, retdata, _is_deprecated) = select_execute_entry_point_func(...);
...
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
``` [3](#0-2) 

The `deploy_contract` function's return signature is structurally incapable of communicating constructor revert status to `execute_deploy`. The TODO comment explicitly acknowledges this gap. The result is that the OS writes a provably incorrect `failure_flag=0` into the syscall response segment for every deploy whose constructor reverts.

---

### Impact Explanation

When a constructor reverts:

1. The OS writes `failure_flag=0` into the `ResponseHeader` for the `deploy` syscall.
2. The calling Cairo 1 contract reads `failure_flag=0` and proceeds as if the deploy succeeded.
3. The calling contract receives a valid `contract_address` in the `DeployResponse`.
4. The calling contract may transfer funds to that address, store it as a trusted counterparty, or grant it permissions — all based on the false assumption that the constructor completed successfully.
5. The deployed contract exists in state (its class hash is registered) but its constructor-initialized storage was reverted, leaving it in an uninitialized or broken state.
6. Funds sent to this address may be permanently unrecoverable if the contract's withdrawal logic depends on constructor-initialized state.

**Impact: Critical — Permanent freezing of funds / Direct loss of funds.**

---

### Likelihood Explanation

The `deploy` syscall is a standard, unprivileged operation available to any Cairo 1 contract. An attacker can:

- Deploy a factory contract that uses `deploy` and transfers funds to newly deployed contracts.
- Craft constructor calldata that causes the constructor to revert (e.g., by triggering an assertion or running out of gas).
- The factory contract, reading `failure_flag=0`, proceeds to transfer funds to the broken contract.

No privileged access, leaked keys, or operator cooperation is required. The attacker only needs to submit a transaction that calls a factory-pattern contract. This is a realistic and low-barrier attack path.

---

### Recommendation

1. **Fix `deploy_contract`'s return signature** to expose `is_reverted`:
   ```cairo
   let (is_reverted, retdata_size, retdata) = deploy_contract(
       block_context=block_context, constructor_execution_context=constructor_execution_context
   );
   ```

2. **Propagate `is_reverted` into `failure_flag`**, mirroring `contract_call_helper`:
   ```diff
   - assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
   + assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
   ```

3. **Handle the reverted-deploy case** in the response body (e.g., write an error retdata instead of the constructor retdata when `is_reverted=1`), consistent with how `contract_call_helper` appends `ERROR_ENTRY_POINT_FAILED`. [4](#0-3) 

---

### Proof of Concept

1. Deploy a factory contract `F` on StarkNet. `F.__execute__` does:
   - Calls `deploy(class_hash=BOMB_CLASS, constructor_calldata=[...])` where `BOMB_CLASS` has a constructor that always reverts.
   - Reads `response_header.failure_flag` — OS writes `0`, so `F` sees success.
   - Calls `transfer(deployed_address, amount)` to send STRK to the deployed address.
2. Submit an invoke transaction calling `F.__execute__`.
3. The OS executes `execute_deploy`:
   - `deploy_contract` runs the constructor, which reverts; constructor state changes are rolled back via the revert log.
   - OS writes `failure_flag=0` unconditionally.
4. `F` reads `failure_flag=0`, transfers funds to `deployed_address`.
5. `deployed_address` exists in state (class hash registered) but its storage is uninitialized (constructor reverted). Any withdrawal function that depends on constructor-set state is broken.
6. Funds are permanently frozen at `deployed_address`. [5](#0-4)

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
