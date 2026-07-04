### Title
Missing Constructor Revert Handling in `deploy` Syscall Causes OS Proof Generation Failure - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo`)

---

### Summary

The StarkNet OS unconditionally asserts that a constructor cannot revert when processing the `deploy` syscall. If the sequencer's blockifier handles a reverting constructor gracefully (returning a failure response to the calling contract), but the OS panics on the same scenario, any block containing such a transaction cannot be proven, halting the network.

---

### Finding Description

`deploy_contract.cairo` is called from two sites: `execute_deploy_account_transaction` (for top-level deploy-account transactions) and `execute_deploy` (for the `deploy` syscall invoked from within a running contract). In both cases, after invoking the constructor via `select_execute_entry_point_func`, the function unconditionally asserts success:

```cairo
// The deprecated deploy syscalls do not support reverts.
assert is_reverted = 0;
``` [1](#0-0) 

The comment labels this a "deprecated" limitation, but the same `deploy_contract` function is called directly from the **new** `execute_deploy` syscall handler, which also carries an explicit TODO acknowledging the missing failure path:

```cairo
// Write the response header.
// TODO(Yoni, 1/1/2026): support failures.
assert [response_header] = ResponseHeader(gas=remaining_gas, failure_flag=0);
``` [2](#0-1) 

The `execute_deploy` function is reached through the syscall dispatch loop in `execute_syscalls`, which handles the `DEPLOY_SELECTOR` case: [3](#0-2) 

When a constructor reverts, the Cairo `assert is_reverted = 0` constraint is violated. In Cairo's constraint system, a violated assertion means no valid witness (execution trace) can be produced — the OS program becomes unsatisfiable and no proof can be generated for the block.

The `execute_deploy` syscall is designed to be called from within arbitrary user contracts. The calling contract is expected to receive a `failure_flag` in the response header and handle it. The OS never writes `failure_flag=1` for deploy — it is hardcoded to `0` — and panics before reaching that point if the constructor reverts.

---

### Impact Explanation

If the sequencer's blockifier handles a reverting constructor in the `deploy` syscall gracefully (propagating `failure_flag=1` back to the calling contract, allowing the outer transaction to succeed), the sequencer will include such a transaction in a block. When the OS attempts to prove that block, it hits `assert is_reverted = 0` and cannot produce a valid proof. The block cannot be finalized, and the network halts.

**Impact: High — Network not being able to confirm new transactions (total network shutdown).**

---

### Likelihood Explanation

Any unprivileged user can:
1. Declare a contract class whose constructor unconditionally reverts (e.g., `assert 1 = 0`).
2. Deploy a wrapper contract that calls the `deploy` syscall with that class hash and handles the expected failure response.
3. Submit an invoke transaction calling the wrapper.

The sequencer's blockifier simulates the transaction, sees the outer call succeed (the wrapper handles the deploy failure), and includes it in a block. The OS then fails to prove the block.

The TODO comment `// TODO(Yoni, 1/1/2026): support failures.` confirms the developers are aware the blockifier supports this path while the OS does not — making the discrepancy explicit and the attack surface real.

---

### Recommendation

In `deploy_contract.cairo`, replace the unconditional `assert is_reverted = 0` with a conditional branch that propagates the revert status back to the caller when invoked from the `deploy` syscall context. In `execute_deploy` (`syscall_impls.cairo`), write `failure_flag=is_reverted` in the response header and handle the reverted state (including reverting state changes via the revert log), mirroring the pattern used in `contract_call_helper`: [4](#0-3) 

The `deploy_contract` function should return `is_reverted` to its callers and let each call site decide how to handle it, rather than asserting success unconditionally.

---

### Proof of Concept

1. Declare a class with a constructor: `func constructor(...) { assert 1 = 0; }` — always reverts.
2. Declare a wrapper class with an `__execute__` that calls `deploy(class_hash=<reverting_class>, ...)` and reads the response `failure_flag` (expecting `1`).
3. Deploy the wrapper contract.
4. Submit an invoke transaction to the wrapper's `__execute__`.
5. The blockifier simulates: constructor reverts → `deploy` syscall returns `failure_flag=1` → wrapper reads it and returns successfully → transaction is included in the block.
6. The OS processes the block: `deploy_contract` calls `select_execute_entry_point_func` → constructor reverts → `is_reverted=1` → `assert is_reverted = 0` fails → OS cannot produce a valid proof → block cannot be finalized → network halts. [5](#0-4)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L29-92)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L402-448)
```text
// Executes the entry point and writes the corresponding response to the syscall_ptr.
// Assumes that syscall_ptr points at the response header.
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L165-173)
```text
    if (selector == DEPLOY_SELECTOR) {
        execute_deploy(block_context=block_context, caller_execution_context=execution_context);
        %{ OsLoggerExitSyscall %}
        return execute_syscalls(
            block_context=block_context,
            execution_context=execution_context,
            syscall_ptr_end=syscall_ptr_end,
        );
    }
```
