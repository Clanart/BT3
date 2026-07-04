### Title
Deploy Syscall Always Reports Success Regardless of Constructor Failure — (File: `execution/syscall_impls.cairo`)

---

### Summary

The `execute_deploy` syscall implementation in the StarkNet OS unconditionally writes `failure_flag=0` in the response header, regardless of whether the deployed contract's constructor succeeded or reverted. This is a direct analog to H-05: state (the contract registration) is committed before the external call (constructor), and if the call fails, the failure is silently suppressed rather than reported to the caller.

---

### Finding Description

In `execute_deploy` (`syscall_impls.cairo`, lines 527–555), after calling `deploy_contract` with the constructor execution context, the OS writes the response header with a hardcoded `failure_flag=0`:

```cairo
with remaining_gas {
    let (retdata_size, retdata) = deploy_contract(
        block_context=block_context, constructor_execution_context=constructor_execution_context
    );
}

// TODO(Yoni, 1/1/2026): support failures.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
``` [1](#0-0) 

The `deploy_contract` call participates in the revert log mechanism (since `revert_log` is an implicit argument of `execute_deploy`), meaning the constructor's storage and class-hash changes can be rolled back on revert. However, the return value of `deploy_contract` — which carries `(retdata_size, retdata)` — is never inspected for a failure/revert condition, and the `failure_flag` field in the response is always set to `0` (success).

The parallel to H-05 is exact:

| H-05 (Connext) | StarkNet OS analog |
|---|---|
| `approve(callTo, toSend)` before external call | Contract address registered / class hash written before constructor runs |
| `IFulfillHelper.addFunds(...)` call fails | Constructor reverts |
| Approval not reset → `callTo` can pull tokens | `failure_flag` not set to 1 → caller contract proceeds as if deploy succeeded | [2](#0-1) 

The revert log in `revert.cairo` handles `CHANGE_CLASS_ENTRY` and storage-write entries, but the initial class-hash assignment for the newly deployed contract address may not be covered by the revert log, meaning the contract address can remain "live" (class hash set) even after the constructor reverts. [3](#0-2) 

---

### Impact Explanation

**Critical — Direct loss of funds / Permanent freezing of funds.**

A caller contract that uses the `deploy` syscall and relies on `failure_flag` to gate subsequent fund transfers cannot distinguish a successful deploy from a failed one. Concretely:

1. Caller invokes `deploy` to create a vault/pair/escrow sub-contract.
2. The constructor reverts (e.g., due to an edge-case condition or attacker-crafted calldata).
3. The OS reports `failure_flag=0` (success) to the caller.
4. The caller transfers user tokens to the "deployed" address.
5. Two outcomes depending on whether the class-hash assignment was reverted:
   - **Class hash still set**: Contract exists but is uninitialized (no owner, no access controls). Funds are immediately stealable by anyone who can call the contract's unguarded entry points.
   - **Class hash reverted**: Contract does not exist. Funds sent to the address are permanently locked with no contract to handle withdrawals.

Both outcomes satisfy the allowed impact criteria.

---

### Likelihood Explanation

The `deploy` syscall is a standard StarkNet primitive used by any contract that programmatically deploys sub-contracts (factory patterns, DeFi pair/vault deployers, account deployers, etc.). The bug is unconditional — every invocation of the `deploy` syscall returns `failure_flag=0`. Any contract that gates fund transfers on deploy success is affected. The TODO comment confirms the developers are aware the failure path is unimplemented, not merely untested. [4](#0-3) 

---

### Recommendation

Inside `execute_deploy`, inspect the result of `deploy_contract` for a revert/failure condition and propagate it to the response header:

```cairo
with remaining_gas {
    let (is_reverted, retdata_size, retdata) = deploy_contract(...);
}
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
```

Additionally, ensure that when the constructor reverts, the initial class-hash assignment for the new contract address is also rolled back via the revert log (analogous to how `execute_replace_class` appends a `CHANGE_CLASS_ENTRY` to the revert log). [5](#0-4) 

---

### Proof of Concept

**Attacker-controlled entry path** (unprivileged transaction sender):

1. Attacker deploys a factory contract `F` on StarkNet. `F.__execute__` does:
   ```
   let (addr) = syscall::deploy(class_hash=VAULT_CLASS, calldata=[...]);
   // failure_flag is always 0 — no revert check possible
   erc20.transfer(recipient=addr, amount=user_funds);
   ```
2. Attacker crafts `calldata` such that `VAULT_CLASS`'s constructor reverts (e.g., passes an invalid argument that triggers an assertion in the constructor).
3. User calls `F.__execute__` with their funds.
4. OS executes: constructor reverts → revert log rolls back constructor state → OS writes `failure_flag=0`.
5. `F` receives `failure_flag=0`, calls `erc20.transfer(addr, user_funds)`.
6. Funds arrive at `addr`. If the class hash was not reverted, the vault contract exists but has no initialized owner — funds are immediately drainable. If the class hash was reverted, the funds are permanently frozen at a non-contract address.

The root cause is exclusively in the OS code at: [6](#0-5)

### Citations

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L877-915)
```text
// Replaces the class.
func execute_replace_class{
    range_check_ptr,
    syscall_ptr: felt*,
    contract_state_changes: DictAccess*,
    revert_log: RevertLogEntry*,
}(contract_address: felt) {
    alloc_locals;
    let request = cast(syscall_ptr + RequestHeader.SIZE, ReplaceClassRequest*);

    // Reduce gas.
    let success = reduce_syscall_gas_and_write_response_header(
        total_gas_cost=REPLACE_CLASS_GAS_COST, request_struct_size=ReplaceClassRequest.SIZE
    );
    if (success == FALSE) {
        // Not enough gas to execute the syscall.
        return ();
    }

    let class_hash = request.class_hash;

    // TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}

    tempvar new_state_entry = new StateEntry(
        class_hash=class_hash, storage_ptr=state_entry.storage_ptr, nonce=state_entry.nonce
    );

    dict_update{dict_ptr=contract_state_changes}(
        key=contract_address,
        prev_value=cast(state_entry, felt),
        new_value=cast(new_state_entry, felt),
    );

    assert [revert_log] = RevertLogEntry(selector=CHANGE_CLASS_ENTRY, value=state_entry.class_hash);
    let revert_log = &revert_log[1];

    return ();
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/revert.cairo (L75-101)
```text
func revert_contract_changes{
    class_hash: felt, storage_ptr: DictAccess*, revert_log_end: RevertLogEntry*
}() {
    alloc_locals;
    let revert_log_end = &revert_log_end[-1];

    tempvar selector = revert_log_end[0].selector;
    if (selector == CHANGE_CONTRACT_ENTRY) {
        // Change contract entries are handled by the caller.
        return ();
    }

    if (selector == CHANGE_CLASS_ENTRY) {
        // Change class entry.
        let class_hash = revert_log_end[0].value;
        return revert_contract_changes();
    }

    // Storage write entry.
    let storage_key = selector;
    let value = revert_log_end[0].value;
    local prev_value;
    %{ ReadStorageKeyForRevert %}
    assert storage_ptr[0] = DictAccess(key=storage_key, prev_value=prev_value, new_value=value);
    %{ WriteStorageKeyForRevert %}
    let storage_ptr = &storage_ptr[1];
    return revert_contract_changes();
```
