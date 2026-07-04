### Title
Deploy Syscall Always Reports Success Regardless of Constructor Outcome - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

### Summary

In `execute_deploy`, the `ResponseHeader.failure_flag` written back to the calling contract is hardcoded to `0` (success) unconditionally. If the deployed contract's constructor reverts, the calling contract still receives a success response, causing it to proceed under the false assumption that the deployment succeeded.

### Finding Description

The `execute_deploy` function in `syscall_impls.cairo` implements the `deploy` syscall. After calling `deploy_contract`, it writes the syscall response header with `failure_flag` hardcoded to `0`:

```cairo
// Write the response header.
// TODO(Yoni, 1/1/2026): support failures.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
``` [1](#0-0) 

The `execute_deploy` function signature includes `revert_log` as an implicit argument, meaning `deploy_contract` can internally handle constructor reverts via the revert log mechanism and return normally even when the constructor fails. [2](#0-1) 

The `deploy_contract` call returns `(retdata_size, retdata)` with no `is_reverted` return value surfaced to `execute_deploy`: [3](#0-2) 

Contrast this with `contract_call_helper`, which correctly propagates `is_reverted` into the `ResponseHeader`: [4](#0-3) 

The `EntryPointReturnValues.failure_flag` semantics are clearly documented: `0` = success, `1` = failure. The `execute_deploy` path violates this contract by never setting `failure_flag=1`. [5](#0-4) 

### Impact Explanation

**Critical — Direct loss of funds.**

A calling contract that uses the `deploy` syscall and then transfers tokens to the newly deployed address (a common pattern: deploy-then-fund) will proceed with the transfer even when the constructor reverted and the contract was never actually deployed. The constructor's state changes are rolled back via the revert log, leaving the target address empty. Tokens sent to that empty address are permanently inaccessible to the sender. If someone later deploys a different contract at the same address (address derivation is deterministic from salt + class hash + deployer), they gain control of those tokens — constituting direct, permanent loss of funds for the original sender.

### Likelihood Explanation

Any unprivileged user can trigger this by deploying a contract that:
1. Calls the `deploy` syscall with a constructor that conditionally reverts (e.g., based on a storage value the attacker controls).
2. Transfers fee tokens or other ERC-20 tokens to the returned `contract_address` field of the `DeployResponse`.

The `DeployResponse.contract_address` is always written (it is computed before the constructor runs), so the calling contract has a valid-looking address to send funds to. The TODO comment confirms the developers are aware this path is incomplete, making it a known gap rather than an oversight that might be defended elsewhere.

### Recommendation

`deploy_contract` should return an `is_reverted` flag (analogous to `execute_entry_point`'s return value). `execute_deploy` should then write `ResponseHeader(gas=remaining_gas, failure_flag=is_reverted)` and, when `is_reverted != 0`, append `ERROR_ENTRY_POINT_FAILED` to the retdata (matching the pattern in `contract_call_helper`).

### Proof of Concept

1. Attacker deploys contract `A` with the following logic in its `execute` entry point:
   - Call `deploy(class_hash=B, salt=X, ...)` where `B`'s constructor always reverts.
   - Read `DeployResponse.contract_address` → `addr`.
   - Read `DeployResponse` failure flag → **always 0** (hardcoded by OS).
   - Transfer 1000 STRK to `addr` (assuming success).

2. OS execution:
   - `execute_deploy` runs, constructor of `B` reverts, revert log rolls back `B`'s state changes.
   - `failure_flag=0` is written unconditionally.
   - Contract `A` sees success, executes the token transfer.

3. Result: 1000 STRK sits at `addr`, which has no deployed contract. The funds are permanently frozen unless an attacker redeploys a contract at the same address (same salt + class hash + deployer), which they can arrange by controlling the constructor parameters. [6](#0-5)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L452-460)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L527-531)
```text
    with remaining_gas {
        let (retdata_size, retdata) = deploy_contract(
            block_context=block_context, constructor_execution_context=constructor_execution_context
        );
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L533-555)
```text
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L67-74)
```text
struct EntryPointReturnValues {
    gas_builtin: felt,
    syscall_ptr: felt*,
    // The failure_flag is 0 if the execution succeeded and 1 if it failed.
    failure_flag: felt,
    retdata_start: felt*,
    retdata_end: felt*,
}
```
