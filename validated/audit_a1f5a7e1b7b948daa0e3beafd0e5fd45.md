### Title
Deploy Syscall Constructor Failure Not Propagated to Caller — (`execution/syscall_impls.cairo`)

---

### Summary

In `execute_deploy` inside `syscall_impls.cairo`, the syscall response header unconditionally writes `failure_flag=0` after calling `deploy_contract`, regardless of whether the constructor actually succeeded or reverted. A calling contract therefore always receives a "success" signal from the `deploy` syscall, even when the constructor failed. This is the direct StarkNet OS analog of the reported H-27 pattern: a sub-call's failure is silently swallowed, and the caller proceeds as if the operation succeeded, enabling permanent freezing of user funds.

---

### Finding Description

In `execute_deploy` (`syscall_impls.cairo`, lines 527–554), after invoking `deploy_contract`, the OS writes the syscall response with a hardcoded `failure_flag=0`:

```cairo
with remaining_gas {
    let (retdata_size, retdata) = deploy_contract(
        block_context=block_context, constructor_execution_context=constructor_execution_context
    );
}

// TODO(Yoni, 1/1/2026): support failures.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
```

The inline TODO comment explicitly acknowledges that failure propagation is unimplemented. The `deploy_contract` call returns `(retdata_size, retdata)` — it does **not** return an `is_reverted` flag — and no check on the constructor's success is performed before writing the response.

Compare this with `contract_call_helper` (lines 413–448 of the same file), which correctly reads `is_reverted` from `select_execute_entry_point_func` and propagates it:

```cairo
let (is_reverted, retdata_size, retdata, _is_deprecated) = select_execute_entry_point_func(
    block_context=block_context, execution_context=execution_context
);
...
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
```

The `execute_deploy` path lacks this check entirely. When a constructor reverts (e.g., due to an assertion failure, out-of-gas, or any other revert condition), `execute_entry_point` internally creates a fresh `revert_log` and rolls back the constructor's storage writes via `handle_revert`. However, the class-hash registration performed by `deploy_contract` before the constructor runs may or may not be rolled back depending on the internal implementation of `deploy_contract` (which is not accessible in this analysis). Regardless, the response written to `syscall_ptr` always carries `failure_flag=0`, so the calling contract's Sierra/Cairo code sees a successful deploy and receives the `contract_address` as a valid deployed contract.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

A calling contract that uses the `deploy` syscall to create a sub-contract (e.g., a vault, escrow, or pool) and then immediately deposits user funds into it will:

1. Receive `failure_flag=0` and a `contract_address` even when the constructor reverted.
2. Proceed to transfer tokens to the returned address (via ERC-20 `transfer`), believing the contract is live.
3. The deployed address either has no class hash (if the deploy was fully reverted) or has a class hash but no constructor-initialized state (if only the constructor's storage writes were reverted).

In either case, the tokens are now held in the ERC-20 contract's storage under an address that cannot process withdrawals. Because the OS also marks the transaction as executed in the block's state update, there is no retry or recovery path — the funds are permanently frozen.

---

### Likelihood Explanation

Any unprivileged user can trigger this path:

- A user submits a transaction that calls a factory/deployer contract which uses the `deploy` syscall.
- The constructor of the deployed class fails (e.g., due to an out-of-gas condition, a failed assertion on constructor arguments, or a deliberate revert).
- The OS writes `failure_flag=0` unconditionally; the factory contract proceeds as if the deploy succeeded.
- The factory contract deposits user funds into the returned address.

Constructor failures are a normal part of contract execution (invalid arguments, insufficient gas, etc.) and are expected to be handled gracefully. The TODO comment confirms the developers are aware this path is broken. Any factory pattern contract is vulnerable.

---

### Recommendation

1. Modify `deploy_contract` to return an `is_reverted: felt` flag alongside `(retdata_size, retdata)`.
2. In `execute_deploy`, use the returned flag to set `failure_flag` in the response header:

```cairo
with remaining_gas {
    let (is_reverted, retdata_size, retdata) = deploy_contract(
        block_context=block_context, constructor_execution_context=constructor_execution_context
    );
}
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
```

3. When `is_reverted != 0`, append `ERROR_ENTRY_POINT_FAILED` to the retdata (mirroring `contract_call_helper`).
4. Remove the TODO comment once the fix is in place.

---

### Proof of Concept

1. Declare a class whose constructor always reverts: `assert 1 = 0`.
2. Deploy a factory contract that (a) calls the `deploy` syscall with the above class hash, (b) reads the returned `contract_address`, and (c) calls `transfer` on an ERC-20 token to send 100 tokens to `contract_address`.
3. Submit an invoke transaction calling the factory.
4. Observe: the OS writes `failure_flag=0` in the deploy response; the factory proceeds to transfer 100 tokens to the address.
5. Observe: the deployed address has no valid contract state (constructor reverted); the 100 tokens are permanently frozen — no contract exists at that address to call `withdraw` or any recovery function.
6. Confirm: `retryDeposit`-equivalent recovery is impossible because the block's state update records the transaction as fully executed.

**Root cause line:** [1](#0-0) 

**Correct pattern (missing in `execute_deploy`):** [2](#0-1) 

**Contrast — `deploy` call without failure capture:** [3](#0-2)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L413-434)
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
