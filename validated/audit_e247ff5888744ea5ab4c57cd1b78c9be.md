### Title
Deploy Syscall Always Reports Success Regardless of Constructor Outcome, Enabling Undetectable Fund Loss - (File: `crates/apollo_starknet_os_program/src/cairo/starknet/core/os/execution/syscall_impls.cairo`)

### Summary

The `execute_deploy` syscall implementation in the StarkNet OS unconditionally writes `failure_flag=0` in its response header, regardless of whether the deployed contract's constructor succeeded or reverted. This is the direct analog of H-02: just as the 0x `TransformController` assumed only the specified `outputToken` would be returned (ignoring other transformer outputs), the OS here assumes the constructor always succeeds, ignoring the actual execution outcome. Any calling contract that relies on the `failure_flag` to detect constructor failures will receive incorrect information, potentially leading to permanent freezing or direct loss of funds.

### Finding Description

In `execute_deploy` (`syscall_impls.cairo`, lines 534–539), after calling `deploy_contract`, the response header is unconditionally written with `failure_flag=0`:

```cairo
// Write the response header.
// TODO(Yoni, 1/1/2026): support failures.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
``` [1](#0-0) 

The `deploy_contract` function is called and returns `(retdata_size, retdata)`, but crucially does **not** return an `is_reverted` flag:

```cairo
with remaining_gas {
    let (retdata_size, retdata) = deploy_contract(
        block_context=block_context, constructor_execution_context=constructor_execution_context
    );
}
``` [2](#0-1) 

Contrast this with `contract_call_helper`, which correctly propagates the revert flag:

```cairo
let (is_reverted, retdata_size, retdata, _is_deprecated) = select_execute_entry_point_func(...)
...
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
``` [3](#0-2) 

The `execute_entry_point` function in `execute_entry_point.cairo` does correctly handle reverts internally — when `is_reverted != FALSE`, it creates a dummy `OsCarriedOutputs` to discard messages and calls `handle_revert` to roll back storage changes:

```cairo
if (is_reverted != FALSE) {
    // Create a dummy OsCarriedOutputs so that messages to L1 will be discarded.
    %{ GenerateDummyOsOutputSegment %}
    let revert_log = init_revert_log();
    ...
    handle_revert(...)
``` [4](#0-3) 

So the constructor's **storage changes are reverted**, but the **contract address is still registered** (class hash written to `contract_state_changes` before the constructor runs), and the **calling contract is told the deployment succeeded**. The calling contract receives a valid `contract_address` in the `DeployResponse` and has no mechanism to detect that the constructor failed. [5](#0-4) 

### Impact Explanation

**Critical — Direct loss of funds / Permanent freezing of funds.**

A factory contract pattern is common in StarkNet: a contract deploys a child contract and then immediately funds or configures it. The factory relies on `failure_flag` in the `DeployResponse` to know whether to proceed. Because `failure_flag` is always `0`:

1. The factory deploys a vault/escrow contract whose constructor sets the owner/access-control state.
2. The constructor reverts (e.g., out-of-gas, invalid argument, or deliberate revert).
3. The OS reverts the constructor's storage writes, leaving the contract in default (zeroed) state — no owner, no access control.
4. The OS reports `failure_flag=0` to the factory.
5. The factory, believing deployment succeeded, transfers funds to the deployed address.
6. The deployed contract has no owner and no initialized state; funds are permanently locked with no recovery path.

This constitutes **permanent freezing of funds** at the protocol level, because the OS's incorrect accounting of the constructor outcome is the necessary and sufficient cause — not any application-level mistake.

### Likelihood Explanation

**High.** The trigger path is fully controlled by an unprivileged user:

- Any user can invoke the `deploy` syscall from within their contract.
- Any user can craft a constructor that reverts (e.g., by passing invalid calldata, or by deploying a class whose constructor explicitly reverts).
- The OS will always report success.
- Factory/proxy patterns that deploy-and-fund in a single transaction are standard DeFi primitives on StarkNet.

No privileged role, leaked key, or external dependency is required. The bug is in the OS's own syscall dispatch loop, which is a necessary step in every deployment.

### Recommendation

1. Modify `deploy_contract` (in `deploy_contract.cairo`) to return an `is_reverted: felt` flag alongside `(retdata_size, retdata)`, mirroring the signature of `select_execute_entry_point_func`.
2. In `execute_deploy`, use the returned `is_reverted` flag to set `failure_flag` in the `ResponseHeader`, exactly as `contract_call_helper` does:

```cairo
// Proposed fix:
with remaining_gas {
    let (is_reverted, retdata_size, retdata) = deploy_contract(
        block_context=block_context, constructor_execution_context=constructor_execution_context
    );
}
...
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
```

3. Remove the `// TODO(Yoni, 1/1/2026): support failures.` comment once the fix is in place.

### Proof of Concept

**Attacker-controlled entry path:**

```
User submits invoke tx
  └─ Contract A.__execute__()
       └─ syscall: deploy(class_hash=MaliciousVault, constructor_calldata=[...])
            └─ OS: execute_deploy()
                 └─ deploy_contract() → constructor reverts
                 └─ OS writes: ResponseHeader(gas=X, failure_flag=0)  ← BUG
       └─ Contract A reads DeployResponse.failure_flag == 0  (incorrectly)
       └─ Contract A calls: transfer(deployed_address, 1000 ETH)
            └─ Funds sent to uninitialized contract
            └─ Funds permanently frozen (no owner, no withdraw function initialized)
```

The root cause is exclusively in the OS at: [6](#0-5) 

No external dependency, no trusted role, and no network-level attack is required. The bug is triggered by any `deploy` syscall whose constructor reverts.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L527-531)
```text
    with remaining_gas {
        let (retdata_size, retdata) = deploy_contract(
            block_context=block_context, constructor_execution_context=constructor_execution_context
        );
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L534-539)
```text
    let response_header = cast(syscall_ptr, ResponseHeader*);
    let syscall_ptr = syscall_ptr + ResponseHeader.SIZE;

    // Write the response header.
    // TODO(Yoni, 1/1/2026): support failures.
    assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L541-555)
```text
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L288-320)
```text
    if (is_reverted != FALSE) {
        // Create a dummy OsCarriedOutputs so that messages to L1 will be discarded.
        // The dummy is initialized with
        // OsCarriedOutputs(messages_to_l1="empty segment", messages_to_l2=0).
        %{ GenerateDummyOsOutputSegment %}
        // Create a new revert log for the reverted entry point. This will be used to revert the
        // entry point changes after calling `call_execute_syscalls`.
        let revert_log = init_revert_log();
    } else {
        assert outputs = orig_outputs;
        tempvar revert_log = orig_revert_log;
    }
    let builtin_ptrs = return_builtin_ptrs;
    with syscall_ptr {
        call_execute_syscalls(
            block_context=block_context,
            execution_context=execution_context,
            syscall_ptr_end=entry_point_return_values.syscall_ptr,
        );
    }

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
