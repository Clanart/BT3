### Title
Hardcoded `failure_flag=0` in `execute_deploy` Ignores Constructor Revert — (`File: execution/syscall_impls.cairo`)

---

### Summary

The `execute_deploy` syscall handler in the StarkNet OS unconditionally writes `failure_flag=0` in the response header, regardless of whether the deployed contract's constructor actually succeeded or reverted. This is a direct analog of the Gearbox bug: just as `totalValue` was incremented even when a token transfer silently failed, the OS here always reports "success" to the calling contract even when the constructor execution fails.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_deploy` function runs the constructor via `deploy_contract`, then writes the syscall response:

```cairo
with remaining_gas {
    let (retdata_size, retdata) = deploy_contract(
        block_context=block_context, constructor_execution_context=constructor_execution_context
    );
}

// Write the response header.
// TODO(Yoni, 1/1/2026): support failures.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
``` [1](#0-0) 

The `failure_flag` field is hardcoded to `0` (success). The developer comment `// TODO(Yoni, 1/1/2026): support failures.` confirms this is a known gap — constructor failures are not propagated back to the calling contract.

By contrast, `contract_call_helper` (the analogous handler for `call_contract`) correctly propagates the revert status:

```cairo
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
``` [2](#0-1) 

Inside `execute_entry_point`, when `is_reverted != FALSE`, `handle_revert` is called to roll back storage writes, but the contract address is already registered and the class hash is already set before the constructor runs. The constructor's storage writes are reverted, but the contract shell exists at the computed address. [3](#0-2) 

The calling contract receives `failure_flag=0` and the deployed `contract_address` in the `DeployResponse`, with no way to distinguish a successful deployment from one where the constructor silently reverted. [4](#0-3) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

A factory contract that deploys child contracts and then transfers funds to them (a common pattern: deploy → fund → use) will receive a "success" response even when the child constructor reverted. The child contract exists at the computed address but is in an uninitialized state (e.g., no owner set, no access-control initialized, no internal accounting bootstrapped). Any funds transferred to it by the factory contract — based on the false success signal — are permanently frozen, because the contract's logic requires constructor-initialized state to authorize withdrawals or transfers.

---

### Likelihood Explanation

**High.** The `deploy` syscall is a standard, unprivileged operation available to any contract. Any user can invoke a factory contract that uses `deploy` followed by a fund transfer. The constructor revert can be triggered by an attacker-controlled parameter (e.g., a salt, calldata, or a storage condition the attacker pre-arranged). No privileged access is required. The OS-level bug is deterministic: every constructor revert on every block produces the wrong `failure_flag`.

---

### Recommendation

Propagate the constructor's revert status into the `DeployResponse` header, mirroring the pattern already used in `contract_call_helper`:

```cairo
// Capture is_reverted from deploy_contract.
with remaining_gas {
    let (is_reverted, retdata_size, retdata) = deploy_contract(...);
}
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
```

If `is_reverted == 1`, the `contract_address` field in `DeployResponse` should be set to `0` (or left undefined) so callers cannot act on a failed deployment address. This matches the behavior of `call_contract` and closes the accounting gap.

---

### Proof of Concept

1. Attacker deploys a **factory contract** on StarkNet. The factory's `deploy_and_fund` entry point:
   - Calls the `deploy` syscall with a child class whose constructor always reverts (e.g., `assert 1 = 0`).
   - Reads the returned `contract_address` from `DeployResponse`.
   - Calls `transfer(contract_address, amount)` on the ERC-20 fee token, sending funds to the "deployed" address.

2. The OS executes `execute_deploy`:
   - `deploy_contract` runs the constructor → constructor reverts → `handle_revert` rolls back storage writes.
   - **Bug**: `assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0)` — success is reported.
   - The `DeployResponse` contains the computed `contract_address`.

3. The factory contract sees `failure_flag=0`, reads the `contract_address`, and transfers funds to it.

4. The child contract exists at that address (class hash is set) but has no initialized state. Its logic requires constructor-set state to authorize any outgoing transfer. **Funds are permanently frozen.**

The root cause is exclusively in the OS Cairo code at: [5](#0-4)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L428-434)
```text
    let response_header = cast(syscall_ptr, ResponseHeader*);
    let syscall_ptr = syscall_ptr + ResponseHeader.SIZE;

    // Write the response header.
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L541-554)
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
