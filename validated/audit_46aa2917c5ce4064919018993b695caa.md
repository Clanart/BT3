### Title
`execute_deploy` Syscall Unconditionally Reports Success on Constructor Revert, Enabling Permanent Fund Freezing — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_deploy` syscall handler in the StarkNet OS always writes `failure_flag=0` (success) to the syscall response, even when the deployed contract's constructor reverts. The OS correctly reverts the state changes via `handle_revert`, but the calling contract is unconditionally told the deployment succeeded. A factory contract that subsequently transfers funds to the "deployed" address will permanently freeze those funds, since the address has no `class_hash` after the revert.

---

### Finding Description

In `syscall_impls.cairo`, `execute_deploy` (lines 452–556) processes the `deploy` syscall. After calling `deploy_contract`, the response header is written with a hardcoded `failure_flag=0`:

```cairo
// TODO(Yoni, 1/1/2026): support failures.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
``` [1](#0-0) 

The `deploy_contract` call does receive the `revert_log` implicit argument (inherited from `execute_deploy`'s own signature), so if the constructor reverts, `handle_revert` is invoked and the state changes are properly rolled back. However, the response written back to the calling contract always carries `failure_flag=0` and a valid `contract_address`:

```cairo
assert [response] = DeployResponse(
    contract_address=contract_address,
    constructor_retdata_start=retdata,
    constructor_retdata_end=retdata + retdata_size,
);
``` [2](#0-1) 

The `execute_deploy` function signature confirms `revert_log` is an implicit argument, meaning constructor reverts are silently absorbed at the state level but never surfaced to the caller: [3](#0-2) 

Contrast this with `execute_entry_point`, which correctly propagates `is_reverted` and calls `handle_revert` before returning the flag to the caller: [4](#0-3) 

The `charge_fee` path, for comparison, uses `non_reverting_select_execute_entry_point_func` which asserts `is_reverted = 0` — meaning fee-transfer reverts abort the proof. The deploy path has no equivalent guard and silently swallows the failure: [5](#0-4) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

After a reverted constructor:
- The deployed `contract_address` has `class_hash = 0` in the committed state (the deployment was rolled back).
- The calling contract receives `failure_flag=0` and the computed `contract_address`.
- If the calling contract transfers tokens (ERC-20 or native) to that address — a standard factory pattern — those tokens are credited to an address with no executable class. No entry point exists to move them out.
- The OS proof is valid and accepted on L1; the frozen balance is committed to the canonical state.

There is no recovery path: the address cannot be re-deployed to the same address with a different constructor (the `prev_value=0` enforcement in `dict_update` for `contract_class_changes` would require the class hash to still be 0, which it is, but the nonce/storage state may differ), and the funds cannot be retrieved without a class that handles transfers.

---

### Likelihood Explanation

Factory contracts that deploy child contracts and immediately fund them are a standard DeFi primitive. The constructor revert can be triggered by:
1. An attacker-controlled argument that causes the constructor to `assert False` or run out of gas.
2. A legitimate race condition (e.g., a uniqueness check that fails because another transaction in the same block already deployed to the same address).
3. Any constructor that validates external state that changes between transaction submission and execution.

An unprivileged transaction sender can craft calldata to a factory contract that causes the constructor to revert while the factory still sends funds, requiring no privileged access.

---

### Recommendation

The `execute_deploy` syscall must propagate the constructor's `is_reverted` flag to the response header. `deploy_contract` should be refactored to return `(is_reverted, retdata_size, retdata)` analogously to `execute_entry_point`, and the response header should be written as:

```cairo
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
```

The existing `// TODO(Yoni, 1/1/2026): support failures.` comment confirms the developers are aware this path is incomplete.

---

### Proof of Concept

1. Declare a class `BombConstructor` whose constructor always reverts (`assert 0 = 1`).
2. Deploy a factory contract `Factory` with the following `execute` logic:
   - Call `deploy(BombConstructor_class_hash, [])` — constructor reverts.
   - Read `failure_flag` from the syscall response — OS returns `0` (success).
   - Call `erc20.transfer(deployed_address, 1_000_000)` — funds sent to the address.
3. Submit an invoke transaction calling `Factory.execute()`.
4. The OS proof is generated and accepted on L1.
5. The ERC-20 balance of `deployed_address` is `1_000_000`, but `get_class_hash_at(deployed_address)` returns `0`.
6. Any call to `deployed_address` fails with `ERROR_ENTRY_POINT_NOT_FOUND`. Funds are permanently frozen. [6](#0-5)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L452-461)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L527-554)
```text
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L192-196)
```text
    let (is_reverted, retdata_size, retdata, is_deprecated) = select_execute_entry_point_func{
        revert_log=revert_log
    }(block_context=block_context, execution_context=execution_context);
    assert is_reverted = 0;
    return (retdata_size, retdata, is_deprecated);
```
