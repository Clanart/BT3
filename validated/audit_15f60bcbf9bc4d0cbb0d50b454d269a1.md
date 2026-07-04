### Title
`execute_deploy` Syscall Hardcodes `failure_flag=0`, Masking Constructor Failures and Enabling Permanent Fund Loss — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_deploy` syscall handler in the StarkNet OS always writes `failure_flag=0` in the response header regardless of whether the constructor execution succeeded or failed. Caller contracts therefore cannot detect constructor failures and may proceed with incorrect assumptions — most critically, sending funds to a contract address where no contract was actually deployed, causing permanent loss of those funds.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_deploy` function (lines 452–556) handles the `deploy` syscall. After invoking `deploy_contract`, it unconditionally writes a success response:

```cairo
// Write the response header.
// TODO(Yoni, 1/1/2026): support failures.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
``` [1](#0-0) 

The embedded TODO comment explicitly acknowledges that failure support is not implemented. The function signature of `execute_deploy` carries `revert_log` as an implicit argument:

```cairo
func execute_deploy{
    ...
    revert_log: RevertLogEntry*,
    ...
}(block_context: BlockContext*, caller_execution_context: ExecutionContext*) {
``` [2](#0-1) 

This means `deploy_contract` is called with the `revert_log` implicit argument passed through automatically. When the constructor reverts, `deploy_contract` uses `revert_log` to undo all state changes — the contract is not deployed (its `class_hash` remains `0` at the computed address). However, the OS still writes `failure_flag=0` and returns the computed `contract_address` to the caller:

```cairo
assert [response] = DeployResponse(
    contract_address=contract_address,
    constructor_retdata_start=retdata,
    constructor_retdata_end=retdata + retdata_size,
);
``` [3](#0-2) 

The caller contract receives a success response with a valid-looking `contract_address`, but no contract exists at that address. If the caller subsequently transfers tokens to that address (a common post-deploy pattern), those tokens are permanently locked because there is no contract code to handle them.

This is structurally identical to the external report: a function intended to move assets sends them to a destination that cannot handle them, with no recovery path.

---

### Impact Explanation

**Critical — Direct loss of funds / Permanent freezing of funds.**

Any on-chain protocol that:
1. Uses the `deploy` syscall to deploy sub-contracts, and
2. Transfers tokens or ETH to the deployed address after receiving a success response

…is vulnerable. When the constructor reverts (for any reason), the state is rolled back but the caller is told deployment succeeded. Tokens sent to the uninitialized address are permanently locked: `class_hash=0` means no entry point exists to transfer them out.

---

### Likelihood Explanation

Likelihood is **moderate to high**:

- Many DeFi protocols on StarkNet use factory patterns that deploy sub-contracts and immediately fund them.
- Constructors revert for legitimate reasons: invalid parameters, access-control checks, insufficient initial state.
- An attacker who can influence which class is deployed (e.g., via a user-specified `class_hash` in a factory) can deliberately trigger a constructor revert while the factory sends funds to the computed address.
- The false success response is byte-for-byte identical to a genuine success response; no caller-side mitigation is possible without OS-level fix.

---

### Recommendation

Fix `execute_deploy` to propagate constructor failure to the caller:

1. Have `deploy_contract` return an `is_reverted` flag (analogous to `contract_call_helper`).
2. Conditionally set `failure_flag` in the response header based on that flag.
3. When `is_reverted=1`, include the revert reason in the response retdata and do **not** return the computed `contract_address` as a valid deployment target.

The pattern already exists in `contract_call_helper` (lines 404–449) and should be reused here. [4](#0-3) 

---

### Proof of Concept

1. Attacker declares a contract class whose constructor reverts when `calldata[0] == 0xdead`.
2. A legitimate factory contract exposes a public `deploy_and_fund(class_hash, salt, amount)` function that:
   a. Calls `deploy(class_hash, salt, [0xdead])` via syscall.
   b. On success response, calls `token.transfer(deployed_address, amount)`.
3. Attacker calls `factory.deploy_and_fund(attacker_class, salt, victim_amount)`.
4. Constructor reverts → state rolled back → no contract at `deployed_address`.
5. OS writes `failure_flag=0` → factory receives success → transfers `victim_amount` tokens to `deployed_address`.
6. `deployed_address` has `class_hash=0` → no `transfer` entry point → tokens permanently frozen.

The attacker-controlled entry path is entirely unprivileged: declare a class (permissionless on StarkNet), then invoke the factory's public function.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L404-449)
```text
func contract_call_helper{
    range_check_ptr,
    syscall_ptr: felt*,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    revert_log: RevertLogEntry*,
    outputs: OsCarriedOutputs*,
}(remaining_gas: felt, block_context: BlockContext*, execution_context: ExecutionContext*) {
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

    let response = cast(syscall_ptr, CallContractResponse*);
    // Advance syscall pointer to the next syscall.
    let syscall_ptr = syscall_ptr + CallContractResponse.SIZE;

    %{ CheckNewCallContractResponse %}

    // Write the response.
    relocate_segment(src_ptr=response.retdata_start, dest_ptr=retdata);
    assert [response] = CallContractResponse(
        retdata_start=retdata, retdata_end=retdata + retdata_size
    );

    return ();
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L452-461)
```text
func execute_deploy{
    range_check_ptr,
    syscall_ptr: felt*,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    revert_log: RevertLogEntry*,
    outputs: OsCarriedOutputs*,
}(block_context: BlockContext*, caller_execution_context: ExecutionContext*) {
    alloc_locals;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L534-539)
```text
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
