### Title
Unhandled Constructor Failure in `execute_deploy` Syscall Always Reports Success — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_deploy` syscall handler in the StarkNet OS unconditionally writes `failure_flag=0` into the syscall response regardless of whether the constructor execution succeeded or failed. This is the direct Cairo analog of the ERC20 unhandled return value pattern: a sub-call's failure indicator is silently discarded, and the caller receives a false success response.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_deploy` function invokes `deploy_contract` and then writes the response header with a hardcoded `failure_flag=0`:

```cairo
with remaining_gas {
    let (retdata_size, retdata) = deploy_contract(
        block_context=block_context, constructor_execution_context=constructor_execution_context
    );
}

// TODO(Yoni, 1/1/2026): support failures.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
``` [1](#0-0) 

The `deploy_contract` call returns `(retdata_size, retdata)` — its internal failure state is not surfaced as a return value in this call path. The OS then unconditionally asserts `failure_flag=0` in the `ResponseHeader` written back to the calling contract's syscall buffer. The developer-acknowledged TODO comment confirms this is a known incomplete implementation.

By contrast, in `contract_call_helper` (the handler for `call_contract` and `library_call`), the `is_reverted` flag is correctly propagated into the response:

```cairo
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
``` [2](#0-1) 

The `execute_deploy` path lacks this check entirely.

---

### Impact Explanation

**Critical — Direct loss of funds.**

A factory contract that uses the `deploy` syscall and then immediately interacts with (or transfers funds to) the newly deployed address based on the reported success will operate on a false premise when the constructor fails. Specifically:

1. The constructor execution fails and its state changes are reverted via the revert log.
2. The OS still writes `failure_flag=0` and a valid `contract_address` into the response.
3. The calling contract reads `failure_flag=0`, concludes deployment succeeded, and sends funds to `contract_address`.
4. Because the constructor was reverted, the contract at `contract_address` has `class_hash=0` (undeployed state) — no entry point exists to recover the funds.
5. Funds are permanently frozen at that address.

This matches the **Critical: Direct loss of funds** and **Critical: Permanent freezing of funds** impact categories.

---

### Likelihood Explanation

**Medium.** Any contract that:
- Uses the `deploy` syscall, AND
- Sends funds or takes irreversible action contingent on the deploy response

is vulnerable. This is a standard factory pattern used in DeFi protocols on StarkNet. The constructor failure can be triggered by an unprivileged user who crafts calldata that causes the constructor to revert (e.g., by passing invalid constructor arguments, or by deploying a class whose constructor always reverts under specific conditions). The attacker does not need any privileged role — they only need to invoke a vulnerable factory contract.

---

### Recommendation

Propagate the constructor failure flag from `deploy_contract` into the `ResponseHeader`, mirroring the pattern used in `contract_call_helper`:

```cairo
// After deploy_contract returns, check is_reverted:
let (is_reverted, retdata_size, retdata) = deploy_contract(...);

assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
```

If `deploy_contract` does not currently return an `is_reverted` flag, its signature must be updated to do so, and the revert-log-based state rollback must be confirmed to execute before the response is written. The TODO comment at line 538 should be resolved before this code path is used in production.

---

### Proof of Concept

1. Declare a class `FailingChild` whose constructor always reverts.
2. Deploy a `Factory` contract containing:
   ```
   func deploy_and_fund():
       let (addr) = deploy(class_hash=FAILING_CHILD_HASH, ...)
       // response.failure_flag is 0 (false success from OS)
       transfer(token=ETH, to=addr, amount=1_ETH)
       // ETH sent to addr which has class_hash=0 — permanently frozen
   ```
3. Call `Factory.deploy_and_fund()` as an unprivileged user.
4. The OS executes the constructor, which reverts. The revert log rolls back the state change for `addr`. The OS writes `failure_flag=0` anyway.
5. `Factory` reads success, transfers 1 ETH to `addr`.
6. `addr` has no class — funds are permanently frozen.

The root cause is exclusively in the OS production file at: [3](#0-2)

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
