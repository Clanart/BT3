### Title
Unchecked Return Value of Fee Token `transfer` in `charge_fee` Allows Silent Fee Bypass — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

The `charge_fee` function in the StarkNet OS calls the fee token's `transfer` entry point via `non_reverting_select_execute_entry_point_func` but **completely discards the return value**, including the ERC20 `bool` success flag. If the fee token's `transfer` returns `false` (failure without reverting), the OS silently continues, the sequencer receives no fee, and the transaction is processed for free — a direct loss of funds.

---

### Finding Description

In `charge_fee` (lines 160–164 of `transaction_impls.cairo`), the OS invokes the fee token's `transfer` entry point:

```cairo
let remaining_gas = DEFAULT_INITIAL_GAS_COST;
non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
    block_context=block_context, execution_context=&execution_context
);
return ();
```

The function signature of `non_reverting_select_execute_entry_point_func` returns `(retdata_size: felt, retdata: felt*, is_deprecated: felt)`. The `retdata` array contains the ERC20 `transfer` return value — a `bool` indicating success or failure. This return tuple is **never captured or inspected** in `charge_fee`.

Contrast this with every other call site in the same file where the return data is explicitly validated. In `run_validate` (execute_transaction_utils.cairo, lines 149–156):

```cairo
let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(
    block_context=block_context, execution_context=validate_execution_context
);
if (is_deprecated == 0) {
    assert retdata_size = 1;
    assert retdata[0] = VALIDATED;
}
```

The same pattern is applied in `execute_declare_transaction` (lines 804–812) and `execute_deploy_account_transaction` (lines 677–684). The `charge_fee` function is the **sole call site** that discards the return data entirely.

`non_reverting_select_execute_entry_point_func` only asserts `is_reverted = 0` — it panics if the call reverts, but it does **not** inspect the returned data. A fee token whose `transfer` returns `false` without reverting passes this assertion silently.

---

### Impact Explanation

**Critical — Direct loss of funds.**

If the fee token's `transfer` returns `false` (a valid non-reverting failure path present in many ERC20 implementations), the OS does not detect the failure. The transaction is fully processed and committed to state, the nonce is incremented, and the sequencer receives zero fee. An attacker with insufficient fee token balance (or using a fee token that returns `false` on failure) can execute arbitrary transactions at zero cost, draining sequencer revenue and breaking the economic security of the protocol.

---

### Likelihood Explanation

**Low.**

The standard StarkNet fee tokens (STRK, ETH bridged) revert on transfer failure rather than returning `false`. However, the OS `fee_token_address` is a configurable protocol parameter (`block_context.os_global_context.starknet_os_config.fee_token_address`). Any fee token contract that follows the ERC20 pattern of returning `false` on failure — rather than reverting — triggers this silent bypass. The missing check is a latent correctness flaw that becomes exploitable under any such token configuration.

---

### Recommendation

Capture and validate the return data of the fee token `transfer` call in `charge_fee`, consistent with every other call site in the file:

```cairo
let remaining_gas = DEFAULT_INITIAL_GAS_COST;
let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func{
    remaining_gas=remaining_gas
}(block_context=block_context, execution_context=&execution_context);
if (is_deprecated == 0) {
    // ERC20 transfer returns (success: bool); assert it is true (1).
    assert retdata_size = 1;
    assert retdata[0] = 1;
}
return ();
```

This mirrors the validation pattern already applied to `__validate__`, `__validate_declare__`, and `__validate_deploy__` entry points.

---

### Proof of Concept

**Root cause location:** [1](#0-0) 

The return value is silently dropped — no `(retdata_size, retdata, is_deprecated)` binding.

**Contrast — validated call sites (validate entry points):** [2](#0-1) [3](#0-2) 

**`non_reverting_select_execute_entry_point_func` only guards against revert, not false return:** [4](#0-3) 

**Attack path:**
1. Attacker submits any V3 transaction (invoke, declare, or deploy_account) with `max_fee > 0`.
2. OS calls `charge_fee`, which constructs a `transfer` call to the fee token contract.
3. The fee token's `transfer` executes and returns `false` (non-reverting failure).
4. `non_reverting_select_execute_entry_point_func` asserts `is_reverted = 0` — passes, because the call did not revert.
5. `charge_fee` returns without inspecting `retdata`.
6. The transaction is committed to state; the sequencer receives no fee. [5](#0-4)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L804-812)
```text
        let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(
            block_context=block_context, execution_context=validate_declare_execution_context
        );
    }
    // TODO(Yoni): calculate the gas consumed and use it to charge fee (for all transactions).
    if (is_deprecated == 0) {
        assert retdata_size = 1;
        assert retdata[0] = VALIDATED;
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L191-196)
```text
    let revert_log = init_revert_log();
    let (is_reverted, retdata_size, retdata, is_deprecated) = select_execute_entry_point_func{
        revert_log=revert_log
    }(block_context=block_context, execution_context=execution_context);
    assert is_reverted = 0;
    return (retdata_size, retdata, is_deprecated);
```
