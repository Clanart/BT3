### Title
Deploy Syscall Hardcodes `failure_flag=0`, Silently Swallowing Constructor Reverts — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_deploy` syscall implementation in the StarkNet OS always writes `failure_flag=0` (success) into the response header regardless of whether the deployed contract's constructor actually succeeded or reverted. A calling contract that issues a `deploy` syscall will always observe a successful deployment response, even when the constructor reverted and the contract's state was rolled back. This is a direct analog to the bridge service bug: a fallback to a hardcoded default "success" state when the underlying operation may have failed.

---

### Finding Description

In `syscall_impls.cairo`, `execute_deploy` calls `deploy_contract` and then unconditionally writes a success response:

```cairo
with remaining_gas {
    let (retdata_size, retdata) = deploy_contract(
        block_context=block_context, constructor_execution_context=constructor_execution_context
    );
}

// Write the response header.
// TODO(Yoni, 1/1/2026): support failures.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
``` [1](#0-0) 

Two structural defects are present simultaneously:

1. **`deploy_contract` returns only `(retdata_size, retdata)`** — there is no `is_reverted` return value in this call site, unlike `select_execute_entry_point_func` which returns `(is_reverted, retdata_size, retdata, is_deprecated)`. [2](#0-1) 

2. **`failure_flag` is hardcoded to `0`** with an explicit TODO acknowledging the missing failure path: `// TODO(Yoni, 1/1/2026): support failures.` [3](#0-2) 

By contrast, `contract_call_helper` — used by `execute_call_contract` and `execute_library_call` — correctly propagates `is_reverted` into the response header:

```cairo
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
``` [4](#0-3) 

The `deploy` syscall is the only cross-contract call syscall that does not propagate the callee's revert status.

---

### Impact Explanation

**Impact: Critical — Direct loss of funds.**

When a contract (e.g., a factory or vault) issues a `deploy` syscall and the constructor reverts:

1. The OS revert log rolls back the constructor's state changes (storage writes, class hash registration), leaving the newly deployed address in an uninitialized or empty state.
2. The deploy syscall response unconditionally reports `failure_flag=0` and returns the computed `contract_address`.
3. The calling contract, observing a success response with a valid address, proceeds to interact with or transfer funds to that address — for example, seeding it with an initial token balance, registering it as a trusted vault, or transferring ownership.
4. Because the constructor reverted, the contract at that address has no initialized state and no ability to recover or forward funds. Any assets sent there are permanently frozen.

This matches the **"Permanent freezing of funds"** and **"Direct loss of funds"** impact categories.

---

### Likelihood Explanation

**Likelihood: Medium.**

The attacker-controlled entry path is fully reachable by any unprivileged transaction sender:

- Any Cairo 1 contract can issue a `deploy` syscall with an arbitrary class hash and constructor calldata.
- The attacker crafts or reuses a class whose constructor conditionally reverts (e.g., based on a storage value, a block number, or a supplied argument).
- The attacker's calling contract is written to trust the `failure_flag` in the response and act on the returned `contract_address` — a standard and expected pattern for factory contracts.
- No privileged role, leaked key, or operator cooperation is required.

DeFi factory patterns (deploy-then-seed, deploy-then-register) are common on StarkNet, making this a realistic exploitation scenario.

---

### Recommendation

Propagate the constructor's revert status through `deploy_contract`'s return value and use it to set `failure_flag` in the response header, consistent with how `contract_call_helper` handles `call_contract` and `library_call`:

```cairo
// deploy_contract should return (is_reverted, retdata_size, retdata)
with remaining_gas {
    let (is_reverted, retdata_size, retdata) = deploy_contract(...);
}
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
```

If the constructor reverts, the response should carry `failure_flag=1` and the revert data, so calling contracts can branch correctly and not act on a failed deployment.

---

### Proof of Concept

1. Declare class `MaliciousChild` whose constructor always reverts (e.g., `assert 1 = 0`).
2. Deploy class `Factory` whose `__execute__` does:
   - Issues `deploy` syscall for `MaliciousChild` with some salt.
   - Reads `response.failure_flag` — observes `0` (success).
   - Reads `response.contract_address` — receives the deterministic address.
   - Calls `transfer(token, response.contract_address, amount)` to seed the "deployed" contract.
3. Submit an invoke transaction calling `Factory.__execute__`.
4. The OS proves the block. The constructor revert is rolled back (no code at the child address), but the deploy response carries `failure_flag=0`. The token transfer succeeds, sending funds to an address with no code.
5. Funds at `contract_address` are permanently frozen — no contract exists there to recover them. [5](#0-4)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L432-434)
```text
    with_attr error_message("Predicted gas costs are inconsistent with the actual execution.") {
        assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
    }
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
