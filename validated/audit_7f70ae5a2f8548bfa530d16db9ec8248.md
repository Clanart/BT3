### Title
Unchecked ERC20 Transfer Return Value in `charge_fee` Allows Fee-Free Transaction Execution — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

The `charge_fee` function in `transaction_impls.cairo` invokes the fee token's ERC20 `transfer` entry point via `non_reverting_select_execute_entry_point_func` but **completely discards the return value**. If the fee token's `transfer` returns `false` (without reverting — valid ERC20 behavior), the OS silently proceeds as if the fee was paid. The sequencer loses fee revenue and the transaction sender effectively executes for free.

---

### Finding Description

In `charge_fee`, the ERC20 transfer is executed as:

```cairo
let remaining_gas = DEFAULT_INITIAL_GAS_COST;
non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
    block_context=block_context, execution_context=&execution_context
);
return ();
``` [1](#0-0) 

The function signature of `non_reverting_select_execute_entry_point_func` returns `(retdata_size: felt, retdata: felt*, is_deprecated: felt)`: [2](#0-1) 

The function internally asserts `is_reverted = 0` — meaning a **reverting** transfer would invalidate the proof. However, it does **not** assert anything about the return data. For a Cairo 1 ERC20 `transfer`, the retdata contains the boolean success value. If the transfer returns `false` without reverting, `is_reverted` remains `0`, the assertion passes, and the ignored retdata contains `false`. The OS proceeds as if the fee was paid.

Contrast this with every other call site of `non_reverting_select_execute_entry_point_func`, which explicitly validates retdata:

```cairo
// run_validate:
assert retdata_size = 1;
assert retdata[0] = VALIDATED;
``` [3](#0-2) 

`charge_fee` is the only call site that performs **no check** on the returned data. [4](#0-3) 

---

### Impact Explanation

**Critical. Direct loss of funds.**

If the fee token's `transfer` entry point returns `false` (without reverting) — which is valid ERC20 behavior for insufficient balance or other failure conditions — the sequencer (fee recipient) receives nothing. The OS writes a valid state transition and the block is proven without the fee ever being transferred. The sequencer permanently loses the fee revenue for every such transaction.

---

### Likelihood Explanation

Any unprivileged transaction sender can trigger this by submitting a transaction where their fee token balance is insufficient, provided the fee token contract returns `false` on failure rather than reverting. The current STRK token implementation is expected to revert, but:

1. The OS places **no constraint** on the fee token's revert-vs-return-false behavior — it is an architectural assumption, not an enforced invariant.
2. If the fee token contract is upgraded or replaced (the address comes from `block_context.os_global_context.starknet_os_config.fee_token_address`), a version that returns `false` on failure would immediately expose this path.
3. The `assert_nn_le(calldata.amount.low, max_fee)` check only bounds the fee amount against `max_fee` — it does not verify the sender's balance. [5](#0-4) 

---

### Recommendation

After calling `non_reverting_select_execute_entry_point_func` in `charge_fee`, validate the ERC20 transfer return value for non-deprecated contracts:

```cairo
let (retdata_size, retdata, is_deprecated) =
    non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
        block_context=block_context, execution_context=&execution_context
    );
if (is_deprecated == 0) {
    assert retdata_size = 1;
    assert retdata[0] = 1;  // ERC20 transfer must return true
}
return ();
```

This mirrors the pattern already used for `__validate__`, `__validate_declare__`, and `__validate_deploy__` entry points.

---

### Proof of Concept

1. Attacker deploys an account contract backed by a fee token that implements `transfer` to return `false` (without reverting) when balance is insufficient.
2. Attacker submits an invoke transaction with `max_l2_gas_amount * max_price_per_unit > 0` (non-zero fee bound) but zero actual fee token balance.
3. The OS executes `charge_fee`, calls the fee token's `transfer`, receives `retdata = [0]` (false), but ignores it because `is_reverted = 0`.
4. `assert_nn_le(calldata.amount.low, max_fee)` passes (actual fee ≤ max fee).
5. The block is proven with a valid state transition. The sequencer receives no fee. The attacker's transaction is executed for free.
6. Repeated across many transactions: permanent, cumulative direct loss of funds to the sequencer.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L104-165)
```text
// Charges a fee from the user.
// If max_fee is not 0, validates that the selector matches the entry point of an account contract
// and executes an ERC20 transfer on the behalf of that account contract.
//
// Arguments:
// block_context - a global context that is fixed throughout the block.
// tx_execution_context - The execution context of the transaction that pays the fee.
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
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L149-156)
```text
    let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(
        block_context=block_context, execution_context=validate_execution_context
    );
    if (is_deprecated == 0) {
        %{ CheckRetdataForDebug %}
        assert retdata_size = 1;
        assert retdata[0] = VALIDATED;
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L181-197)
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
}
```
