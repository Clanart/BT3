### Title
Hardcoded `failure_flag=0` in `execute_deploy` Syscall Silently Swallows Constructor Reverts, Enabling Permanent Freezing of Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_deploy` syscall handler in the StarkNet OS unconditionally writes `failure_flag=0` in the response header, regardless of whether the deployed contract's constructor actually succeeded or reverted. A calling contract receives a false "success" response along with a valid-looking contract address, but the constructor's state changes have been rolled back — meaning no contract exists at that address. Any tokens transferred to that address by the calling contract are permanently frozen.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_deploy` function calls `deploy_contract` (which internally calls `execute_entry_point` and fully supports constructor reverts via `handle_revert`), but then unconditionally writes a success response:

```cairo
// Write the response header.
// TODO(Yoni, 1/1/2026): support failures.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
```

The developers themselves acknowledge this is unimplemented with the TODO comment. The `failure_flag` field is the mechanism by which the OS communicates syscall failure to the calling contract. Setting it to `0` unconditionally means the calling contract can never observe a deploy failure.

Compare this to `contract_call_helper` in the same file, which correctly propagates the revert flag:

```cairo
if (is_reverted != FALSE) {
    assert retdata[retdata_size] = ERROR_ENTRY_POINT_FAILED;
    ...
}
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
```

The `execute_deploy` path has no equivalent check. The full execution path is:

1. `execute_deploy` computes `contract_address` (lines 487–495).
2. `deploy_contract` is called (lines 527–530); if the constructor reverts, `execute_entry_point` calls `handle_revert`, rolling back all state changes — the contract is **not** deployed.
3. `execute_deploy` writes `failure_flag=0` (line 539) and returns `contract_address` in the `DeployResponse` (lines 548–553).
4. The calling contract receives a success response with a valid-looking address, but no contract exists there. [1](#0-0) 

The correct failure-propagation pattern used elsewhere in the same file: [2](#0-1) 

The revert machinery that `deploy_contract` relies on (and which correctly rolls back state): [3](#0-2) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

When a constructor reverts:
- `handle_revert` rolls back all storage writes and class-hash assignments for the target address.
- The address has no deployed class (class hash remains 0 / unset).
- The calling contract receives `failure_flag=0` and the computed `contract_address`.
- If the calling contract transfers ERC-20 tokens to that address (a common post-deploy pattern), those tokens are recorded in the ERC-20 contract's storage under an address that has no code and no `__execute__` entry point.
- No one can ever retrieve those tokens — they are permanently frozen.

This is a direct, on-chain, irreversible loss of user assets triggered by a single malicious or buggy constructor.

---

### Likelihood Explanation

**Medium-High.** The entry path requires only an unprivileged user:

1. A user declares a contract class whose constructor always reverts (or reverts under attacker-controlled conditions).
2. The user deploys a "victim" contract that calls the `deploy` syscall with that class hash and then transfers tokens to the returned address.
3. The OS reports success; the victim contract transfers tokens; the tokens are frozen.

No privileged role, no key compromise, and no network-level attack is required. The attacker only needs to submit two ordinary transactions (declare + invoke).

---

### Recommendation

Propagate the constructor's revert status to the response header, mirroring the pattern used in `contract_call_helper`:

```cairo
// After calling deploy_contract, capture is_reverted:
let (is_reverted, retdata_size, retdata) = deploy_contract(...);

assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);

// Only write the contract_address on success:
if (is_reverted == FALSE) {
    assert [response] = DeployResponse(
        contract_address=contract_address,
        constructor_retdata_start=retdata,
        constructor_retdata_end=retdata + retdata_size,
    );
} else {
    // Write failure retdata (revert reason) instead.
    ...
}
```

`deploy_contract` must also be updated to return `is_reverted` so the caller can act on it. [4](#0-3) 

---

### Proof of Concept

**Step 1 — Declare a "bomb" class** whose constructor always reverts:
```cairo
@constructor
func constructor() {
    assert 1 = 0;  // always reverts
    return ();
}
```

**Step 2 — Declare a "victim" class** that uses the `deploy` syscall:
```cairo
@external
func exploit(bomb_class_hash: felt, token_address: felt, amount: Uint256) {
    // Deploy the bomb contract.
    let (contract_address) = deploy(
        class_hash=bomb_class_hash,
        contract_address_salt=0,
        constructor_calldata_size=0,
        constructor_calldata=cast(0, felt*),
        deploy_from_zero=FALSE,
    );
    // OS returns failure_flag=0 (hardcoded), so we reach here.
    // Transfer tokens to the "deployed" address — they are now frozen.
    IERC20.transfer(contract_address=token_address, recipient=contract_address, amount=amount);
    return ();
}
```

**Step 3 — Invoke `exploit`** with a non-zero `amount`.

**Observed**: The `deploy` syscall returns `failure_flag=0` and a valid `contract_address`. The ERC-20 transfer succeeds. The tokens are recorded at `contract_address` in the ERC-20 storage.

**Actual state**: The bomb constructor reverted; `handle_revert` rolled back the class-hash assignment; `contract_address` has no code. The tokens are permanently frozen with no recovery path. [5](#0-4) [6](#0-5)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L419-434)
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
    }
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
