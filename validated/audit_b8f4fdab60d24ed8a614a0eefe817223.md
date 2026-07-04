### Title
Missing Failure Propagation in `execute_deploy` Syscall Always Reports Success, Enabling Permanent Fund Freezing - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_deploy` syscall handler in `syscall_impls.cairo` unconditionally writes `failure_flag=0` in the response header, regardless of whether the deployed contract's constructor actually succeeded or failed. This is an explicit missing implementation (acknowledged by a `TODO` comment). A caller contract that uses the `deploy` syscall cannot distinguish a successful deployment from a failed one, and may proceed to transfer funds to an uninitialized contract address, permanently freezing those funds.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_deploy` function executes the constructor of a new contract via `deploy_contract`, then writes the syscall response:

```cairo
// Write the response header.
// TODO(Yoni, 1/1/2026): support failures.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
``` [1](#0-0) 

The `failure_flag` field is hardcoded to `0` (success) unconditionally. The `deploy_contract` call that precedes it can return revert data (`retdata_size`, `retdata`), indicating the constructor failed:

```cairo
with remaining_gas {
    let (retdata_size, retdata) = deploy_contract(
        block_context=block_context, constructor_execution_context=constructor_execution_context
    );
}
``` [2](#0-1) 

Compare this with `contract_call_helper`, which correctly propagates `is_reverted` from `select_execute_entry_point_func` into the response header's `failure_flag`:

```cairo
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
``` [3](#0-2) 

The `execute_deploy` syscall is missing the equivalent failure-propagation logic. The constructor's revert status is never read and never written into the response. The caller contract always receives `failure_flag=0` and a valid `contract_address`, even when the constructor reverted.

The `execute_deploy_account_transaction` in `transaction_impls.cairo` uses a `revert_log` to handle constructor failures at the top-level transaction layer:

```cairo
let revert_log = init_revert_log();
deploy_contract{revert_log=revert_log}(
    block_context=block_context, constructor_execution_context=constructor_execution_context
);
``` [4](#0-3) 

But the `execute_deploy` syscall path has no equivalent revert-status check and no failure propagation — the implementation is simply absent, exactly analogous to the reported missing `beneficiaryWithdraw` implementation.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

A caller contract that uses the `deploy` syscall and then transfers tokens to the returned `contract_address` (a standard deploy-and-fund pattern) will send funds to a contract whose constructor silently failed. Because the constructor did not complete, the contract's internal state (e.g., owner, withdrawal logic, token accounting) is uninitialized. Any funds transferred to that address are permanently frozen: the contract cannot process withdrawals or transfers because its state was never set up. The OS proof will be valid (the OS accepted the transaction), so there is no on-chain mechanism to recover the funds.

---

### Likelihood Explanation

The `deploy` syscall is a standard, publicly available syscall callable by any contract from any unprivileged invoke transaction. The deploy-and-fund pattern (deploy a contract, then immediately send it an initial balance) is ubiquitous in DeFi and token protocols on StarkNet. A constructor can fail for many reasons reachable by an attacker: crafted calldata, a storage slot already occupied, an assertion on an initial parameter, or an out-of-gas condition. Because the OS always reports success, neither the caller contract nor the user has any signal that the constructor failed, making the fund loss silent and unrecoverable.

---

### Recommendation

Propagate the constructor's revert status into the `failure_flag` field of the `execute_deploy` response header, mirroring the pattern used in `contract_call_helper`. Specifically:

1. Modify `deploy_contract` to return an `is_reverted` flag (or check the retdata for a failure sentinel).
2. Write `failure_flag=is_reverted` instead of the hardcoded `failure_flag=0`.
3. When `is_reverted=1`, append `ERROR_ENTRY_POINT_FAILED` to the retdata (consistent with `contract_call_helper`) and ensure the contract's state changes are rolled back via the revert log.

---

### Proof of Concept

1. Deploy a contract `Caller` on StarkNet that implements the following logic in its `__execute__` entry point:
   - Call the `deploy` syscall with a class hash whose constructor always reverts (e.g., `assert 1 = 0`).
   - Read the response: `failure_flag` will be `0` and `contract_address` will be a valid address.
   - Call the ERC-20 `transfer` syscall to send 1000 STRK to the returned `contract_address`.

2. Submit an invoke transaction calling `Caller.__execute__`.

3. The OS executes the block:
   - `execute_deploy` runs the constructor, which reverts.
   - `execute_deploy` writes `ResponseHeader(gas=remaining_gas, failure_flag=0)` — the hardcoded path at line 539 of `syscall_impls.cairo`.
   - `Caller` reads `failure_flag=0`, concludes the deploy succeeded, and transfers 1000 STRK to the address.

4. The block is proven and committed. The 1000 STRK now reside at a contract address whose constructor never completed. The contract has no initialized state, no owner, and no withdrawal function. The funds are permanently frozen. [5](#0-4)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L432-434)
```text
    with_attr error_message("Predicted gas costs are inconsistent with the actual execution.") {
        assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L527-555)
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

    return ();
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L642-645)
```text
        let revert_log = init_revert_log();
        deploy_contract{revert_log=revert_log}(
            block_context=block_context, constructor_execution_context=constructor_execution_context
        );
```
