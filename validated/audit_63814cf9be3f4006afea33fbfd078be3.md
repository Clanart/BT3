### Title
Deploy Syscall Unconditionally Reports Success Regardless of Constructor Revert — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

In `execute_deploy` within `syscall_impls.cairo`, the `failure_flag` field of the `ResponseHeader` is hardcoded to `0` (success) unconditionally, even when the deployed contract's constructor reverts. This is structurally analogous to the reported `withdraw_minter` bug: just as that function contained a check (`assert_one_yocto`) that could never be satisfied because the function lacked `#[payable]`, `execute_deploy` contains a response path that can never report failure because the failure branch is permanently suppressed by a hardcoded constant. Any calling contract is permanently unable to detect a failed deployment, leading to incorrect state assumptions and potential permanent freezing of funds.

---

### Finding Description

In `execute_deploy` (`syscall_impls.cairo`, lines 527–555), after calling `deploy_contract`, the OS writes the syscall response header with `failure_flag` hardcoded to `0`:

```cairo
with remaining_gas {
    let (retdata_size, retdata) = deploy_contract(
        block_context=block_context, constructor_execution_context=constructor_execution_context
    );
}

// TODO(Yoni, 1/1/2026): support failures.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
``` [1](#0-0) 

The `TODO` comment explicitly acknowledges this is incomplete. The `deploy_contract` function returns `(retdata_size, retdata)`, which can contain revert data when the constructor fails. However, the `ResponseHeader` written to `syscall_ptr` always carries `failure_flag=0`, so the calling contract's Sierra/CASM code always observes a successful deployment response, regardless of the actual constructor outcome.

This is reachable via the `DEPLOY_SELECTOR` branch in `execute_syscalls`:

```cairo
if (selector == DEPLOY_SELECTOR) {
    execute_deploy(block_context=block_context, caller_execution_context=execution_context);
    ...
}
``` [2](#0-1) 

When the constructor reverts, the OS revert-log mechanism rolls back the constructor's state changes (including the class hash assignment at the deployed address), leaving the address with `class_hash=0`. Yet the calling contract receives `failure_flag=0` and a valid-looking `contract_address` in the `DeployResponse`. The calling contract has no protocol-level mechanism to distinguish this from a genuine success.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

A factory or vault contract that:
1. Calls the `deploy` syscall to deploy a sub-contract (e.g., a per-user vault),
2. Receives `failure_flag=0` and stores the returned `contract_address` as "deployed",
3. Transfers ERC-20 funds to that address (a standard initialization pattern),

will permanently lock those funds. Because the constructor reverted, the revert log rolls back the class hash assignment, leaving the target address with `class_hash=0`. The ERC-20 `transfer` to that address succeeds at the token-contract level (it is a storage write in the fee token), but since the recipient address has no class, no `__execute__` entry point exists to authorize any future outbound transfer. The funds are irrecoverably frozen.

---

### Likelihood Explanation

The pattern of deploying a sub-contract and immediately funding it is ubiquitous in DeFi (factory contracts, per-user vaults, escrow deployers). Constructor reverts are common causes: out-of-gas during initialization, failed assertions on constructor arguments, or deliberate reverts in edge cases. Because the OS unconditionally suppresses the failure signal, every such contract is silently vulnerable without any code-level mitigation available to the contract developer. The entry path requires only a standard unprivileged `deploy` syscall from any contract.

---

### Recommendation

The `deploy_contract` function should return an `is_reverted` flag (consistent with how `execute_entry_point` returns `is_reverted`), and `execute_deploy` should propagate it to the response header:

```cairo
with remaining_gas {
    let (is_reverted, retdata_size, retdata) = deploy_contract(
        block_context=block_context, constructor_execution_context=constructor_execution_context
    );
}
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
```

This mirrors the pattern used in `contract_call_helper` where `is_reverted` from `select_execute_entry_point_func` is correctly forwarded to the `ResponseHeader`. [3](#0-2) 

---

### Proof of Concept

1. Attacker or user deploys a **Factory** contract on StarkNet.
2. Factory calls the `deploy` syscall targeting a **Vault** class whose constructor contains a conditional revert (e.g., `assert(init_param != 0)` where `init_param=0` is passed).
3. The constructor reverts; the OS revert log rolls back the class hash at the vault address, leaving it at `class_hash=0`.
4. `execute_deploy` writes `ResponseHeader(gas=..., failure_flag=0)` and a `DeployResponse` with the computed `contract_address`.
5. Factory's Sierra code reads `failure_flag=0`, concludes deployment succeeded, and calls the ERC-20 fee token's `transfer` to fund the vault address.
6. The ERC-20 transfer succeeds (it is a storage write in the token contract).
7. The vault address has `class_hash=0`; no `__execute__` entry point exists. The funds are permanently frozen with no recovery path.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L527-539)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L165-173)
```text
    if (selector == DEPLOY_SELECTOR) {
        execute_deploy(block_context=block_context, caller_execution_context=execution_context);
        %{ OsLoggerExitSyscall %}
        return execute_syscalls(
            block_context=block_context,
            execution_context=execution_context,
            syscall_ptr_end=syscall_ptr_end,
        );
    }
```
