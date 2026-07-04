### Title
`non_reverting_select_execute_entry_point_func` Hard-Asserts `is_reverted = 0` for L1 Handler Execution, Causing Entire Block Proof to Fail on Revert — (File: `execution/execute_transaction_utils.cairo`)

---

### Summary

`non_reverting_select_execute_entry_point_func` unconditionally asserts `is_reverted = 0` after executing an entry point. When this function is used to execute an L1 handler, a reverting L1 handler causes a hard Cairo assertion failure that invalidates the **entire block proof**, not just the single transaction. An unprivileged L1 message sender can trigger this by crafting a message whose handler will revert after the sequencer has already committed to including it with `is_reverted = FALSE`.

---

### Finding Description

`non_reverting_select_execute_entry_point_func` is defined in `execute_transaction_utils.cairo`:

```cairo
func non_reverting_select_execute_entry_point_func{...}(...) -> (...) {
    let revert_log = init_revert_log();
    let (is_reverted, retdata_size, retdata, is_deprecated) = select_execute_entry_point_func{
        revert_log=revert_log
    }(block_context=block_context, execution_context=execution_context);
    assert is_reverted = 0;   // <-- hard Cairo assertion; fails the entire proof
    return (retdata_size, retdata, is_deprecated);
}
``` [1](#0-0) 

This function is called unconditionally inside `execute_l1_handler_transaction` after the message has already been consumed and written to the output segment:

```cairo
// Consume L1-to-L2 message.
consume_l1_to_l2_message(execution_context=tx_execution_context, nonce=nonce);
let remaining_gas = L1_HANDLER_L2_GAS_MAX_AMOUNT;
non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
    block_context=block_context, execution_context=tx_execution_context
);
``` [2](#0-1) 

The only guard against this path is the early-exit `is_reverted` hint check at the very top of `execute_l1_handler_transaction`:

```cairo
local is_reverted;
%{ IsReverted %}
// Skip the execution step for reverted transaction.
if (is_reverted != FALSE) {
    %{ EndTx %}
    return ();
}
``` [3](#0-2) 

This guard is set by the sequencer hint `%{ IsReverted %}` **before** execution. If the sequencer marks `is_reverted = FALSE` (expecting success) but the L1 handler actually reverts at proof time, the `assert is_reverted = 0` inside `non_reverting_select_execute_entry_point_func` fails, aborting the entire block proof.

**Contrast with invoke transactions**, which have a post-validation `is_reverted` check that gracefully skips `__execute__` when the transaction is reverted:

```cairo
local is_reverted;
%{ IsReverted %}
check_is_reverted(is_reverted);
if (is_reverted == FALSE) {
    // Execute only non-reverted transactions.
    non_reverting_select_execute_entry_point_func(...);
}
``` [4](#0-3) 

L1 handlers have no equivalent post-execution graceful path. The same hard-assert pattern also applies to `charge_fee` (line 161) and all `__validate*__` entry points (lines 149, 677, 804), but the L1 handler case is the most attacker-accessible because L1-to-L2 messages originate from unprivileged L1 senders. [5](#0-4) 

---

### Impact Explanation

If the `assert is_reverted = 0` fires, the Cairo proof for the entire block is invalid. The sequencer must discard the block and rebuild it. An attacker who can repeatedly trigger this condition forces the sequencer into a continuous rebuild loop, preventing the network from finalizing any new transactions — matching the **High: Network not being able to confirm new transactions** impact class.

---

### Likelihood Explanation

The attack is reachable by any unprivileged L1 message sender:

1. The attacker sends an L1-to-L2 message targeting an L2 contract whose handler will revert under a specific storage condition.
2. The attacker simultaneously submits an L2 invoke transaction that sets that storage condition.
3. The sequencer simulates the L1 handler in isolation (before the storage-mutating invoke runs) and observes success; it sets `is_reverted = FALSE`.
4. In the actual block, the invoke transaction executes first and mutates the storage.
5. When the L1 handler executes, it reverts.
6. `assert is_reverted = 0` fires; the block proof is invalid.

This mirrors the external report's front-running pattern exactly: the attacker invalidates a "committed" item between simulation and finalization. The sequencer has no on-chain mechanism to detect this race; it relies entirely on off-chain simulation accuracy.

---

### Recommendation

Replace the unconditional `non_reverting_select_execute_entry_point_func` call in `execute_l1_handler_transaction` with `select_execute_entry_point_func` and handle the returned `is_reverted` flag gracefully — rolling back state changes via `handle_revert` and continuing block execution, exactly as the revert machinery in `execute_entry_point.cairo` already does for inner calls. [6](#0-5) 

Additionally, the same pattern should be audited for `charge_fee` and all `__validate*__` call sites that use `non_reverting_select_execute_entry_point_func`.

---

### Proof of Concept

1. Deploy L2 contract `Victim` with an `@l1_handler` that reads storage key `K` and panics if `K == 1`.
2. From L1, send a message to `Victim` (this queues the L1 handler).
3. From an L2 account, submit an invoke transaction to `Victim` that writes `K = 1`.
4. The sequencer simulates the L1 handler first (K is still 0 → success) and sets `is_reverted = FALSE`.
5. The sequencer builds a block with the invoke transaction ordered before the L1 handler.
6. During proof generation: invoke runs → `K = 1`; L1 handler runs → panics → `is_reverted = 1`.
7. `assert is_reverted = 0` at `execute_transaction_utils.cairo:195` fails.
8. The block proof is rejected; the sequencer must rebuild without the L1 handler or with `is_reverted = TRUE`.
9. Repeat from step 2 to sustain the denial of service. [7](#0-6) [8](#0-7)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L181-196)
```text
func non_reverting_select_execute_entry_point_func{
    range_check_ptr,
    remaining_gas: felt,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    outputs: OsCarriedOutputs*,
}(block_context: BlockContext*, execution_context: ExecutionContext*) -> (
    retdata_size: felt, retdata: felt*, is_deprecated: felt
) {
    let revert_log = init_revert_log();
    let (is_reverted, retdata_size, retdata, is_deprecated) = select_execute_entry_point_func{
        revert_log=revert_log
    }(block_context=block_context, execution_context=execution_context);
    assert is_reverted = 0;
    return (retdata_size, retdata, is_deprecated);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L160-163)
```text
    let remaining_gas = DEFAULT_INITIAL_GAS_COST;
    non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
        block_context=block_context, execution_context=&execution_context
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L338-358)
```text
    local is_reverted;
    %{ IsReverted %}
    check_is_reverted(is_reverted);
    if (is_reverted == FALSE) {
        // Execute only non-reverted transactions.
        with remaining_gas {
            cap_remaining_gas(max_gas=EXECUTE_MAX_SIERRA_GAS);
            non_reverting_select_execute_entry_point_func(
                block_context=block_context, execution_context=updated_tx_execution_context
            );
        }
    } else {
        // Align the stack with the `if` branch to avoid revoked references.
        tempvar range_check_ptr = range_check_ptr;
        tempvar remaining_gas = remaining_gas;
        tempvar builtin_ptrs = builtin_ptrs;
        tempvar contract_state_changes = contract_state_changes;
        tempvar contract_class_changes = contract_class_changes;
        tempvar outputs = outputs;
        tempvar _dummy_return_value: non_reverting_select_execute_entry_point_func.Return;
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L374-452)
```text
func execute_l1_handler_transaction{
    range_check_ptr,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    outputs: OsCarriedOutputs*,
}(block_context: BlockContext*) {
    alloc_locals;

    %{ StartTx %}
    local is_reverted;
    %{ IsReverted %}
    // Skip the execution step for reverted transaction.
    if (is_reverted != FALSE) {
        %{ EndTx %}
        return ();
    }

    // TODO(Yoni): currently, the contract state is not fetched for reverted L1 handlers.
    //   Once block hash is supported, we should fetch the contract state for them as well.
    local entry_point_selector;
    %{ TxEntryPointSelector %}
    let (local tx_execution_context: ExecutionContext*) = get_invoke_tx_execution_context(
        block_context=block_context,
        entry_point_type=ENTRY_POINT_TYPE_L1_HANDLER,
        entry_point_selector=entry_point_selector,
    );
    local tx_execution_info: ExecutionInfo* = tx_execution_context.execution_info;

    local nonce;
    %{ LoadTxNonceL1Handler %}
    local chain_id = block_context.os_global_context.starknet_os_config.chain_id;

    let pedersen_ptr = builtin_ptrs.selectable.pedersen;
    with pedersen_ptr {
        let transaction_hash = compute_l1_handler_transaction_hash(
            execution_context=tx_execution_context, chain_id=chain_id, nonce=nonce
        );
    }
    update_pedersen_in_builtin_ptrs(pedersen_ptr=pedersen_ptr);

    %{ AssertTransactionHash %}

    // Write the transaction info and complete the ExecutionInfo struct.
    tempvar tx_info = tx_execution_info.tx_info;
    assert [tx_info] = TxInfo(
        version=L1_HANDLER_VERSION,
        account_contract_address=tx_execution_info.contract_address,
        max_fee=0,
        signature_start=cast(0, felt*),
        signature_end=cast(0, felt*),
        transaction_hash=transaction_hash,
        chain_id=chain_id,
        nonce=nonce,
        resource_bounds_start=cast(0, ResourceBounds*),
        resource_bounds_end=cast(0, ResourceBounds*),
        tip=0,
        paymaster_data_start=cast(0, felt*),
        paymaster_data_end=cast(0, felt*),
        nonce_data_availability_mode=0,
        fee_data_availability_mode=0,
        account_deployment_data_start=cast(0, felt*),
        account_deployment_data_end=cast(0, felt*),
        proof_facts_start=cast(0, felt*),
        proof_facts_end=cast(0, felt*),
    );
    fill_deprecated_tx_info(tx_info=tx_info, dst=tx_execution_context.deprecated_tx_info);
    assert_deprecated_tx_fields_consistency(tx_info=tx_info);

    // Consume L1-to-L2 message.
    consume_l1_to_l2_message(execution_context=tx_execution_context, nonce=nonce);
    let remaining_gas = L1_HANDLER_L2_GAS_MAX_AMOUNT;
    non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
        block_context=block_context, execution_context=tx_execution_context
    );

    %{ EndTx %}
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
