### Title
Fee Transfer Return Value Not Checked in `charge_fee` — (File: `execution/transaction_impls.cairo`)

---

### Summary

The `charge_fee` function in `transaction_impls.cairo` invokes the ERC20 fee token's `transfer` entry point via `non_reverting_select_execute_entry_point_func` but **completely discards the return value**. If the fee token's `transfer` returns `false` (a valid non-reverting ERC20 failure mode) rather than reverting, the OS silently continues, the sequencer receives no fee, and the transaction is still included in the block — a direct loss of funds for the sequencer.

---

### Finding Description

In `charge_fee`, the OS constructs an `ExecutionContext` targeting the fee token's `transfer` selector and calls:

```cairo
let remaining_gas = DEFAULT_INITIAL_GAS_COST;
non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
    block_context=block_context, execution_context=&execution_context
);
return ();
``` [1](#0-0) 

The three return values `(retdata_size, retdata, is_deprecated)` from `non_reverting_select_execute_entry_point_func` are **not captured at all**. The ERC20 `transfer` function returns a boolean success value as its first return datum. If the transfer returns `false` without reverting, the OS has no mechanism to detect this.

`non_reverting_select_execute_entry_point_func` only asserts `is_reverted = 0`:

```cairo
assert is_reverted = 0;
return (retdata_size, retdata, is_deprecated);
``` [2](#0-1) 

This means only a **reverting** transfer is caught. A transfer that returns `false` without reverting passes through silently.

**Contrast with how other entry points are handled.** Both `run_validate` and `execute_deploy_account_transaction` capture the return data and assert the magic `VALIDATED` constant:

```cairo
let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(...);
if (is_deprecated == 0) {
    assert retdata_size = 1;
    assert retdata[0] = VALIDATED;
}
``` [3](#0-2) 

No equivalent check exists for the fee transfer return value in `charge_fee`.

---

### Impact Explanation

**Critical — Direct loss of funds.**

If the fee token's `transfer` returns `false` (insufficient balance or any other non-reverting failure), the sequencer processes the transaction and commits the state transition without receiving the fee. Every transaction in a block could be processed for free under this condition. The sequencer's revenue stream is silently drained with no on-chain evidence of the failure, since the OS proof would still be valid — it never asserted the transfer succeeded.

---

### Likelihood Explanation

**Medium.** The fee token address is fixed in `StarknetOsConfig`: [4](#0-3) 

Standard OpenZeppelin ERC20 implementations revert on failure rather than returning `false`. However:
- The OS provides **zero defense-in-depth** against a fee token that returns `false`.
- A future fee token upgrade, a non-standard implementation, or a bug in the fee token contract could trigger this silently.
- Any unprivileged transaction sender whose account has insufficient fee token balance, combined with a fee token that returns `false` on underflow, is a reachable trigger path requiring no privileged access.

---

### Recommendation

After calling `non_reverting_select_execute_entry_point_func` in `charge_fee`, capture the return data and assert the transfer succeeded:

```cairo
let remaining_gas = DEFAULT_INITIAL_GAS_COST;
let (retdata_size, retdata, is_deprecated) =
    non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
        block_context=block_context, execution_context=&execution_context
    );
// Assert the ERC20 transfer returned true (success).
if (is_deprecated == 0) {
    assert retdata_size = 1;
    assert retdata[0] = 1;  // ERC20 transfer must return true.
}
```

This mirrors the pattern already used for `__validate__` and `__validate_declare__` return value checks.

---

### Proof of Concept

1. The fee token contract at `fee_token_address` is configured with a `transfer` implementation that returns `(false,)` (i.e., `retdata = [0]`) instead of reverting when the sender's balance is insufficient.
2. An unprivileged user submits a V3 invoke transaction with non-zero resource bounds (so `max_fee != 0`) but with zero fee token balance.
3. The OS executes the transaction, then calls `charge_fee`.
4. `charge_fee` dispatches `non_reverting_select_execute_entry_point_func` targeting `TRANSFER_ENTRY_POINT_SELECTOR`.
5. The fee token's `transfer` executes, finds insufficient balance, and returns `[0]` without reverting — so `is_reverted = 0` and `assert is_reverted = 0` passes.
6. `charge_fee` discards `(retdata_size=1, retdata=[0], is_deprecated=0)` and returns normally.
7. The transaction is finalized in the block. The sequencer's balance is unchanged. The user paid no fee. [5](#0-4)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L195-196)
```text
    assert is_reverted = 0;
    return (retdata_size, retdata, is_deprecated);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os_config/os_config.cairo (L17-18)
```text
    // The (L2) address of the fee token contract.
    fee_token_address: felt,
```
