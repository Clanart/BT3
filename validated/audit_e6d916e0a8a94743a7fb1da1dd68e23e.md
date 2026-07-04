### Title
Unhandled Constructor Revert in `execute_deploy` Syscall Causes Unprovable Block — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo` and `syscall_impls.cairo`)

---

### Summary

The `deploy_contract` function unconditionally asserts `is_reverted = 0` after invoking a constructor via `select_execute_entry_point_func`. The `execute_deploy` syscall handler also hardcodes `failure_flag=0` in the response header. Together, these mean the StarkNet OS cannot generate a valid ZK proof for any block that contains a transaction where a `deploy` syscall's constructor reverts. An unprivileged user can trigger this by sending an invoke transaction that calls `deploy` with a class whose constructor always reverts, causing the OS to fail proof generation and halting the network.

---

### Finding Description

In `deploy_contract.cairo`, after invoking the constructor entry point, the OS hard-asserts the constructor did not revert:

```cairo
// Invoke the contract constructor.
let (is_reverted, retdata_size, retdata, _is_deprecated) = select_execute_entry_point_func(
    block_context=block_context, execution_context=constructor_execution_context
);
// ...
// The deprecated deploy syscalls do not support reverts.
assert is_reverted = 0;
``` [1](#0-0) 

The comment "The deprecated deploy syscalls do not support reverts" reveals this assertion was designed for the deprecated deploy path. However, the new `execute_deploy` syscall in `syscall_impls.cairo` also calls `deploy_contract` and inherits this hard assertion:

```cairo
with remaining_gas {
    let (retdata_size, retdata) = deploy_contract(
        block_context=block_context, constructor_execution_context=constructor_execution_context
    );
}

// TODO(Yoni, 1/1/2026): support failures.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
``` [2](#0-1) 

The `failure_flag` is hardcoded to `0` (success), and `deploy_contract` asserts `is_reverted = 0`. If a constructor reverts, the Cairo assertion fails, making the OS unable to produce a valid proof for the block.

This is the direct analog to the external report: just as `DAO._executeApproval` did not handle an external call revert (preventing `markBallotAsFinalized` from executing), the OS `execute_deploy` path does not handle a constructor revert, preventing the OS from completing proof generation for the block.

---

### Impact Explanation

**High. Network not being able to confirm new transactions (total network shutdown).**

The StarkNet OS is a ZK proof program. If the Cairo assertion `assert is_reverted = 0` fails during proof generation, the entire block proof is invalid. The sequencer includes reverted transactions in blocks (they still consume gas and pay fees). If a block contains a transaction where a `deploy` syscall's constructor reverts, the OS cannot produce a valid proof for that block. No subsequent blocks can be proven until the issue is resolved, halting the network.

---

### Likelihood Explanation

Any unprivileged user can:
1. Declare a contract class whose constructor unconditionally reverts.
2. Send an invoke transaction that calls the `deploy` syscall with that class hash.
3. The sequencer executes the transaction, the constructor reverts, and the outer transaction is marked as reverted (still included in the block, as reverted transactions pay fees).
4. The OS attempts to prove the block, hits `assert is_reverted = 0`, and fails.

This requires no privileged access, no leaked keys, and no operator cooperation. The attack is cheap and deterministic.

---

### Recommendation

The `deploy_contract` function must be refactored to return `is_reverted` to its callers rather than asserting it is zero. The `execute_deploy` syscall handler must then propagate the revert outcome into the `ResponseHeader.failure_flag` field and handle the revert log rollback, consistent with how `contract_call_helper` handles reverted calls:

```cairo
// In deploy_contract: remove `assert is_reverted = 0;`, return is_reverted.

// In execute_deploy (syscall_impls.cairo):
// Replace hardcoded failure_flag=0 with the actual is_reverted value.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=is_reverted);
```

The revert log entries written inside `deploy_contract` must also be unwound via `handle_revert` when `is_reverted = 1`, consistent with the existing revert mechanism in `revert.cairo`. [3](#0-2) 

---

### Proof of Concept

**Attack steps:**

1. Declare a class with a constructor that always reverts (e.g., `assert 1 = 0`).
2. Send an invoke transaction from any account that calls the `deploy` syscall with that class hash and any salt.
3. The sequencer executes the transaction: the constructor reverts, the outer transaction is marked reverted, and it is included in the block (fees are charged).
4. The OS attempts to prove the block. Execution reaches `deploy_contract.cairo` line 91: `assert is_reverted = 0` — this fails because `is_reverted = 1`.
5. The OS cannot produce a valid proof. The block is unprovable. The network halts.

**Relevant code path:**

```
execute_transactions_inner
  → execute_invoke_function_transaction        [transaction_impls.cairo]
    → non_reverting_select_execute_entry_point_func
      → execute_syscalls                       [execute_syscalls.cairo]
        → execute_deploy                       [syscall_impls.cairo:452]
          → deploy_contract                    [deploy_contract.cairo:29]
            → select_execute_entry_point_func  (constructor reverts → is_reverted=1)
            → assert is_reverted = 0           ← FAILS, proof generation aborts
``` [4](#0-3) [5](#0-4)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L29-93)
```text
func deploy_contract{
    range_check_ptr,
    remaining_gas: felt,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    revert_log: RevertLogEntry*,
    outputs: OsCarriedOutputs*,
}(block_context: BlockContext*, constructor_execution_context: ExecutionContext*) -> (
    retdata_size: felt, retdata: felt*
) {
    alloc_locals;

    local contract_address = constructor_execution_context.execution_info.contract_address;

    // Assert that we don't deploy to one of the reserved addresses.
    assert_not_zero(
        (contract_address - ORIGIN_ADDRESS) * (contract_address - BLOCK_HASH_CONTRACT_ADDRESS) * (
            contract_address - ALIAS_CONTRACT_ADDRESS
        ) * (contract_address - RESERVED_CONTRACT_ADDRESS),
    );

    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}
    assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;
    assert state_entry.nonce = 0;

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

    // Entries before this point belong to the caller.
    // Note the caller is the deployer (not the caller of the deployer).
    assert [revert_log] = RevertLogEntry(
        selector=CHANGE_CONTRACT_ENTRY,
        value=constructor_execution_context.execution_info.caller_address,
    );
    let revert_log = &revert_log[1];

    assert [revert_log] = RevertLogEntry(
        selector=CHANGE_CLASS_ENTRY, value=UNINITIALIZED_CLASS_HASH
    );
    let revert_log = &revert_log[1];

    // Invoke the contract constructor.
    let (is_reverted, retdata_size, retdata, _is_deprecated) = select_execute_entry_point_func(
        block_context=block_context, execution_context=constructor_execution_context
    );

    // Entries before this point belong to the deployed contract.
    assert [revert_log] = RevertLogEntry(selector=CHANGE_CONTRACT_ENTRY, value=contract_address);
    let revert_log = &revert_log[1];

    // The deprecated deploy syscalls do not support reverts.
    assert is_reverted = 0;
    return (retdata_size=retdata_size, retdata=retdata);
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L452-556)
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
    let request = cast(syscall_ptr + RequestHeader.SIZE, DeployRequest*);
    local constructor_calldata_start: felt* = request.constructor_calldata_start;
    local constructor_calldata_size = request.constructor_calldata_end - constructor_calldata_start;

    let specific_base_gas_cost = DEPLOY_GAS_COST + DEPLOY_CALLDATA_FACTOR_GAS_COST *
        constructor_calldata_size;
    let (success, remaining_gas) = reduce_syscall_base_gas(
        specific_base_gas_cost=specific_base_gas_cost, request_struct_size=DeployRequest.SIZE
    );
    if (success == FALSE) {
        // Not enough gas to execute the syscall.
        return ();
    }

    local caller_execution_info: ExecutionInfo* = caller_execution_context.execution_info;
    local caller_address = caller_execution_info.contract_address;

    // Verify deploy_from_zero is either 0 (FALSE) or 1 (TRUE).
    tempvar deploy_from_zero = request.deploy_from_zero;
    assert deploy_from_zero * (deploy_from_zero - 1) = 0;
    // Set deployer_address to 0 if request.deploy_from_zero is TRUE.
    let deployer_address = (1 - deploy_from_zero) * caller_address;

    let selectable_builtins = &builtin_ptrs.selectable;
    let hash_ptr = selectable_builtins.pedersen;
    with hash_ptr {
        let (contract_address) = get_contract_address(
            salt=request.contract_address_salt,
            class_hash=request.class_hash,
            constructor_calldata_size=constructor_calldata_size,
            constructor_calldata=constructor_calldata_start,
            deployer_address=deployer_address,
        );
    }
    tempvar builtin_ptrs = new BuiltinPointers(
        selectable=SelectableBuiltins(
            pedersen=hash_ptr,
            range_check=selectable_builtins.range_check,
            ecdsa=selectable_builtins.ecdsa,
            bitwise=selectable_builtins.bitwise,
            ec_op=selectable_builtins.ec_op,
            poseidon=selectable_builtins.poseidon,
            segment_arena=selectable_builtins.segment_arena,
            range_check96=selectable_builtins.range_check96,
            add_mod=selectable_builtins.add_mod,
            mul_mod=selectable_builtins.mul_mod,
        ),
        non_selectable=builtin_ptrs.non_selectable,
    );

    tempvar constructor_execution_context = new ExecutionContext(
        entry_point_type=ENTRY_POINT_TYPE_CONSTRUCTOR,
        class_hash=request.class_hash,
        calldata_size=constructor_calldata_size,
        calldata=constructor_calldata_start,
        execution_info=new ExecutionInfo(
            block_info=caller_execution_info.block_info,
            tx_info=caller_execution_info.tx_info,
            caller_address=caller_address,
            contract_address=contract_address,
            selector=CONSTRUCTOR_ENTRY_POINT_SELECTOR,
        ),
        deprecated_tx_info=caller_execution_context.deprecated_tx_info,
    );

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
}
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
