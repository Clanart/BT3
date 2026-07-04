### Title
Constructor Failure Silently Suppressed in `deploy` Syscall Allows Funds to Be Sent to Broken Contract — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_deploy` syscall handler unconditionally writes `failure_flag=0` in the response header regardless of whether the deployed contract's constructor succeeded or failed. A calling contract that uses the `deploy` syscall cannot detect a constructor revert and may proceed to transfer funds to a contract that was never properly initialized, permanently freezing those funds.

---

### Finding Description

In `execute_deploy` (`syscall_impls.cairo` lines 534–539), after `deploy_contract` is called, the response header is written with a hardcoded success flag:

```cairo
// TODO(Yoni, 1/1/2026): support failures.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
``` [1](#0-0) 

The TODO comment explicitly acknowledges this is an unimplemented feature. The `deploy_contract` call returns `(retdata_size, retdata)` — which may carry constructor error data — but the `failure_flag` field in the response is never set from that return value. [2](#0-1) 

The revert mechanism in `execute_entry_point` creates a **fresh** revert log for the constructor entry point when it fails and calls `handle_revert` to undo the constructor's own storage writes: [3](#0-2) 

However, the class-hash assignment that `deploy_contract` performs before invoking the constructor is appended to the **caller's** `revert_log` (passed implicitly into `execute_deploy`), not to the constructor's fresh revert log. Therefore, when the constructor reverts:

- The constructor's storage writes are undone (by the constructor's own revert log).
- The class-hash assignment for the new address is **not** undone (it lives in the caller's revert log and is only undone if the caller itself reverts).
- The response to the calling contract always says `failure_flag=0`.

The calling contract has no way to distinguish a successful deployment from a failed one. It will proceed as if the contract is fully initialized.

The `execute_storage_write` path shows how the revert log is populated per-contract: [4](#0-3) 

And `handle_revert` processes only the entries in the revert log it is given: [5](#0-4) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Consider a factory contract (a common DeFi primitive) that:
1. Calls the `deploy` syscall with a caller-supplied `class_hash`.
2. Reads `failure_flag` from the response — always 0 — and concludes the deployment succeeded.
3. Immediately transfers tokens to the newly deployed address (e.g., initial liquidity, collateral).

If the constructor of the deployed class reverts:
- The deployed address has its `class_hash` set in the state (the assignment is in the caller's revert log, not the constructor's).
- The constructor's storage initialization is rolled back.
- The contract is in a broken, partially initialized state.
- Tokens sent to it are permanently locked: the contract's logic depends on constructor-initialized storage that was never written, so any withdrawal path is unreachable.

There is no protocol-level mechanism to recover funds sent to such an address. The state commitment (`compute_contract_state_commitment`) will faithfully commit the broken state to the Merkle tree. [6](#0-5) 

---

### Likelihood Explanation

**Medium.**

The preconditions are:
1. A factory or deployer contract that (a) accepts a caller-supplied `class_hash` and (b) sends funds to the deployed address in the same transaction — a standard DeFi pattern.
2. An attacker who can submit a `declare` transaction (an unprivileged operation available to any account) to register a class whose constructor always reverts (e.g., `assert 1 = 0`).

Both conditions are trivially satisfiable on any live StarkNet network. The attacker does not need any privileged role; they only need to be a class declarer and a transaction sender.

---

### Recommendation

Remove the hardcoded `failure_flag=0`. The `deploy_contract` function should return an `is_reverted` flag (mirroring the return signature of `execute_entry_point`), and `execute_deploy` should write `failure_flag=is_reverted` in the response header. Additionally, when the constructor reverts, the class-hash assignment should be rolled back before returning, so the deployed address is left in a clean uninitialized state rather than a partially initialized one.

---

### Proof of Concept

1. **Attacker** submits a `declare` transaction for a Sierra class whose constructor body is `assert 1 = 0` (always reverts).
2. **Attacker** calls a victim factory contract (e.g., a pool factory) passing the malicious `class_hash` as the class to deploy.
3. The factory executes the `deploy` syscall:
   - `deploy_contract` is called; the constructor reverts.
   - `execute_deploy` writes `ResponseHeader(gas=..., failure_flag=0)`.
4. The factory reads `failure_flag=0`, concludes success, and transfers liquidity tokens to the deployed address.
5. The deployed address has `class_hash` set but all constructor storage is zeroed (reverted). No withdrawal function is reachable.
6. Tokens are permanently frozen. The state root committed to L1 reflects the broken state.

### Citations

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L638-686)
```text
func execute_storage_write{
    range_check_ptr,
    syscall_ptr: felt*,
    contract_state_changes: DictAccess*,
    revert_log: RevertLogEntry*,
}(contract_address: felt) {
    alloc_locals;
    let request = cast(syscall_ptr + RequestHeader.SIZE, StorageWriteRequest*);

    // Reduce gas.
    let success = reduce_syscall_gas_and_write_response_header(
        total_gas_cost=STORAGE_WRITE_GAS_COST, request_struct_size=StorageWriteRequest.SIZE
    );
    if (success == FALSE) {
        // Not enough gas to execute the syscall.
        return ();
    }

    local prev_value: felt;
    local state_entry: StateEntry*;
    %{ WriteSyscallResult %}

    // Update the contract's storage.
    static_assert StorageWriteRequest.SIZE == 3;
    assert request.reserved = 0;
    tempvar storage_ptr = state_entry.storage_ptr;
    tempvar storage_key = request.key;
    assert [storage_ptr] = DictAccess(
        key=storage_key, prev_value=prev_value, new_value=request.value
    );
    let storage_ptr = storage_ptr + DictAccess.SIZE;

    assert [revert_log] = RevertLogEntry(selector=storage_key, value=prev_value);
    let revert_log = &revert_log[1];

    // Update the state.
    dict_update{dict_ptr=contract_state_changes}(
        key=contract_address,
        prev_value=cast(state_entry, felt),
        new_value=cast(
            new StateEntry(
                class_hash=state_entry.class_hash, storage_ptr=storage_ptr, nonce=state_entry.nonce
            ),
            felt,
        ),
    );

    return ();
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L288-319)
```text
    if (is_reverted != FALSE) {
        // Create a dummy OsCarriedOutputs so that messages to L1 will be discarded.
        // The dummy is initialized with
        // OsCarriedOutputs(messages_to_l1="empty segment", messages_to_l2=0).
        %{ GenerateDummyOsOutputSegment %}
        // Create a new revert log for the reverted entry point. This will be used to revert the
        // entry point changes after calling `call_execute_syscalls`.
        let revert_log = init_revert_log();
    } else {
        assert outputs = orig_outputs;
        tempvar revert_log = orig_revert_log;
    }
    let builtin_ptrs = return_builtin_ptrs;
    with syscall_ptr {
        call_execute_syscalls(
            block_context=block_context,
            execution_context=execution_context,
            syscall_ptr_end=entry_point_return_values.syscall_ptr,
        );
    }

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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/revert.cairo (L37-71)
```text
func handle_revert{contract_state_changes: DictAccess*}(
    contract_address, revert_log_end: RevertLogEntry*
) {
    alloc_locals;

    local state_entry: StateEntry*;

    %{ PrepareStateEntryForRevert %}

    let class_hash = state_entry.class_hash;
    let storage_ptr = state_entry.storage_ptr;
    with class_hash, storage_ptr, revert_log_end {
        revert_contract_changes();
    }

    dict_update{dict_ptr=contract_state_changes}(
        key=contract_address,
        prev_value=cast(state_entry, felt),
        new_value=cast(
            new StateEntry(class_hash=class_hash, storage_ptr=storage_ptr, nonce=state_entry.nonce),
            felt,
        ),
    );

    // `revert_contract_changes()` stops where
    // `revert_log_end[0].selector == CHANGE_CONTRACT_ENTRY`.
    tempvar next_contract_address = revert_log_end[0].value;

    if (next_contract_address == CONTRACT_ADDRESS_UPPER_BOUND) {
        // Finish backward processing: this entry marks the beginning of the revert log.
        return ();
    }

    return handle_revert(contract_address=next_contract_address, revert_log_end=revert_log_end);
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L76-111)
```text
func compute_contract_state_commitment{hash_ptr: HashBuiltin*, range_check_ptr}(
    contract_state_changes_start: DictAccess*,
    n_contract_state_changes: felt,
    patricia_update_constants: PatriciaUpdateConstants*,
) -> CommitmentUpdate {
    alloc_locals;

    // Hash the entries of the contract state changes to prepare the input for the commitment tree
    // multi-update.
    let (local hashed_state_changes: DictAccess*) = alloc();
    compute_contract_state_commitment_inner(
        state_changes=contract_state_changes_start,
        n_contract_state_changes=n_contract_state_changes,
        hashed_state_changes=hashed_state_changes,
        patricia_update_constants=patricia_update_constants,
    );

    // Compute the initial and final roots of the contracts' state tree.
    local initial_root;
    local final_root;

    %{ SetPreimageForStateCommitments %}

    // Call patricia_update_using_update_constants() instead of patricia_update()
    // in order not to repeat globals_pow2 calculation.
    patricia_update_using_update_constants(
        patricia_update_constants=patricia_update_constants,
        update_ptr=hashed_state_changes,
        n_updates=n_contract_state_changes,
        height=MERKLE_HEIGHT,
        prev_root=initial_root,
        new_root=final_root,
    );

    return (CommitmentUpdate(initial_root=initial_root, final_root=final_root));
}
```
