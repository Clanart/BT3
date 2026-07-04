### Title
Hardcoded `failure_flag=0` in `execute_deploy` Ignores Constructor Revert Result — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_deploy` syscall handler unconditionally writes `failure_flag=0` in the `ResponseHeader` regardless of whether the deployed contract's constructor succeeded or reverted. This is a direct analog to the Augur `onTransferOwnership` unchecked-return-value bug: a sub-call's failure status is silently discarded, and the caller is always told the operation succeeded.

---

### Finding Description

In `execute_deploy` (`syscall_impls.cairo`, lines 527–539), after invoking `deploy_contract` (which runs the constructor entry point), the response header is written with a hardcoded `failure_flag=0`:

```cairo
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
``` [1](#0-0) 

The developer TODO comment `// TODO(Yoni, 1/1/2026): support failures.` explicitly acknowledges that constructor failures are not propagated. The `failure_flag` field is the protocol-level signal to the calling contract that the syscall failed; hardcoding it to `0` means the calling contract always receives a "success" response.

Contrast this with the correct pattern used in `contract_call_helper` (lines 413–433), which properly reads `is_reverted` from `select_execute_entry_point_func` and writes it into the response header:

```cairo
with remaining_gas {
    let (is_reverted, retdata_size, retdata, _is_deprecated) = select_execute_entry_point_func(...)
}
...
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
``` [2](#0-1) 

The `deploy_contract` call signature returns only `(retdata_size, retdata)` — no `is_reverted` flag — so even if the constructor reverts internally, the failure status is structurally discarded before the response is written.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

When a calling contract invokes the `deploy` syscall and the constructor reverts:

1. The OS writes `failure_flag=0` (success) into the syscall response.
2. The calling contract reads the response, sees success, and receives the computed `contract_address`.
3. The calling contract may record this address as a valid deployed contract and transfer funds (e.g., ERC-20 tokens or ETH) to it.
4. Because the constructor reverted, the contract at that address has no valid initialized state (class hash may be set but storage is zeroed/reverted). Any subsequent call to it will fail or behave incorrectly.
5. Funds sent to the address are permanently frozen with no recovery path.

This is a protocol-level impact: the OS itself is the component that lies to the calling contract, so no amount of correct contract-level logic can defend against it.

---

### Likelihood Explanation

**Medium-High.** Any unprivileged user can:

- Deploy a contract whose constructor unconditionally reverts (e.g., a constructor that calls `panic`).
- Trigger this via the `deploy` syscall from any Sierra contract.
- The OS will process the syscall, run the constructor, observe the revert, and still write `failure_flag=0`.

No privileged access, leaked keys, or operator collusion is required. The entry path is a standard user-initiated transaction invoking the `deploy` syscall.

---

### Recommendation

Replace the hardcoded `failure_flag=0` with the actual constructor execution result. Refactor `deploy_contract` to return an `is_reverted` flag (analogous to `select_execute_entry_point_func`), and propagate it into the `ResponseHeader`:

```cairo
with remaining_gas {
    let (is_reverted, retdata_size, retdata) = deploy_contract(
        block_context=block_context, constructor_execution_context=constructor_execution_context
    );
}
...
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
```

This mirrors the pattern already used correctly in `contract_call_helper`.

---

### Proof of Concept

1. User submits an invoke transaction calling a contract `Caller` that executes the `deploy` syscall targeting a class whose constructor always reverts.
2. The OS executes `execute_deploy` in `syscall_impls.cairo`.
3. `deploy_contract` runs the constructor; the constructor reverts.
4. Execution reaches line 539: `assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);` — success is reported unconditionally.
5. `Caller` reads `failure_flag=0`, concludes deployment succeeded, and transfers funds to the returned `contract_address`.
6. The contract at that address has no valid initialized state; all subsequent calls to it fail.
7. Funds are permanently frozen. [3](#0-2)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L527-553)
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
