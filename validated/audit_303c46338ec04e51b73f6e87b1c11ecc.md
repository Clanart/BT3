### Title
Hardcoded `failure_flag=0` in `execute_deploy` Syscall Response Masks Constructor Failures — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_deploy` function in `syscall_impls.cairo` unconditionally writes `failure_flag=0` (success) into the deploy syscall response header, regardless of whether the constructor execution actually succeeded or reverted. This is a direct analog to the external report's "hardcoded flag" vulnerability class: a boolean flag that should be dynamic is hardcoded to a fixed value, causing the caller to receive an incorrect status. Calling contracts that rely on `failure_flag` to gate subsequent fund transfers will be misled into sending funds to a non-existent contract address, resulting in permanent loss of those funds.

---

### Finding Description

In `execute_deploy` (`syscall_impls.cairo`, line 539), after calling `deploy_contract`, the response header is written with an unconditionally hardcoded `failure_flag=0`:

```cairo
// Write the response header.
// TODO(Yoni, 1/1/2026): support failures.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
``` [1](#0-0) 

The embedded TODO comment explicitly acknowledges this is an unresolved limitation. The `deploy_contract` call does return `(retdata_size, retdata)`, and the function has `revert_log` as an implicit argument (inherited from `execute_deploy`'s signature), meaning constructor reverts **do** cause state to be rolled back via the revert log. However, the `failure_flag` written to the response is never derived from the constructor's actual outcome — it is always `0`. [2](#0-1) 

The `ResponseHeader.failure_flag` field is the mechanism by which the Sierra runtime propagates syscall failures to the calling contract. When `failure_flag=1`, the runtime reverts the caller. When `failure_flag=0`, the runtime treats the syscall as successful and continues execution. By hardcoding `failure_flag=0`, the OS breaks this contract: a constructor revert is silently swallowed, and the calling contract proceeds as if deployment succeeded.

Compare this to the correct handling in `contract_call_helper`, where `failure_flag` is properly set from the actual execution result:

```cairo
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
``` [3](#0-2) 

The `execute_deploy` function's signature confirms `revert_log` is in scope, so the constructor revert path is reachable and state is correctly rolled back — but the response flag is never updated to reflect this: [4](#0-3) 

---

### Impact Explanation

**Critical — Direct loss of funds.**

When a constructor reverts:
1. `deploy_contract` rolls back the contract's state via the revert log — the contract address does **not** exist in the state trie.
2. The OS writes `failure_flag=0` to the response, so the calling contract's Sierra runtime does **not** propagate the failure.
3. The calling contract continues execution, believing the deployment succeeded.
4. If the calling contract subsequently transfers ERC-20 tokens (or any asset) to the "deployed" address, the ERC-20 transfer succeeds (it only updates the token contract's own storage), but the funds are permanently unrecoverable — there is no contract at that address capable of withdrawing them.

This matches the "Direct loss of funds" impact category.

---

### Likelihood Explanation

Factory contracts that accept user-supplied class hashes and deploy-then-fund in a single transaction are a standard DeFi pattern. Any such contract is vulnerable. The attacker's path requires only:
- Declaring a class with a reverting constructor (permissionless on StarkNet).
- Calling a victim factory that deploys and funds in sequence.

No privileged access, leaked keys, or operator collusion is required. The entry point is a standard unprivileged `invoke` transaction.

---

### Recommendation

Replace the hardcoded `failure_flag=0` with the actual constructor outcome. The `deploy_contract` function should return an `is_reverted` flag (as `execute_entry_point` does), and the response header should be written as:

```cairo
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
```

Additionally, when `is_reverted=1`, the response body should carry the constructor's revert data so the calling contract can inspect the failure reason, consistent with how `contract_call_helper` handles reverted calls.

---

### Proof of Concept

1. **Attacker** declares `MaliciousClass` — a Sierra class whose constructor always reverts (e.g., `assert 1 = 0`).
2. **Victim factory** contract exposes a public function `deploy_and_fund(class_hash, salt, amount)` that:
   - Calls the `deploy` syscall with the provided `class_hash`.
   - On receiving `failure_flag=0` (always, due to the bug), transfers `amount` tokens to the returned `contract_address`.
3. **Attacker** calls `victim_factory.deploy_and_fund(MaliciousClass_hash, salt, 1000_ETH)`.
4. **OS execution**:
   - Constructor of `MaliciousClass` reverts.
   - `deploy_contract` rolls back state via revert log — no contract exists at `contract_address`.
   - OS writes `ResponseHeader(gas=..., failure_flag=0)` — hardcoded success.
5. **Victim factory** receives `failure_flag=0`, proceeds to transfer `1000_ETH` to `contract_address`.
6. ERC-20 transfer succeeds (updates token storage), but `contract_address` has no code — funds are permanently frozen.

The root cause is exclusively in the scoped file at line 539 of `syscall_impls.cairo`, reachable by any unprivileged transaction sender via a standard `invoke` transaction targeting any factory-pattern contract.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L432-434)
```text
    with_attr error_message("Predicted gas costs are inconsistent with the actual execution.") {
        assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L452-460)
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
