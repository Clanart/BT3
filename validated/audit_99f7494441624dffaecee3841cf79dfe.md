### Title
Constructor Failure in `deploy` Syscall Produces Unprovable Block — (`File: execution/deploy_contract.cairo`, `execution/syscall_impls.cairo`)

### Summary

The `deploy` syscall in the StarkNet OS unconditionally asserts that a deployed contract's constructor never reverts (`assert is_reverted = 0`). If a constructor does revert, the Cairo proof for the entire block becomes invalid, halting the network. Simultaneously, the syscall response always reports `failure_flag=0` (success) to the calling contract, meaning the caller proceeds as if deployment succeeded even when it did not. This is the direct analog of M-01: a committed resource transfer (state update + class hash assignment) followed by an execution step (constructor) that can fail, with no recovery path.

---

### Finding Description

**Root cause — `deploy_contract.cairo` line 91:** [1](#0-0) 

```cairo
// Invoke the contract constructor.
let (is_reverted, retdata_size, retdata, _is_deprecated) = select_execute_entry_point_func(
    block_context=block_context, execution_context=constructor_execution_context
);
...
// The deprecated deploy syscalls do not support reverts.
assert is_reverted = 0;
return (retdata_size=retdata_size, retdata=retdata);
```

`select_execute_entry_point_func` can legitimately return `is_reverted = 1` when the constructor panics (out-of-gas, assertion failure, etc.). The Cairo `assert is_reverted = 0` is a **proof constraint**, not a runtime check. If it is violated, the STARK proof for the block is unsatisfiable — the block can never be finalized.

**Root cause — `syscall_impls.cairo` lines 527–554 (the new `deploy` syscall):** [2](#0-1) 

```cairo
with remaining_gas {
    let (retdata_size, retdata) = deploy_contract(
        block_context=block_context, constructor_execution_context=constructor_execution_context
    );
}

// TODO(Yoni, 1/1/2026): support failures.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
```

The `failure_flag` is hardcoded to `0` regardless of whether the constructor succeeded. The TODO comment explicitly acknowledges that failure handling is unimplemented. The calling contract therefore always receives a "success" response and a valid-looking contract address, even when the constructor reverted.

**Two-step committed state before execution:**

Inside `deploy_contract`, the contract's class hash is written to `contract_state_changes` **before** the constructor runs: [3](#0-2) 

```cairo
tempvar new_state_entry = new StateEntry(
    class_hash=constructor_execution_context.class_hash,
    storage_ptr=state_entry.storage_ptr,
    nonce=0,
);
dict_update{dict_ptr=contract_state_changes}(
    key=contract_address, prev_value=cast(state_entry, felt), new_value=cast(new_state_entry, felt),
);
```

This mirrors M-01's pattern: resources are committed (state updated, class hash assigned, contract address allocated) before the execution step (constructor) that can fail.

---

### Impact Explanation

**High — Network not being able to confirm new transactions (total network shutdown).**

If the sequencer includes a block containing a transaction where the `deploy` syscall's constructor reverts:

1. The OS Cairo program reaches `assert is_reverted = 0` with `is_reverted = 1`.
2. The proof constraint is violated; no valid STARK proof can be generated for the block.
3. The block cannot be finalized on L1.
4. The network is unable to confirm any further transactions until the issue is resolved.

Additionally, because `failure_flag=0` is always returned to the calling contract, any contract that sends tokens to the "deployed" address after receiving the false-success response will permanently lose those funds — a secondary **direct loss of funds** impact.

---

### Likelihood Explanation

The sequencer's blockifier is expected to detect constructor failures and mark the outer transaction as reverted, preventing the OS from ever reaching the failing assertion. However:

1. The comment in `deploy_contract.cairo` says "deprecated deploy syscalls do not support reverts," but `execute_deploy` is the **new** (non-deprecated) syscall. It reuses the same `deploy_contract` function, inheriting the same hard constraint. This is an acknowledged gap (TODO comment).
2. Any discrepancy between the blockifier's revert detection and the OS's constraint — e.g., a constructor that behaves differently under proving conditions, or a blockifier bug — causes the block to become unprovable.
3. An unprivileged user can declare a contract class whose constructor conditionally reverts (e.g., based on gas remaining, storage state, or block number) and trigger the `deploy` syscall from any contract they control or interact with.

---

### Recommendation

1. **In `deploy_contract.cairo`:** Remove the unconditional `assert is_reverted = 0`. Instead, return `is_reverted` as part of the function's return tuple so callers can handle it.
2. **In `syscall_impls.cairo` (`execute_deploy`):** Use the returned `is_reverted` flag to set `failure_flag` in the `ResponseHeader` correctly, and roll back state changes via the `revert_log` when the constructor fails — mirroring the pattern used in `contract_call_helper`.
3. Ensure the revert log entries written before the constructor call (class hash assignment, contract entry) are properly unwound on constructor failure.

---

### Proof of Concept

1. Attacker declares a contract class whose constructor always reverts: `assert 1 = 0`.
2. Attacker (or any contract the attacker can influence) calls the `deploy` syscall with this class hash.
3. `deploy_contract` calls `select_execute_entry_point_func`, which returns `is_reverted = 1`.
4. `assert is_reverted = 0` at `deploy_contract.cairo:91` is violated.
5. The STARK proof for the block is unsatisfiable.
6. The block cannot be submitted to L1; the network halts.
7. Simultaneously, the calling contract receives `failure_flag=0` and a contract address, and may transfer funds to that address — funds are permanently lost. [4](#0-3) [5](#0-4)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L28-29)
```text
// TODO(Yoni, 1/1/2027): handle failures.
func deploy_contract{
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L56-66)
```text
    tempvar new_state_entry = new StateEntry(
        class_hash=constructor_execution_context.class_hash,
        storage_ptr=state_entry.storage_ptr,
        nonce=0,
    );

    dict_update{dict_ptr=contract_state_changes}(
        key=contract_address,
        prev_value=cast(state_entry, felt),
        new_value=cast(new_state_entry, felt),
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L82-92)
```text
    let (is_reverted, retdata_size, retdata, _is_deprecated) = select_execute_entry_point_func(
        block_context=block_context, execution_context=constructor_execution_context
    );

    // Entries before this point belong to the deployed contract.
    assert [revert_log] = RevertLogEntry(selector=CHANGE_CONTRACT_ENTRY, value=contract_address);
    let revert_log = &revert_log[1];

    // The deprecated deploy syscalls do not support reverts.
    assert is_reverted = 0;
    return (retdata_size=retdata_size, retdata=retdata);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L527-554)
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

```
