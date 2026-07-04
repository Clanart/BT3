After reading the key execution files in the scoped directory, I found a concrete analog to the described vulnerability class.

### Title
Constructor Failure Silently Reported as Success in `execute_deploy` Syscall — (File: `execution/syscall_impls.cairo`)

### Summary
The `execute_deploy` syscall in the StarkNet OS always writes `failure_flag=0` (success) in its response header, regardless of whether the deployed contract's constructor actually succeeded or reverted. This is the direct analog of the original bug: a two-step operation (register contract + run constructor) where partial failure (constructor reverts) is misreported as full success to the calling contract, breaking the accounting invariant that the caller can trust the return value.

### Finding Description

In `execute_deploy` (`syscall_impls.cairo`, lines 527–554), after calling `deploy_contract` to run the constructor, the response header is written with a hardcoded `failure_flag=0`:

```cairo
with remaining_gas {
    let (retdata_size, retdata) = deploy_contract(
        block_context=block_context, constructor_execution_context=constructor_execution_context
    );
}

// TODO(Yoni, 1/1/2026): support failures.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
``` [1](#0-0) 

The TODO comment explicitly acknowledges that failure handling is not implemented. By contrast, the analogous `contract_call_helper` function (used by `call_contract` and `library_call`) correctly propagates the `is_reverted` flag from `select_execute_entry_point_func` into the response:

```cairo
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
``` [2](#0-1) 

The `execute_deploy` function signature carries `revert_log` as an implicit argument, meaning `deploy_contract` appends constructor revert-log entries to the outer execution's shared log: [3](#0-2) 

When the constructor reverts, those revert-log entries are never processed inside `execute_deploy` (no `handle_revert` call), so the constructor's state mutations remain in `contract_state_changes`. The calling contract receives `failure_flag=0` and the contract address, believing deployment succeeded.

This is structurally identical to the original bug: `LendingPoolGauge.notifyRewardAmount` partially transfers funds and returns `false`, but the caller (`distribute`) treats the entire amount as unsent and restores `claimable`, making the system insolvent. Here, `deploy_contract` partially mutates state (constructor runs and may write storage or change class hash) and the constructor reverts, but `execute_deploy` reports success, making the caller's view of the world inconsistent with actual on-chain state.

### Impact Explanation

A calling contract that:
1. Calls `deploy` syscall with a constructor that reverts under some condition,
2. Receives `failure_flag=0` and a contract address,
3. Transfers funds to that address,

will permanently freeze those funds. The deployed contract's state is inconsistent (constructor revert-log entries were never applied), so the contract may be unable to process or forward the received funds. Because the OS proof commits to this state, the freeze is permanent and irreversible.

This matches the **Critical — Permanent freezing of funds** impact category.

### Likelihood Explanation

Any unprivileged user can craft a transaction that invokes a contract which calls the `deploy` syscall with a constructor that conditionally reverts (e.g., based on a storage value or argument). This is a standard, reachable code path. The `execute_deploy` function is exercised by every `deploy` syscall in every Sierra contract. The constructor revert path is reachable by design (constructors are user-supplied code).

### Recommendation

Replace the hardcoded `failure_flag=0` with the actual `is_reverted` return value from `deploy_contract`, mirroring the pattern used in `contract_call_helper`. When the constructor reverts, `execute_deploy` must:
1. Call `handle_revert` to undo the constructor's state mutations from the shared revert log.
2. Write `failure_flag=1` in the response header.
3. Return the constructor's failure retdata to the caller.

### Proof of Concept

1. Deploy a contract `Victim` whose constructor calls `storage_write` and then reverts (e.g., `assert 0 = 1`).
2. Deploy a contract `Attacker` that:
   a. Calls `deploy(Victim, ...)` syscall.
   b. Reads `response.failure_flag` — it is `0` (success).
   c. Transfers `N` tokens to `response.contract_address`.
3. Observe: `Victim`'s constructor storage writes are present in state (revert log not processed), but the constructor's invariants are violated. The `N` tokens are now held by a contract in an inconsistent state with no guaranteed ability to transfer them out. The funds are permanently frozen.

The root cause is exclusively in: [4](#0-3)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L432-434)
```text
    with_attr error_message("Predicted gas costs are inconsistent with the actual execution.") {
        assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
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
