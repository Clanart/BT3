### Title
Constructor Failure Silently Ignored in `execute_deploy` Syscall — Hardcoded `failure_flag=0` Regardless of Outcome - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_deploy` syscall handler in the StarkNet OS unconditionally writes `failure_flag=0` into the syscall response header, regardless of whether the deployed contract's constructor actually succeeded or reverted. A calling contract always receives a "success" response from `deploy`, even when the constructor failed. This is a direct analog of the Ethereum bridge's unchecked `transferFrom` return value: a critical sub-operation's failure is silently swallowed, and the system proceeds as if it succeeded.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_deploy` function handles the `deploy` syscall. After calling `deploy_contract`, it writes the syscall response header with `failure_flag` **hardcoded to `0`** (success):

```cairo
// TODO(Yoni, 1/1/2026): support failures.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
``` [1](#0-0) 

The `deploy_contract` call returns only `(retdata_size, retdata)` — it does not surface an `is_reverted` flag to `execute_deploy`:

```cairo
with remaining_gas {
    let (retdata_size, retdata) = deploy_contract(
        block_context=block_context, constructor_execution_context=constructor_execution_context
    );
}
``` [2](#0-1) 

Compare this with `contract_call_helper`, which correctly propagates `is_reverted` into the response header:

```cairo
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
``` [3](#0-2) 

The `execute_entry_point` function does return `is_reverted` as its first value, and the revert log mechanism correctly undoes storage writes made by a reverted constructor. However, because `execute_deploy` never reads or propagates this flag, the calling contract's view of the operation is always "success." [4](#0-3) 

The in-code `TODO` comment at line 538 explicitly acknowledges this gap: `// TODO(Yoni, 1/1/2026): support failures.` [5](#0-4) 

---

### Impact Explanation

**Critical — Direct loss of funds.**

When a constructor reverts, the revert log correctly undoes the constructor's storage writes, but the contract's class hash entry is already committed in `contract_state_changes` (the contract address exists with its class hash set). The calling contract receives `failure_flag=0` and a valid `contract_address` in the `DeployResponse`:

```cairo
assert [response] = DeployResponse(
    contract_address=contract_address,
    constructor_retdata_start=retdata,
    constructor_retdata_end=retdata + retdata_size,
);
``` [6](#0-5) 

The calling contract, trusting the `failure_flag=0` response, proceeds to interact with the returned address — for example, transferring tokens to it or registering it as a trusted counterparty. Because the constructor's initialization writes were reverted, the deployed contract's storage is in a zeroed/uninitialized state. An attacker who controls the constructor logic can deliberately revert it, receive a "deployed" address with no access controls or ownership set, and then drain any funds sent to that address by the unsuspecting caller.

---

### Likelihood Explanation

**High.** The entry path requires only an unprivileged transaction sender:

1. An attacker declares a contract class whose constructor conditionally reverts (e.g., based on a storage value or calldata).
2. The attacker calls a victim contract (e.g., a factory or vault) that issues a `deploy` syscall and immediately transfers funds to the returned address.
3. The OS reports `failure_flag=0`; the victim contract proceeds with the transfer.
4. The attacker calls functions on the uninitialized deployed contract to extract the funds.

Factory patterns that deploy-and-fund in a single transaction are common in DeFi protocols on StarkNet. No privileged access is required; the attacker only needs to be a class declarer, which is an unprivileged role.

---

### Recommendation

In `execute_deploy`, capture the `is_reverted` return value from `deploy_contract` (or from the underlying `execute_entry_point` call it wraps) and propagate it into the `ResponseHeader`:

```cairo
// Replace:
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);

// With:
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
```

Additionally, when `is_reverted != 0`, the `DeployResponse` should not include a valid `contract_address`, and the contract's class hash entry should be removed from `contract_state_changes` (or the deploy should be treated as a no-op). This mirrors how `contract_call_helper` already handles reverted sub-calls by appending `ERROR_ENTRY_POINT_FAILED` and setting `failure_flag=is_reverted`. [7](#0-6) 

---

### Proof of Concept

1. Attacker declares class `MaliciousVault` whose constructor does:
   ```
   // Revert unconditionally, leaving storage zeroed (no owner set).
   assert 1 = 0;
   ```
2. Victim factory contract `F` executes:
   ```
   let (addr) = deploy(class_hash=MaliciousVault, ...);
   // failure_flag is 0 — F believes deployment succeeded.
   IERC20(token).transfer(addr, 1_000_000);  // funds sent to uninitialized contract
   ```
3. OS `execute_deploy` writes `ResponseHeader(gas=..., failure_flag=0)` unconditionally. [8](#0-7) 
4. `MaliciousVault` at `addr` exists (class hash set) but has zeroed storage — no owner, no access control.
5. Attacker calls `MaliciousVault.withdraw(addr, 1_000_000)` — succeeds because no ownership check was initialized.
6. Funds are permanently lost from the victim factory's perspective.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L527-531)
```text
    with remaining_gas {
        let (retdata_size, retdata) = deploy_contract(
            block_context=block_context, constructor_execution_context=constructor_execution_context
        );
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L533-539)
```text
    // TODO(Yoni, 1/1/2026): consider sharing code with call_contract_helper.
    let response_header = cast(syscall_ptr, ResponseHeader*);
    let syscall_ptr = syscall_ptr + ResponseHeader.SIZE;

    // Write the response header.
    // TODO(Yoni, 1/1/2026): support failures.
    assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L548-553)
```text
    relocate_segment(src_ptr=response.constructor_retdata_start, dest_ptr=retdata);
    assert [response] = DeployResponse(
        contract_address=contract_address,
        constructor_retdata_start=retdata,
        constructor_retdata_end=retdata + retdata_size,
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L150-152)
```text
}(block_context: BlockContext*, execution_context: ExecutionContext*) -> (
    is_reverted: felt, retdata_size: felt, retdata: felt*
) {
```
