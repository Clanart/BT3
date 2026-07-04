### Title
`execute_deploy` Syscall Unconditionally Reports `failure_flag=0`, Enabling Permanent Freezing of Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_deploy` syscall implementation in the StarkNet OS always writes `failure_flag=0` in the response header, regardless of whether the deployed contract's constructor actually succeeded or failed. A calling contract receives a "success" response and the computed contract address even when the constructor reverted. If the caller subsequently transfers funds to that address, those funds are permanently frozen because no contract was actually initialized there.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_deploy` function orchestrates contract deployment by calling `deploy_contract` and then writing the syscall response. The critical flaw is at lines 538–539:

```cairo
// Write the response header.
// TODO(Yoni, 1/1/2026): support failures.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
``` [1](#0-0) 

The `failure_flag` field is hardcoded to `0` (success) unconditionally. The inline TODO comment explicitly acknowledges that failure propagation is not yet implemented.

The `execute_deploy` function signature includes `revert_log: RevertLogEntry*` as an implicit argument: [2](#0-1) 

This means `deploy_contract` receives the `revert_log` implicitly and can roll back constructor state changes on failure. The constructor's state changes are reverted, leaving the computed address with `class_hash=0` (no deployed code). However, the response to the calling contract still says `failure_flag=0` and includes the computed contract address as if deployment succeeded.

Contrast this with `execute_deploy_account_transaction` in `transaction_impls.cairo`, which explicitly initializes a fresh revert log and handles the constructor result: [3](#0-2) 

That path correctly accounts for constructor failure. The syscall path does not.

The `deploy_contract` call inside `execute_deploy`: [4](#0-3) 

Returns `(retdata_size, retdata)` but the return values are never inspected for a failure indicator before the hardcoded-success response is written.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

When a constructor fails:
1. The revert log rolls back all constructor state changes; the address retains `class_hash=0`.
2. The calling contract receives `failure_flag=0` and the computed address.
3. The calling contract, believing deployment succeeded, transfers ERC20 tokens (or any asset) to that address.
4. Because no contract exists at that address (`class_hash=0`), no `withdraw` or `transfer` entry point can ever be invoked on it.
5. The funds are permanently locked with zero recovery path — an exact structural analog to the `ImmutableBundle` report where tokens sent via the wrong function are locked forever.

---

### Likelihood Explanation

**Medium.** Constructor failures are reachable by any unprivileged transaction sender through:

- Providing constructor calldata that triggers an assertion failure inside the constructor.
- Exhausting the gas budget allocated to the constructor (the `remaining_gas` passed into `deploy_contract` is attacker-influenced via the `deploy` syscall's `RequestHeader.gas` field).
- Any other revert condition inside the constructor logic.

Factory-pattern contracts — which deploy a child contract and immediately fund it in the same transaction — are the primary at-risk pattern. This is a common DeFi primitive on StarkNet.

---

### Recommendation

Inspect the return value of `deploy_contract` for a failure indicator (or propagate the revert status through the revert log) and conditionally set `failure_flag=1` in the response header when the constructor failed. The computed `contract_address` should still be returned so the caller can distinguish "deployed but failed" from "not attempted," but the failure flag must be set so the caller can branch correctly and not send funds to the address. This is already acknowledged by the existing TODO comment at line 538.

---

### Proof of Concept

1. **Attacker** declares a contract class `BrokenToken` whose constructor always reverts (e.g., `assert 1 = 0`).
2. **VictimFactory** contract calls the `deploy` syscall with `class_hash = BrokenToken`, some salt, and constructor calldata.
3. The OS computes the deterministic `contract_address`, calls `deploy_contract`, the constructor reverts, the revert log rolls back state — `contract_address` now has `class_hash=0`.
4. `execute_deploy` writes `ResponseHeader(gas=remaining_gas, failure_flag=0)` and `DeployResponse(contract_address=contract_address, ...)`. [5](#0-4) 

5. **VictimFactory** reads `failure_flag=0`, reads `contract_address` from the response, and calls `erc20.transfer(recipient=contract_address, amount=X)`.
6. The ERC20 transfer succeeds (it only updates storage in the token contract). `contract_address` now holds balance `X` in the ERC20 ledger.
7. Because `class_hash=0` at `contract_address`, no entry point can ever be dispatched there. The `X` tokens are permanently frozen with no rescue path.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L527-531)
```text
    with remaining_gas {
        let (retdata_size, retdata) = deploy_contract(
            block_context=block_context, constructor_execution_context=constructor_execution_context
        );
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L534-554)
```text
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L642-646)
```text
        let revert_log = init_revert_log();
        deploy_contract{revert_log=revert_log}(
            block_context=block_context, constructor_execution_context=constructor_execution_context
        );
    }
```
