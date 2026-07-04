### Title
Deploy Syscall Hardcodes `failure_flag=0`, Silently Masking Constructor Failures and Enabling Permanent Fund Freezing — (File: `execution/syscall_impls.cairo`)

---

### Summary

The `execute_deploy` syscall implementation in the StarkNet OS always writes `failure_flag=0` in the response header, regardless of whether the deployed contract's constructor succeeded or failed. This is an OS-level state-transition bypass: any calling contract that relies on `failure_flag` to gate subsequent fund transfers will be silently misled into treating a failed deployment as a success, permanently locking funds at an uninitialized contract address.

---

### Finding Description

In `execute_deploy` (`syscall_impls.cairo`, lines 534–539), after calling `deploy_contract` and receiving its return values, the OS unconditionally writes a success response:

```cairo
// TODO(Yoni, 1/1/2026): support failures.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
``` [1](#0-0) 

The `failure_flag` field is the sole mechanism by which a calling contract can determine whether a `deploy` syscall succeeded. By hardcoding it to `0`, the OS strips every calling contract of the ability to detect a constructor failure.

Critically, `deploy_contract` is invoked in the syscall path with a return-value signature `(retdata_size, retdata)`:

```cairo
with remaining_gas {
    let (retdata_size, retdata) = deploy_contract(
        block_context=block_context, constructor_execution_context=constructor_execution_context
    );
}
``` [2](#0-1) 

Because the function returns normally (rather than panicking), a constructor revert does not abort the surrounding transaction — it returns failure retdata that the OS then silently discards while still reporting success. This is structurally distinct from the `execute_deploy_account_transaction` path, which passes a `revert_log` and does not return retdata:

```cairo
let revert_log = init_revert_log();
deploy_contract{revert_log=revert_log}(
    block_context=block_context, constructor_execution_context=constructor_execution_context
);
``` [3](#0-2) 

The syscall path has no equivalent revert-log integration and no failure propagation.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

When a constructor fails, the contract is not initialized at the computed `contract_address`. Any ERC-20 or native token transfer subsequently directed to that address by the calling contract (e.g., a factory or bridge contract that funds newly deployed vaults) is irrecoverable: there is no contract code at the address to implement a withdrawal function. Because `failure_flag=0` is always returned, even a correctly written calling contract that checks `failure_flag` before transferring funds will be deceived into proceeding. The funds are permanently frozen.

---

### Likelihood Explanation

**Medium.**

The attack requires two conditions that are both reachable by unprivileged protocol users:

1. **Class declarer**: Any user can declare a Sierra class whose constructor contains a revert path (e.g., always reverts, or reverts under an attacker-controlled condition). This is a standard, permissionless protocol operation.
2. **Factory/bridge contract**: A contract that (a) accepts a caller-supplied class hash, (b) deploys it via the `deploy` syscall, and (c) transfers funds to the returned address. This pattern is common in DeFi factory and bridge designs on StarkNet.

No privileged key, operator access, or external dependency compromise is required. The root cause is entirely within the OS syscall handler.

---

### Recommendation

Implement proper failure propagation in `execute_deploy`. Specifically:

- Inspect the return value of `deploy_contract` to determine whether the constructor succeeded or failed.
- When the constructor fails, write `failure_flag=1` and include the failure reason in the response, consistent with how `contract_call_helper` handles callee failures:

```cairo
if (is_reverted != FALSE) {
    assert retdata[retdata_size] = ERROR_ENTRY_POINT_FAILED;
    ...
}
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
``` [4](#0-3) 

Remove the TODO and align `execute_deploy` with the failure-handling pattern already used in `contract_call_helper`.

---

### Proof of Concept

1. **Attacker declares** a Sierra class `MaliciousVault` whose constructor unconditionally reverts (or reverts when `calldata[0] == ATTACKER_TRIGGER`).
2. **Attacker calls** a factory contract `VaultFactory` that:
   - Accepts a `class_hash` argument from the caller.
   - Invokes the `deploy` syscall with the supplied `class_hash`.
   - On receiving the response, checks `failure_flag` — sees `0` (OS bug).
   - Transfers `N` tokens to `response.contract_address`.
3. **OS execution**:
   - `deploy_contract` runs the constructor of `MaliciousVault`; constructor reverts.
   - `deploy_contract` returns `(retdata_size=error_data, retdata=...)` normally.
   - `execute_deploy` writes `ResponseHeader(gas=remaining_gas, failure_flag=0)`.
   - `VaultFactory` reads `failure_flag=0`, concludes success, transfers `N` tokens to `contract_address`.
4. **Result**: `contract_address` holds `N` tokens but has no deployed contract (constructor failed, no code). The tokens are permanently frozen with no withdrawal path. [5](#0-4)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L419-433)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L452-555)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L642-645)
```text
        let revert_log = init_revert_log();
        deploy_contract{revert_log=revert_log}(
            block_context=block_context, constructor_execution_context=constructor_execution_context
        );
```
