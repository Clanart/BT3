### Title
Deploy Syscall Hardcodes `failure_flag=0`, Masking Constructor Reverts and Enabling Permanent Fund Freezing — (`execution/syscall_impls.cairo`)

---

### Summary

The `execute_deploy` syscall implementation in `syscall_impls.cairo` unconditionally writes `failure_flag=0` in the response header, regardless of whether the constructor execution succeeded or reverted. Because the calling contract always receives a success response, it cannot detect a failed deployment and may proceed to transfer funds into an uninitialized contract, permanently freezing them.

---

### Finding Description

In `execute_deploy` (lines 527–553 of `syscall_impls.cairo`), after invoking `deploy_contract`, the response header is written with a hardcoded `failure_flag=0`:

```cairo
// Write the response header.
// TODO(Yoni, 1/1/2026): support failures.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
``` [1](#0-0) 

The `execute_deploy` function carries `revert_log` as an implicit argument, and `deploy_contract` has access to it, meaning constructor reverts are structurally supported and the state rollback occurs. However, the outcome of that revert is never surfaced to the caller: the `failure_flag` field that the calling contract reads is always `0`.

Compare this with `contract_call_helper`, which correctly propagates the revert status:

```cairo
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
``` [2](#0-1) 

The `deploy_contract` call in the syscall path returns only `(retdata_size, retdata)` — no `is_reverted` flag — so the syscall handler has no mechanism to distinguish a successful constructor from a reverted one:

```cairo
with remaining_gas {
    let (retdata_size, retdata) = deploy_contract(
        block_context=block_context, constructor_execution_context=constructor_execution_context
    );
}
``` [3](#0-2) 

The `DeployResponse` written to the syscall buffer always carries the contract address and the (possibly error) retdata, but with `failure_flag=0`, so the calling contract's Sierra/CASM code cannot branch on failure:

```cairo
assert [response] = DeployResponse(
    contract_address=contract_address,
    constructor_retdata_start=retdata,
    constructor_retdata_end=retdata + retdata_size,
);
``` [4](#0-3) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

A contract factory pattern that:
1. Calls `deploy` to create a child contract (e.g., a vault or escrow),
2. Reads the returned `contract_address` from the `DeployResponse`,
3. Transfers tokens or ETH to that address,

…will do so even when the constructor reverted. The child contract's storage is rolled back to its empty initial state (no owner, no access-control initialization). Any funds sent to it are permanently locked because the contract's withdrawal or rescue functions depend on state that the constructor was supposed to set.

The attacker controls the class hash of the deployed contract (class declaration is permissionless). By declaring a class whose constructor always reverts, the attacker can trigger this path against any on-chain factory that accepts caller-supplied class hashes.

---

### Likelihood Explanation

**High.** The entry path requires only:
1. Declaring a contract class with a reverting constructor — permissionless for any L2 user.
2. Invoking any on-chain factory contract that accepts a caller-supplied `class_hash` and transfers funds after deployment.

Because `failure_flag` is always `0`, the calling contract has **no way** to detect the failure even if it tries to check. Every contract using the `deploy` syscall is silently exposed.

---

### Recommendation

1. Extend `deploy_contract` to return an `is_reverted: felt` flag alongside `(retdata_size, retdata)`.
2. In `execute_deploy`, use that flag instead of the hardcoded literal:
   ```cairo
   assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
   ```
3. Mirror the pattern already used in `contract_call_helper` (line 433), which correctly propagates `is_reverted` from `select_execute_entry_point_func`. [5](#0-4) 

---

### Proof of Concept

1. **Attacker** declares `MaliciousVault` — a class whose constructor always panics/reverts.
2. **Victim factory** contract exposes `create_vault(class_hash, initial_deposit)`:
   - Calls `deploy(class_hash, ...)` → OS executes constructor → constructor reverts → OS rolls back storage → OS writes `failure_flag=0` to syscall buffer.
   - Factory reads `DeployResponse.failure_flag == 0` → believes deployment succeeded.
   - Factory calls `transfer(vault_address, initial_deposit)` → tokens sent to `vault_address`.
3. `vault_address` exists in the state (address was registered before the constructor ran) but its storage is empty — no owner, no withdrawal logic initialized.
4. Tokens at `vault_address` are permanently frozen; no recovery path exists.

The root cause — `failure_flag=0` hardcoded at line 539 of `syscall_impls.cairo` — is the necessary and sufficient vulnerable step in the OS proof system. [6](#0-5)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L404-448)
```text
func contract_call_helper{
    range_check_ptr,
    syscall_ptr: felt*,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    revert_log: RevertLogEntry*,
    outputs: OsCarriedOutputs*,
}(remaining_gas: felt, block_context: BlockContext*, execution_context: ExecutionContext*) {
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
    }

    let response = cast(syscall_ptr, CallContractResponse*);
    // Advance syscall pointer to the next syscall.
    let syscall_ptr = syscall_ptr + CallContractResponse.SIZE;

    %{ CheckNewCallContractResponse %}

    // Write the response.
    relocate_segment(src_ptr=response.retdata_start, dest_ptr=retdata);
    assert [response] = CallContractResponse(
        retdata_start=retdata, retdata_end=retdata + retdata_size
    );

    return ();
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
