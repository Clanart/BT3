### Title
Unchecked ERC20 `transfer` Return Value in `charge_fee` Allows Fee Collection Bypass — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

The `charge_fee` function in `transaction_impls.cairo` invokes the ERC20 `transfer` entry point via `non_reverting_select_execute_entry_point_func` but **completely discards the return value**. The ERC20 `transfer` function returns a boolean success indicator. If the fee token contract returns `false` (transfer failed) without reverting, the OS silently proceeds, treating the transaction as fee-paid when no fee was actually collected.

---

### Finding Description

In `charge_fee` (lines 111–165 of `transaction_impls.cairo`), the OS constructs an `ExecutionContext` targeting the fee token's `transfer` entry point and calls:

```cairo
// Lines 160-164
let remaining_gas = DEFAULT_INITIAL_GAS_COST;
non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
    block_context=block_context, execution_context=&execution_context
);
return ();
```

The return tuple `(retdata_size: felt, retdata: felt*, is_deprecated: felt)` is **not captured at all**. The ERC20 `transfer` function returns `[1]` on success and `[0]` on failure. The OS never inspects `retdata[0]`.

Contrast this with every other call to `non_reverting_select_execute_entry_point_func` in the same file, which **does** check the return value:

- `run_validate` (lines 149–156 of `execute_transaction_utils.cairo`):
```cairo
let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(...);
if (is_deprecated == 0) {
    assert retdata_size = 1;
    assert retdata[0] = VALIDATED;
}
```

- `execute_declare_transaction` (lines 804–812): same pattern, asserts `retdata[0] = VALIDATED`.
- `execute_deploy_account_transaction` (lines 677–684): same pattern.

`non_reverting_select_execute_entry_point_func` (lines 181–196 of `execute_transaction_utils.cairo`) only asserts `is_reverted = 0` — it catches execution reverts but does **not** validate the returned data. A fee token that returns `false` without reverting passes this check silently. [1](#0-0) [2](#0-1) [3](#0-2) 

---

### Impact Explanation

**Critical — Direct loss of funds.**

If the fee token's `transfer` call returns `false` (without reverting), the OS proceeds to finalize the transaction as if the fee was paid. The sequencer receives no fee tokens, but the state transition is committed. Over many transactions, this results in direct, unrecoverable loss of fee revenue. Because `charge_fee` is called for every invoke, deploy-account, and declare transaction, the impact is protocol-wide and not limited to a single transaction. [4](#0-3) 

---

### Likelihood Explanation

**Medium.** The standard StarkNet ERC20 reverts on insufficient balance, which `non_reverting_select_execute_entry_point_func` would catch via `assert is_reverted = 0`. However:

1. The ERC20 standard explicitly permits returning `false` without reverting as a valid failure mode.
2. Any fee token upgrade or alternative fee token contract that follows the ERC20 spec (return-false-on-failure) would silently bypass fee collection.
3. Any unprivileged transaction sender triggers `charge_fee` — no special privilege is needed to reach this code path. [5](#0-4) 

---

### Recommendation

Capture and validate the return value of the `transfer` call in `charge_fee`, mirroring the pattern used in `run_validate` and `execute_declare_transaction`:

```cairo
let remaining_gas = DEFAULT_INITIAL_GAS_COST;
let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func{
    remaining_gas=remaining_gas
}(block_context=block_context, execution_context=&execution_context);
if (is_deprecated == 0) {
    // ERC20 transfer returns a single boolean: 1 = success.
    assert retdata_size = 1;
    assert retdata[0] = 1;  // Enforce transfer success.
}
return ();
``` [6](#0-5) 

---

### Proof of Concept

1. Deploy a custom fee token contract at the protocol's `fee_token_address` whose `transfer` function always returns `[0]` (false) without reverting.
2. Submit any V3 invoke transaction with non-zero resource bounds (so `max_fee != 0`).
3. The OS reaches `charge_fee`, constructs the `ExecutionContext` for `transfer`, and calls `non_reverting_select_execute_entry_point_func`.
4. The fee token executes, returns `[0]`, does not revert — `assert is_reverted = 0` passes.
5. `charge_fee` returns without inspecting `retdata`. The transaction is finalized.
6. The sequencer's balance is unchanged; the user paid no fee. Repeat for every transaction in the block. [7](#0-6)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L111-125)
```text
func charge_fee{
    range_check_ptr,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    outputs: OsCarriedOutputs*,
}(block_context: BlockContext*, tx_execution_context: ExecutionContext*) {
    alloc_locals;

    local tx_info: TxInfo* = tx_execution_context.execution_info.tx_info;
    let max_fee = compute_max_possible_fee(tx_info=tx_info);

    if (max_fee == 0) {
        return ();
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L127-164)
```text
    local low_actual_fee;
    %{ LoadActualFee %}
    local calldata: TransferCallData = TransferCallData(
        recipient=block_context.block_info_for_execute.sequencer_address,
        amount=Uint256(low=low_actual_fee, high=0),
    );

    // Verify that the charged amount is not larger than the transaction's max_fee field.
    assert_nn_le(calldata.amount.low, max_fee);

    // TODO(ilya, 01/01/2026): Consider caching the fee_token_class_hash.
    local fee_token_address = block_context.os_global_context.starknet_os_config.fee_token_address;
    let (fee_state_entry: StateEntry*) = dict_read{dict_ptr=contract_state_changes}(
        key=fee_token_address
    );
    let (__fp__, _) = get_fp_and_pc();
    // Use block_info directly from block_context, so that charge_fee will always run in
    // execute-mode rather than validate-mode.
    local execution_context: ExecutionContext = ExecutionContext(
        entry_point_type=ENTRY_POINT_TYPE_EXTERNAL,
        class_hash=fee_state_entry.class_hash,
        calldata_size=TransferCallData.SIZE,
        calldata=&calldata,
        execution_info=new ExecutionInfo(
            block_info=block_context.block_info_for_execute,
            tx_info=tx_info,
            caller_address=tx_info.account_contract_address,
            contract_address=fee_token_address,
            selector=TRANSFER_ENTRY_POINT_SELECTOR,
        ),
        deprecated_tx_info=tx_execution_context.deprecated_tx_info,
    );

    let remaining_gas = DEFAULT_INITIAL_GAS_COST;
    non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
        block_context=block_context, execution_context=&execution_context
    );
    return ();
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L360-365)
```text
    // Charge fee.
    charge_fee(block_context=block_context, tx_execution_context=updated_tx_execution_context);

    %{ EndTx %}

    return ();
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L149-158)
```text
    let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(
        block_context=block_context, execution_context=validate_execution_context
    );
    if (is_deprecated == 0) {
        %{ CheckRetdataForDebug %}
        assert retdata_size = 1;
        assert retdata[0] = VALIDATED;
    }

    return ();
```

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
