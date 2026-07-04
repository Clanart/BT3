### Title
`execute_deploy` Syscall Hardcodes `failure_flag=0`, Masking Constructor Reverts and Enabling Permanent Fund Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_deploy` syscall implementation in the StarkNet OS always writes `failure_flag=0` (success) into the response header, regardless of whether the deployed contract's constructor actually succeeded or reverted. A calling contract therefore cannot distinguish a successful deploy from a failed one. If the calling contract subsequently transfers funds to the "deployed" address — a standard deploy-and-fund pattern — those funds are permanently frozen because no contract exists at that address to withdraw them.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_deploy` function calls `deploy_contract` (which accepts a `revert_log` implicit argument, meaning it is capable of handling constructor reverts by rolling back state changes), but then unconditionally writes a success response:

```cairo
// Write the response header.
// TODO(Yoni, 1/1/2026): support failures.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
``` [1](#0-0) 

The `failure_flag` field of `ResponseHeader` is the mechanism by which the OS communicates syscall failure to the calling contract. For `call_contract`, this is correctly set via `contract_call_helper`:

```cairo
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
``` [2](#0-1) 

But for `execute_deploy`, the field is hardcoded to `0`. The TODO comment at line 538 explicitly acknowledges that failure support is unimplemented.

The `deploy_contract` call inside `execute_deploy` inherits the outer `revert_log` implicit argument:

```cairo
func execute_deploy{
    ...
    revert_log: RevertLogEntry*,
    ...
}(...)
``` [3](#0-2) 

This means when a constructor reverts, the OS correctly rolls back the constructor's state changes via the revert log — but then reports `failure_flag=0` to the calling contract. The calling contract has no way to detect the failure.

The `DeployResponse` struct also returns the computed `contract_address`:

```cairo
assert [response] = DeployResponse(
    contract_address=contract_address,
    constructor_retdata_start=retdata,
    constructor_retdata_end=retdata + retdata_size,
);
``` [4](#0-3) 

The calling contract receives a valid-looking `contract_address` with `failure_flag=0`, and proceeds as if the deploy succeeded.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any contract that follows the common deploy-and-fund pattern (deploy a sub-contract, then transfer tokens to it) is vulnerable:

1. The calling contract invokes `deploy` with a class whose constructor reverts.
2. The OS rolls back the constructor's state changes (contract not deployed), but writes `failure_flag=0`.
3. The calling contract reads success, then calls `ERC20.transfer(contract_address, amount)`.
4. The ERC20 transfer succeeds — the balance is now recorded at `contract_address` in the ERC20 storage.
5. No contract exists at `contract_address` (state was reverted), so no `withdraw` or `transfer` function can be called to recover the funds.
6. Funds are permanently frozen.

This is the direct analog of the `rejectRequest()` bug: a cancelled/failed operation is not communicated to the caller, the caller proceeds as if it succeeded, and funds end up in an irrecoverable state.

---

### Likelihood Explanation

The deploy-and-fund pattern is ubiquitous in DeFi and protocol contracts on StarkNet. Any contract that:
- Accepts a user-supplied `class_hash` for deployment, **or**
- Deploys a class whose constructor can conditionally revert (e.g., based on storage state or calldata)

is exploitable by an unprivileged transaction sender. The attacker only needs to declare a contract class with a reverting constructor (a standard `declare` transaction, requiring no privilege) and then trigger a deploy of that class through a vulnerable calling contract.

---

### Recommendation

Replace the hardcoded `failure_flag=0` with the actual outcome of `deploy_contract`. Mirror the pattern used in `contract_call_helper`:

```cairo
// Capture is_reverted from deploy_contract's return value.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
```

If `deploy_contract` reverts, the response should set `failure_flag=1` and include a `FailureReason` payload, consistent with how `call_contract` failures are reported. The calling contract can then branch on the failure flag and avoid sending funds to an undeployed address.

---

### Proof of Concept

1. Attacker declares class `RevertingClass` whose constructor always reverts.
2. Victim contract `VaultFactory` implements:
   ```
   fn deploy_and_fund(class_hash, salt, amount):
       let addr = deploy(class_hash, salt, [])  // failure_flag always 0
       erc20.transfer(addr, amount)              // funds sent to dead address
   ```
3. Attacker calls `VaultFactory.deploy_and_fund(RevertingClass, salt, 1000)`.
4. OS: constructor reverts → state rolled back → `failure_flag=0` written.
5. `VaultFactory` reads success, calls `erc20.transfer(addr, 1000)`.
6. ERC20 balance at `addr` = 1000, no contract at `addr` → funds permanently frozen.

The root cause is exclusively in the OS at: [5](#0-4)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L432-434)
```text
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L534-539)
```text
    let response_header = cast(syscall_ptr, ResponseHeader*);
    let syscall_ptr = syscall_ptr + ResponseHeader.SIZE;

    // Write the response header.
    // TODO(Yoni, 1/1/2026): support failures.
    assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L548-553)
```text
    relocate_segment(src_ptr=response.constructor_retdata_start, dest_ptr=retdata);
    assert [response] = DeployResponse(
        contract_address=contract_address,
        constructor_retdata_start=retdata,
        constructor_retdata_end=retdata + retdata_size,
    );
```
