### Title
Deploy Syscall Unconditionally Reports Constructor Success Regardless of Revert - (File: `execution/syscall_impls.cairo`)

### Summary
The `execute_deploy` function in `syscall_impls.cairo` hardcodes `failure_flag=0` in the syscall response header, unconditionally reporting success to the calling contract even when the deployed contract's constructor reverts. Any Cairo 1 contract relying on the deploy result to gate fund transfers or critical state transitions will proceed on a false success signal, enabling permanent freezing or direct loss of funds.

### Finding Description

The vulnerability class from the external report is **unhandled error condition in a state-update path**: an operation that can fail is executed, the failure is not surfaced to the caller, and the caller proceeds with incorrect assumptions. The exact same pattern exists here.

In `execute_deploy` (`syscall_impls.cairo`, lines 527–539), `deploy_contract` is called and returns `(retdata_size, retdata)`. Immediately after, the response header is written with a hardcoded `failure_flag=0`, ignoring whether the constructor actually succeeded:

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

The `TODO` comment explicitly acknowledges that constructor failures are not handled. The `retdata` returned by `deploy_contract` (which carries revert data when the constructor fails) is written into the response body, but the `failure_flag` field — the only field the calling contract can inspect to detect failure — is always `0`.

Compare this with `contract_call_helper` in the same file, which correctly propagates `is_reverted` into `failure_flag`:

```cairo
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
``` [2](#0-1) 

The `execute_deploy` path has no equivalent check. The `deploy_contract` call uses the caller's implicit `revert_log`, so constructor-induced storage writes are reverted internally, but the contract address may still be registered with its class hash set while the calling contract is told the deploy succeeded.

### Impact Explanation

Any Cairo 1 contract that:
1. Calls the `deploy` syscall to create a sub-contract (e.g., a vault, escrow, or token contract),
2. Reads `failure_flag` from the response (which is always `0`), and
3. Transfers funds or commits irreversible state to the "deployed" address,

will do so even when the constructor reverted and the sub-contract is uninitialized or in an inconsistent state. Funds sent to an uninitialized contract address are permanently frozen if no recovery path exists in the calling contract's logic. This satisfies **Critical — Permanent freezing of funds** and potentially **Critical — Direct loss of funds**.

### Likelihood Explanation

The attack path requires no privileged access:
- Any user can declare and deploy a Cairo 1 contract that calls the `deploy` syscall.
- Any user can declare a "target" class whose constructor unconditionally reverts.
- The calling contract receives `failure_flag=0` and proceeds normally.

The `TODO` comment confirms the developers are aware this code path is incomplete, making it a known gap rather than an edge case. The pattern (deploy → transfer funds to deployed address) is standard in factory contracts, vaults, and escrow systems, making real-world exploitation realistic.

### Recommendation

Mirror the pattern used in `contract_call_helper`: propagate the actual failure status from `deploy_contract` into the response header's `failure_flag`. Specifically:

1. Have `deploy_contract` return an `is_reverted` flag alongside `(retdata_size, retdata)`.
2. Write `ResponseHeader(gas=remaining_gas, failure_flag=is_reverted)` instead of the hardcoded `failure_flag=0`.
3. When `is_reverted=1`, append `ERROR_ENTRY_POINT_FAILED` to the retdata (consistent with `contract_call_helper`).

### Proof of Concept

1. Attacker declares `RevertingClass` — a class whose constructor always reverts.
2. Attacker deploys `FactoryContract` (Cairo 1) with logic:
   - Call `deploy(class_hash=RevertingClass, ...)` → receives `failure_flag=0`.
   - Interpret success, transfer `N` tokens to the returned `contract_address`.
3. Because `failure_flag` is always `0`, `FactoryContract` proceeds unconditionally.
4. The constructor reverted; the deployed contract is uninitialized (or not registered at all, depending on where in `deploy_contract` the revert occurs).
5. The `N` tokens are sent to an address with no valid `__execute__` entry point, permanently freezing them.

The root cause is at: [3](#0-2)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L432-434)
```text
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
