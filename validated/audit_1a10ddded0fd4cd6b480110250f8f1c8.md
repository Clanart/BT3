### Title
Deploy Syscall Always Reports Success Regardless of Constructor Outcome — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The OS-level implementation of the `deploy` syscall in `execute_deploy` unconditionally writes `failure_flag=0` into the syscall response header, regardless of whether the constructor execution succeeded or failed. This is the direct analog of the "return status ignored" vulnerability class: the actual outcome of `deploy_contract(...)` is never reflected in the response seen by the calling contract.

---

### Finding Description

In `execute_deploy` (`syscall_impls.cairo`, lines 527–555), after calling `deploy_contract`, the response header is written with a hardcoded `failure_flag=0`:

```cairo
with remaining_gas {
    let (retdata_size, retdata) = deploy_contract(
        block_context=block_context, constructor_execution_context=constructor_execution_context
    );
}

let response_header = cast(syscall_ptr, ResponseHeader*);
let syscall_ptr = syscall_ptr + ResponseHeader.SIZE;

// Write the response header.
// TODO(Yoni, 1/1/2026): support failures.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
``` [1](#0-0) 

The `deploy_contract` call returns `(retdata_size, retdata)` — it does **not** return an `is_reverted` flag. The response header's `failure_flag` is never derived from the actual execution outcome; it is always `0`. The inline TODO comment explicitly acknowledges this: `"// TODO(Yoni, 1/1/2026): support failures."` [2](#0-1) 

Contrast this with `contract_call_helper`, which correctly reads `is_reverted` from `select_execute_entry_point_func` and propagates it into the response header:

```cairo
let (is_reverted, retdata_size, retdata, _is_deprecated) = select_execute_entry_point_func(...);
...
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
``` [3](#0-2) 

The `deploy` syscall response is what the calling Sierra contract reads as a `Result` type. With `failure_flag` always `0`, the calling contract always receives `Ok(contract_address)`, even when the constructor reverted.

---

### Impact Explanation

**Direct loss of funds / Permanent freezing of funds (Critical).**

A factory contract pattern — common in DeFi — calls `deploy` to create a sub-contract, then immediately interacts with it (e.g., transfers tokens, sets allowances, registers it as a vault). Because the OS always returns `failure_flag=0`, the factory contract cannot distinguish a successful deployment from a failed one. If the constructor reverted (e.g., due to an attacker-controlled argument causing an assertion failure, or out-of-gas in the constructor), the factory contract proceeds as if the contract is live and properly initialized. Any funds transferred to the returned `contract_address` are sent to a contract whose constructor state was never committed, resulting in funds locked in an uninitialized contract (permanent freeze) or sent to an address the attacker controls via a re-deployment race.

---

### Likelihood Explanation

**Medium-High.** The `deploy` syscall is a standard, publicly accessible syscall reachable by any unprivileged transaction sender. Any contract that deploys sub-contracts and relies on the syscall's `failure_flag` to gate subsequent fund transfers is vulnerable. The attacker's entry path is straightforward: submit a transaction that calls a victim factory contract with constructor arguments crafted to cause a revert. No privileged access, leaked keys, or external dependency compromise is required.

---

### Recommendation

Mirror the pattern used in `contract_call_helper`: have `deploy_contract` return an `is_reverted` flag (or a `(is_reverted, retdata_size, retdata)` tuple), and propagate it into the `ResponseHeader`:

```cairo
with remaining_gas {
    let (is_reverted, retdata_size, retdata) = deploy_contract(...);
}
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
```

If the constructor reverts, the response should carry `failure_flag=1` and the revert data, consistent with how `call_contract` and `library_call` handle failures.

---

### Proof of Concept

1. Attacker deploys a "bomb constructor" class: a contract whose `constructor` always panics (e.g., `assert 1 = 0`).
2. Attacker calls a victim factory contract (e.g., a DEX pool factory) with the bomb class hash as the implementation class.
3. The factory calls the `deploy` syscall. The OS executes `execute_deploy` → `deploy_contract` → constructor panics.
4. The OS writes `ResponseHeader(gas=remaining_gas, failure_flag=0)` — the factory receives `Ok(contract_address)`.
5. The factory, believing deployment succeeded, transfers liquidity tokens to `contract_address`.
6. The tokens are permanently frozen: the contract at that address has no initialized storage (constructor reverted), and no withdrawal function is reachable.

The root cause is at: [2](#0-1)

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
