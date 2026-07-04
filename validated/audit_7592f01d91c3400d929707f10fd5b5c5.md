### Title
Hardcoded `failure_flag=0` in `execute_deploy` Syscall Silently Masks Constructor Failures, Enabling Permanent Fund Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_deploy` syscall handler in the StarkNet OS unconditionally writes `failure_flag=0` in its response header, regardless of whether the deployed contract's constructor actually succeeded. A calling contract always receives a "success" signal and the deployed contract's address, even when the constructor reverted and the contract was never properly initialized. Any funds subsequently transferred to that address are permanently frozen, because the contract at that address has no valid class hash and cannot execute any entry point.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_deploy` function calls `deploy_contract` to run the constructor, then writes the response header with a hardcoded `failure_flag=0`:

```cairo
with remaining_gas {
    let (retdata_size, retdata) = deploy_contract(
        block_context=block_context, constructor_execution_context=constructor_execution_context
    );
}

// TODO(Yoni, 1/1/2026): support failures.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
``` [1](#0-0) 

The `deploy_contract` call signature returns only `(retdata_size, retdata)` — it does not surface an `is_reverted` flag to the caller. Compare this with `contract_call_helper`, which calls `select_execute_entry_point_func` and correctly receives and propagates `is_reverted`:

```cairo
let (is_reverted, retdata_size, retdata, _is_deprecated) = select_execute_entry_point_func(...)
``` [2](#0-1) 

Because `execute_deploy` never checks whether the constructor reverted, the response written to the calling contract's syscall buffer always indicates success. The calling contract's Sierra code observes `failure_flag=0` and a valid `contract_address` in the `DeployResponse`, and proceeds as if the deployment succeeded. [3](#0-2) 

This is the direct analog of the reported "insufficient balance check" vulnerability class: a check (the deploy result) is performed, but the result is always reported as passing regardless of the actual outcome, allowing downstream operations (fund transfers) to proceed on a false premise.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

When a constructor reverts, the StarkNet state changes for that contract address are rolled back via the revert log. The contract at the computed address retains `class_hash=0` (undeployed state). No entry point can be dispatched to an address with `class_hash=0`.

A calling contract that:
1. Calls `deploy(class_hash, salt, calldata)` and receives `failure_flag=0` (always),
2. Reads the returned `contract_address` from the `DeployResponse`,
3. Transfers ERC-20 tokens or native assets to that address,

will permanently lock those funds. The address cannot execute a `transfer` or any withdrawal function because it has no class. There is no recovery path.

---

### Likelihood Explanation

Any unprivileged user can deploy a contract that uses the `deploy` syscall. The attacker does not need any privileged role. The attacker controls:
- The class hash of the sub-contract (whose constructor can be made to revert deterministically),
- The calldata passed to the constructor,
- The logic of the calling contract (which sends funds after the deploy call).

The `deploy` syscall is a standard, publicly accessible StarkNet syscall. The TODO comment in the source code (`// TODO(Yoni, 1/1/2026): support failures.`) confirms this is a known, unresolved gap in the implementation, not a theoretical edge case.

---

### Recommendation

Replace the hardcoded `failure_flag=0` with actual failure propagation from `deploy_contract`. Specifically:

1. Modify `deploy_contract` to return an `is_reverted` flag (analogous to `select_execute_entry_point_func`).
2. In `execute_deploy`, write `ResponseHeader(gas=remaining_gas, failure_flag=is_reverted)`.
3. When `is_reverted=1`, write a `DeployResponse` with `contract_address=0` and appropriate retdata (error reason), consistent with how `contract_call_helper` handles reverted calls.

This mirrors the pattern already correctly implemented in `contract_call_helper`:

```cairo
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
``` [4](#0-3) 

---

### Proof of Concept

1. Attacker writes `VictimClass`: a contract whose constructor reverts unconditionally (e.g., `assert 1 = 0`).
2. Attacker writes `AttackerContract`:
   ```
   fn attack(token_address, amount) {
       let (addr) = deploy(VICTIM_CLASS_HASH, salt, [], false);
       // OS always returns failure_flag=0 here, addr is non-zero
       IERC20(token_address).transfer(addr, amount);
       // Funds sent to addr, which has class_hash=0 — permanently locked
   }
   ```
3. Attacker funds `AttackerContract` with tokens and calls `attack(token_address, amount)`.
4. The OS executes `execute_deploy`: constructor of `VictimClass` reverts, revert log rolls back state, `addr` has `class_hash=0`. But `failure_flag=0` is written to the response.
5. `AttackerContract` reads success, transfers `amount` tokens to `addr`.
6. Tokens are permanently frozen at `addr` — no class, no entry point, no recovery.

The root cause is exclusively in the scoped file at the hardcoded `failure_flag=0` on line 539 of `syscall_impls.cairo`. [5](#0-4)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L413-417)
```text
    with remaining_gas {
        let (is_reverted, retdata_size, retdata, _is_deprecated) = select_execute_entry_point_func(
            block_context=block_context, execution_context=execution_context
        );
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L432-434)
```text
    with_attr error_message("Predicted gas costs are inconsistent with the actual execution.") {
        assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L527-540)
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
