### Title
Unconditional `assert is_reverted = 0` in `deploy_contract` Makes Block Unprovable When Constructor Reverts — (File: `execution/deploy_contract.cairo`)

### Summary

`deploy_contract` calls `select_execute_entry_point_func` to run the constructor, which can legitimately return `is_reverted=1`, but then unconditionally asserts `is_reverted = 0`. The outer caller `execute_deploy_account_transaction` initializes a `revert_log` and passes it in — signaling design intent to support constructor reverts — yet the inner function's hard assertion makes the OS proof unprovable whenever a constructor reverts, causing a network halt.

### Finding Description

In `deploy_contract.cairo`, after invoking the constructor:

```cairo
// Invoke the contract constructor.
let (is_reverted, retdata_size, retdata, _is_deprecated) = select_execute_entry_point_func(
    block_context=block_context, execution_context=constructor_execution_context
);
...
// The deprecated deploy syscalls do not support reverts.
assert is_reverted = 0;
``` [1](#0-0) 

`select_execute_entry_point_func` is the general entry-point dispatcher that returns `is_reverted=1` for any constructor that fails (out-of-gas, assertion failure, explicit revert). The comment "The deprecated deploy syscalls do not support reverts" reveals this assertion was written for the deprecated deploy-syscall path, but the same `deploy_contract` function is also called from `execute_deploy_account_transaction` — a fully public, non-deprecated transaction type. [2](#0-1) 

The outer caller `execute_deploy_account_transaction` explicitly initializes a `revert_log` and passes it into `deploy_contract`, which is the standard pattern used throughout the OS to support reverts:

```cairo
let revert_log = init_revert_log();
deploy_contract{revert_log=revert_log}(
    block_context=block_context, constructor_execution_context=constructor_execution_context
);
``` [3](#0-2) 

This is the direct M-13 analog: the outer function (`execute_deploy_account_transaction`) is designed to support reverts (it carries a `revert_log`), but the inner function (`deploy_contract`) has a more restrictive precondition — `assert is_reverted = 0` — that the outer function cannot satisfy when the constructor reverts. The inner function's restriction is incompatible with the outer function's legitimate use case.

Inside `execute_entry_point`, when `is_reverted != FALSE`, `handle_revert` is called to roll back the constructor's storage changes before returning `is_reverted=1` to `deploy_contract`. So the constructor's state changes are already cleanly reverted at the point `deploy_contract` receives `is_reverted=1`. The `assert is_reverted = 0` then fires on a legitimately-handled revert, making the proof unprovable. [4](#0-3) 

### Impact Explanation

In Cairo, a failed `assert` makes the execution trace invalid — the proof cannot be generated. If the sequencer includes a deploy-account transaction whose constructor reverts, the entire block becomes unprovable. No further blocks can be proven until the bad transaction is excluded, halting the network's ability to confirm new transactions. This matches the allowed impact: **High — Network not being able to confirm new transactions (total network shutdown)**.

### Likelihood Explanation

Any unprivileged user can submit a deploy-account transaction referencing a class whose constructor unconditionally reverts (e.g., `assert 1 = 0`). If the sequencer's off-chain simulation (blockifier) treats a reverting constructor as a valid reverted deploy-account transaction — which is the expected Sierra-path behavior, since Sierra contracts can revert — the sequencer will include the transaction. The OS then hits `assert is_reverted = 0` and cannot produce a proof. The mismatch between the sequencer's permissive simulation and the OS's hard assertion is the exploitable gap.

### Recommendation

Remove the unconditional `assert is_reverted = 0` from `deploy_contract`. Instead, propagate `is_reverted` back to `execute_deploy_account_transaction` and handle the reverted-constructor case there (skip `__validate_deploy__`, charge fee on the reverted amount, and treat the transaction as reverted), consistent with how `execute_entry_point` already handles reverts via `handle_revert` and the `revert_log`. [5](#0-4) 

### Proof of Concept

1. Attacker declares a Sierra contract class whose constructor body is `assert 1 = 0` (always reverts).
2. Attacker submits a `DEPLOY_ACCOUNT` transaction referencing that class hash.
3. Sequencer simulates the transaction; blockifier treats the reverting constructor as a valid reverted deploy-account transaction and includes it in the block.
4. OS begins proving the block; `execute_deploy_account_transaction` calls `deploy_contract`.
5. `deploy_contract` calls `select_execute_entry_point_func` → constructor reverts → `is_reverted = 1`.
6. `assert is_reverted = 0` fires → proof generation fails.
7. Block is unprovable; network cannot advance → **total network shutdown**. [1](#0-0) [6](#0-5)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/entry_point_utils.cairo (L17-67)
```text
func select_execute_entry_point_func{
    range_check_ptr,
    remaining_gas: felt,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    revert_log: RevertLogEntry*,
    outputs: OsCarriedOutputs*,
}(block_context: BlockContext*, execution_context: ExecutionContext*) -> (
    is_reverted: felt, retdata_size: felt, retdata: felt*, is_deprecated: felt
) {
    alloc_locals;
    // TODO(Yoni): SIERRA_GAS_MODE - move back inside `execute_entry_point` functions.
    %{ EnterCall %}

    local is_deprecated;
    %{ CheckIsDeprecated %}
    // Note that the class_hash is validated in both the `if` and `else` cases, so a malicious
    // prover won't be able to produce a proof if guesses the wrong case.
    if (is_deprecated != FALSE) {
        let (is_reverted, retdata_size, retdata: felt*) = deprecated_execute_entry_point(
            block_context=block_context, execution_context=execution_context
        );
        return (
            is_reverted=is_reverted, retdata_size=retdata_size, retdata=retdata, is_deprecated=1
        );
    }

    // TODO(Yoni): SIERRA_GAS_MODE - remove once all Cairo 1 contracts run with Sierra gas mode.
    local caller_remaining_gas = remaining_gas;
    local is_sierra_gas_mode;
    %{ IsSierraGasMode %}
    if (is_sierra_gas_mode != FALSE) {
        tempvar inner_remaining_gas = remaining_gas;
    } else {
        // Run with high enough gas to avoid out-of-gas.
        tempvar inner_remaining_gas = DEFAULT_INITIAL_GAS_COST;
    }
    %{ DebugExpectedInitialGas %}

    let (is_reverted, retdata_size, retdata) = execute_entry_point{
        remaining_gas=inner_remaining_gas
    }(block_context=block_context, execution_context=execution_context);

    if (is_sierra_gas_mode != FALSE) {
        tempvar remaining_gas = inner_remaining_gas;
    } else {
        // Do not count Sierra gas for the caller in this case.
        tempvar remaining_gas = caller_remaining_gas;
    }
    return (is_reverted=is_reverted, retdata_size=retdata_size, retdata=retdata, is_deprecated=0);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L637-648)
```text
    // Constructor.
    with remaining_gas {
        // The constructor entry point runs with a validate call context.
        cap_remaining_gas(max_gas=VALIDATE_MAX_SIERRA_GAS);
        let pre_constructor_gas = remaining_gas;
        let revert_log = init_revert_log();
        deploy_contract{revert_log=revert_log}(
            block_context=block_context, constructor_execution_context=constructor_execution_context
        );
    }
    let constructor_gas_consumed = pre_constructor_gas - remaining_gas;
    tempvar remaining_gas = initial_user_gas_bound - constructor_gas_consumed;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L309-320)
```text
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
    }
```
