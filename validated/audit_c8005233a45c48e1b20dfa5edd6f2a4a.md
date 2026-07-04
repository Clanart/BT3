### Title
Constructor Failure Silently Swallowed in `execute_deploy` Syscall — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_deploy` syscall handler in the StarkNet OS unconditionally writes `failure_flag=0` in the response header, regardless of whether the deployed contract's constructor actually succeeded or failed. A caller contract that deploys a child contract and subsequently transfers funds to it cannot detect a constructor failure, mirroring the M-07 pattern of a missing safe-transfer callback: the recipient (deployed contract) may be in an uninitialized state, and any funds routed to it are permanently frozen.

---

### Finding Description

In `execute_deploy` (`syscall_impls.cairo`, lines 527–554), after calling `deploy_contract`, the OS writes the syscall response header with a **hardcoded** `failure_flag=0`:

```cairo
// TODO(Yoni, 1/1/2026): support failures.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
``` [1](#0-0) 

The `deploy_contract` call returns only `(retdata_size, retdata)` — no `is_reverted` flag — unlike `contract_call_helper`, which correctly reads `is_reverted` from `select_execute_entry_point_func` and writes it into the response:

```cairo
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
``` [2](#0-1) 

The `execute_call_contract` and `execute_library_call` paths both go through `contract_call_helper` and propagate failures correctly. `execute_deploy` is the sole syscall that bypasses this mechanism. [3](#0-2) 

The developer-acknowledged TODO confirms this is a known gap, not an intentional design choice. [4](#0-3) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

A victim contract (e.g., a factory or vault) that:
1. Calls `deploy` to create a child contract, and
2. Immediately transfers tokens or ETH-equivalent assets to the freshly deployed address

…will always observe `failure_flag=0` from the OS, even when the constructor reverted. The child contract is registered at its deterministic address with its class hash set, but its storage is in the default (uninitialized) state because the constructor's writes were rolled back via the revert log. Any assets sent to that address are permanently locked: the contract exists on-chain but lacks the initialization required to process withdrawals or transfers.

---

### Likelihood Explanation

**Medium.**

The pattern of "deploy then fund" is standard in factory contracts, liquidity pool deployers, and account abstraction bootstrapping flows. An attacker who can influence constructor calldata (e.g., by supplying parameters to a public factory function) can craft inputs that cause the constructor to revert. Because the OS always reports success, the factory proceeds to fund the address. No privileged access is required; any unprivileged transaction sender who can invoke a factory-style contract is a viable attacker.

---

### Recommendation

Replace the hardcoded `failure_flag=0` with the actual revert status returned by `deploy_contract`. Align `execute_deploy` with `contract_call_helper`:

1. Change `deploy_contract`'s return signature to include `is_reverted`.
2. Write `ResponseHeader(gas=remaining_gas, failure_flag=is_reverted)`.
3. When `is_reverted=1`, ensure the contract-address state entry is not committed (or is rolled back via the revert log), so no zombie contract exists at the address.

---

### Proof of Concept

```
1. Attacker declares class C whose constructor always panics.
2. Attacker calls victim factory contract F (public entry point).
   F internally calls:
     deploy(class_hash=C, salt=S, calldata=[...])   // constructor panics
     transfer(token, deployed_address, amount)       // funds sent unconditionally
3. OS executes execute_deploy:
   - deploy_contract runs constructor → constructor reverts → state rolled back
   - failure_flag hardcoded to 0 → F sees success
4. F calls transfer → tokens arrive at deployed_address.
5. deployed_address has class C registered but zero/default storage.
   No withdrawal function is reachable because initialization never completed.
6. Tokens are permanently frozen at deployed_address.
``` [5](#0-4)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L402-449)
```text
// Executes the entry point and writes the corresponding response to the syscall_ptr.
// Assumes that syscall_ptr points at the response header.
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
}
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
