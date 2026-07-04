### Title
Unchecked ERC20 Transfer Return Value in `charge_fee` Allows Fee-Free Transaction Processing — (File: `execution/transaction_impls.cairo`)

---

### Summary

`charge_fee` in `transaction_impls.cairo` calls the fee token's `transfer` entry point via `non_reverting_select_execute_entry_point_func` but **completely discards the return value**. If the fee token's `transfer` implementation returns `false` (failure) without reverting — valid ERC20 behavior — the OS accepts the proof and finalizes the transaction without the sequencer receiving any fee payment.

---

### Finding Description

In `charge_fee` (lines 160–164), the OS executes an ERC20 `transfer` call on the fee token contract:

```cairo
let remaining_gas = DEFAULT_INITIAL_GAS_COST;
non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
    block_context=block_context, execution_context=&execution_context
);
return ();
``` [1](#0-0) 

`non_reverting_select_execute_entry_point_func` returns a tuple `(retdata_size, retdata, is_deprecated)`:

```cairo
func non_reverting_select_execute_entry_point_func{...}(...) -> (
    retdata_size: felt, retdata: felt*, is_deprecated: felt
) {
    ...
    assert is_reverted = 0;
    return (retdata_size, retdata, is_deprecated);
}
``` [2](#0-1) 

The function asserts `is_reverted = 0` — meaning it panics if the call **reverts**. However, it does **not** check the actual return data. An ERC20 `transfer` that returns `false` (indicating failure) without reverting would pass this assertion, and the returned boolean is never inspected.

Contrast this with how `run_validate` and `execute_deploy_account_transaction` handle their return data — they explicitly assert the return value equals `VALIDATED`:

```cairo
let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(...)
if (is_deprecated == 0) {
    assert retdata_size = 1;
    assert retdata[0] = VALIDATED;
}
``` [3](#0-2) 

`charge_fee` performs no equivalent check on the transfer's return value.

---

### Impact Explanation

If the fee token's `transfer` entry point returns `false` (non-reverting failure — valid ERC20 behavior), the OS:

1. Does not detect the failure (no revert, so `is_reverted = 0` passes).
2. Discards the `false` return value entirely.
3. Finalizes the transaction and includes it in the proven block.
4. The sequencer receives **zero fee** for processing the transaction.

This is a **direct loss of funds** — the sequencer provides computational work and state transitions without compensation. Across many transactions, this drains sequencer revenue entirely.

---

### Likelihood Explanation

The current StarkNet fee tokens (STRK, ETH-wrapped) are standard contracts that revert on insufficient balance, making this dormant under normal conditions. However:

- The fee token address is a configurable protocol parameter (`starknet_os_config.fee_token_address`).
- Any upgrade or replacement of the fee token with a non-reverting ERC20 (which is spec-compliant) would immediately activate this vulnerability.
- A transaction sender with zero balance could submit transactions that get included in proven blocks without paying fees, since the OS circuit does not enforce the transfer succeeded. [4](#0-3) 

---

### Recommendation

After calling `non_reverting_select_execute_entry_point_func` in `charge_fee`, capture and validate the return data to confirm the transfer returned `true`:

```cairo
let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func{
    remaining_gas=remaining_gas
}(block_context=block_context, execution_context=&execution_context);
if (is_deprecated == 0) {
    // ERC20 transfer must return a single `true` value.
    assert retdata_size = 1;
    assert retdata[0] = 1;  // true
}
```

This mirrors the pattern already used for `__validate__`, `__validate_declare__`, and `__validate_deploy__` return value checks.

---

### Proof of Concept

1. Deploy a fee token contract whose `transfer` entry point returns `Uint256(0, 0)` (i.e., `false`) without reverting when the sender has insufficient balance.
2. Submit a V3 transaction from an account with zero fee token balance.
3. The OS calls `charge_fee`, which calls `transfer` on the fee token.
4. The fee token returns `false` (no revert) — `is_reverted = 0`, assertion passes.
5. Return data `[0]` is discarded by `charge_fee`.
6. The OS finalizes the transaction in the proven block output.
7. The sequencer receives no fee despite processing the transaction. [5](#0-4)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L111-165)
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L188-196)
```text
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
