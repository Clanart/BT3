### Title
Deploy Syscall Always Reports Success Regardless of Constructor Revert — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_deploy` syscall implementation in the StarkNet OS unconditionally writes `failure_flag=0` (success) into the response header, even when the constructor of the deployed contract reverts. A calling contract that relies on this response to determine whether deployment succeeded will be misled into believing the contract was deployed, and may subsequently transfer tokens to an address that has no deployed class. Those tokens are permanently locked with no recovery path.

---

### Finding Description

In `execute_deploy` (the syscall handler for the `deploy` system call), after invoking `deploy_contract`, the OS writes the response header with a hardcoded `failure_flag=0`: [1](#0-0) 

```cairo
// TODO(Yoni, 1/1/2026): support failures.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
```

The `execute_deploy` function accepts `revert_log` as an implicit argument, which is the mechanism used throughout the OS to undo state changes when a constructor reverts: [2](#0-1) 

When the constructor reverts, the revert log correctly undoes all state changes — meaning the contract at the computed address ends up with `class_hash=0` (not deployed). However, the response written back to the calling contract still says `failure_flag=0` and includes the `contract_address`: [3](#0-2) 

```cairo
assert [response] = DeployResponse(
    contract_address=contract_address,
    constructor_retdata_start=retdata,
    constructor_retdata_end=retdata + retdata_size,
);
```

The calling contract receives a `contract_address` and a success flag, with no way to distinguish a reverted constructor from a successful one.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any contract that:
1. Calls the `deploy` syscall,
2. Reads the returned `contract_address` from the response (which always has `failure_flag=0`), and
3. Transfers ERC-20 tokens (e.g., STRK, ETH) to that address

will send tokens to an address whose `class_hash=0` (no deployed contract). Since there is no contract at that address to implement a withdrawal or transfer function, those tokens are permanently locked. There is no OS-level or protocol-level recovery mechanism for funds sent to an address with no class.

---

### Likelihood Explanation

**Medium.** The trigger path is:

1. A contract (deployed by any unprivileged user) uses the `deploy` syscall.
2. The constructor of the inner contract reverts — this can happen due to out-of-gas, an explicit `revert`, or any assertion failure inside the constructor.
3. The calling contract, trusting `failure_flag=0`, proceeds to transfer tokens to the returned address.

This is not a hypothetical edge case: any contract pattern that deploys a sub-contract and then immediately funds it (a common DeFi pattern) is vulnerable. An attacker can craft a constructor that reverts under specific conditions (e.g., based on block number or calldata) to trigger the failure at will.

---

### Recommendation

Replace the hardcoded `failure_flag=0` with the actual revert status returned by `deploy_contract`. The `deploy_contract` function already uses the `revert_log` mechanism to track and undo state changes on failure; the return value or revert status should be propagated into the `ResponseHeader`:

```cairo
// Instead of:
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);

// Use:
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
```

If the constructor reverted, the `contract_address` in the `DeployResponse` should also be set to `0` (or the response format should be adjusted) so the calling contract cannot use a stale address.

---

### Proof of Concept

1. User deploys **Contract A** (caller) with the following logic in its constructor or an external function:
   - Call `deploy(class_hash=B_class, constructor_calldata=[...])` where B's constructor always reverts.
   - Read `contract_address` from the `DeployResponse`.
   - Call `transfer(contract_address, amount)` on the fee token contract.

2. The OS executes `execute_deploy`:
   - `deploy_contract` is called; B's constructor reverts; revert log undoes state changes; B's address has `class_hash=0`.
   - The OS writes `ResponseHeader(gas=..., failure_flag=0)` and `DeployResponse(contract_address=B_addr, ...)`.

3. Contract A reads `failure_flag=0` → proceeds to transfer tokens to `B_addr`.

4. The fee token's `transfer` call succeeds (it just updates storage at `B_addr`'s balance slot).

5. `B_addr` has `class_hash=0` — no contract exists there. The tokens are permanently locked with no withdrawal path. [4](#0-3)

### Citations

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L527-555)
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

    return ();
```
