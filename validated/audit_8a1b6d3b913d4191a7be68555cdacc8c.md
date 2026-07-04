### Title
Unchecked Return Value from Fee Token `transfer` Call in `charge_fee` — (File: `execution/transaction_impls.cairo`)

---

### Summary
The `charge_fee` function in `transaction_impls.cairo` invokes the fee token's `transfer` entry point via `non_reverting_select_execute_entry_point_func` but silently discards all return data. If the fee token's `transfer` implementation returns a failure indicator (`0`/`false`) without reverting — a valid ERC20-style behavior — the OS cannot detect the failure and proceeds as though the fee was successfully collected. Every transaction type (invoke, deploy_account, declare) routes through this function, making the impact protocol-wide.

---

### Finding Description

`charge_fee` in `transaction_impls.cairo` (lines 160–163) calls:

```cairo
let remaining_gas = DEFAULT_INITIAL_GAS_COST;
non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
    block_context=block_context, execution_context=&execution_context
);
return ();
```

`non_reverting_select_execute_entry_point_func` (defined in `execute_transaction_utils.cairo`, lines 181–196) has the return signature:

```cairo
) -> (retdata_size: felt, retdata: felt*, is_deprecated: felt)
```

The tuple `(retdata_size, retdata, is_deprecated)` is the raw return data from the fee token's `transfer` entry point. In StarkNet's ERC20 convention, `retdata[0]` carries the boolean success flag (`1` = success, `0` = failure). The call site in `charge_fee` captures none of these values — they are thrown away.

**Contrast with every other call site in the same file:**

`run_validate` (`execute_transaction_utils.cairo`, lines 149–156):
```cairo
let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(...);
if (is_deprecated == 0) {
    assert retdata_size = 1;
    assert retdata[0] = VALIDATED;
}
```

`execute_deploy_account_transaction` (`transaction_impls.cairo`, lines 677–684):
```cairo
let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(...);
if (is_deprecated == 0) {
    assert retdata_size = 1;
    assert retdata[0] = VALIDATED;
}
```

`execute_declare_transaction` (`transaction_impls.cairo`, lines 804–812):
```cairo
let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(...);
if (is_deprecated == 0) {
    assert retdata_size = 1;
    assert retdata[0] = VALIDATED;
}
```

Every other critical call site captures and asserts the return value. `charge_fee` is the sole exception, creating an asymmetry that mirrors the external report's root cause exactly: a token transfer whose return value is not validated. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

---

### Impact Explanation

**Direct loss of funds (Critical).**

If the fee token's `transfer` entry point returns `0` (failure) without reverting — a behavior that is valid under the ERC20 standard and possible under any non-reverting failure path — the OS state transition records the transaction as complete and the sequencer's address is credited nothing. Because `charge_fee` is invoked for every invoke, deploy_account, and declare transaction, a fee token with this behavior would allow all users to transact for free, permanently draining sequencer revenue. The OS proof would still be valid (no assertion fails), so the state transition would be accepted on-chain with zero fees collected. [5](#0-4) 

---

### Likelihood Explanation

The fee token address is read from `block_context.os_global_context.starknet_os_config.fee_token_address` (line 138). The current production fee tokens (ETH, STRK) revert on transfer failure, so the bug is not immediately exploitable against them. However:

1. The OS is designed to support additional fee tokens in the future (the comment at line 137 notes caching considerations, implying extensibility).
2. Any future fee token that follows the ERC20 convention of returning `false` on failure without reverting — a fully standard and common pattern — would trigger silent fee evasion for every transaction in every block.
3. An unprivileged transaction sender needs only to submit a normal transaction; the vulnerable code path is unconditionally reached for all non-zero-fee transactions. [6](#0-5) 

---

### Recommendation

Capture and assert the return value of the fee token `transfer` call in `charge_fee`, consistent with every other call site:

```cairo
let remaining_gas = DEFAULT_INITIAL_GAS_COST;
let (retdata_size, retdata, is_deprecated) =
    non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
        block_context=block_context, execution_context=&execution_context
    );
if (is_deprecated == 0) {
    assert retdata_size = 1;
    // ERC20 transfer must return true (1) to indicate success.
    assert retdata[0] = 1;
}
```

This mirrors the pattern already applied to `__validate__`, `__validate_deploy__`, and `__validate_declare__` return values throughout the same file.

---

### Proof of Concept

1. A fee token contract is deployed whose `transfer` entry point executes the transfer internally but returns `(0,)` (failure) instead of `(1,)` (success) — a valid ERC20 pattern.
2. This contract is set as `fee_token_address` in the OS config.
3. An unprivileged user submits any V3 invoke transaction with non-zero resource bounds (so `max_fee != 0`).
4. The OS executes the transaction, reaches `charge_fee`, constructs the `TransferCallData` with the actual fee amount, and calls `non_reverting_select_execute_entry_point_func`.
5. The fee token's `transfer` runs, does not revert (`is_reverted = 0` passes), but returns `retdata[0] = 0`.
6. `charge_fee` discards the return tuple and returns normally.
7. The OS proof is generated and accepted on-chain. The sequencer's balance is unchanged. The user paid no fee.
8. This repeats for every transaction in every block, constituting a permanent, provably-valid direct loss of funds. [7](#0-6) [8](#0-7)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L80-84)
```text
// Represents the calldata of an ERC20 transfer.
struct TransferCallData {
    recipient: felt,
    amount: Uint256,
}
```

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L677-684)
```text
        let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(
            block_context=block_context, execution_context=validate_deploy_execution_context
        );
    }
    if (is_deprecated == 0) {
        assert retdata_size = 1;
        assert retdata[0] = VALIDATED;
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
