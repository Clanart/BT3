### Title
Unchecked ERC20 Fee Transfer Return Value in `charge_fee` Allows Silent Fee Payment Bypass - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

The `charge_fee` function in the StarkNet OS executes an ERC20 `transfer` call via `non_reverting_select_execute_entry_point_func` but **never checks the return value** of that transfer. If the transfer fails (e.g., due to insufficient balance), the OS silently continues and marks the transaction as complete, allowing any unprivileged transaction sender to execute transactions without paying fees.

---

### Finding Description

In `transaction_impls.cairo`, the `charge_fee` function (lines 111–165) is responsible for deducting the actual fee from the user's account by calling the ERC20 fee token contract's `transfer` entry point. The call is made via `non_reverting_select_execute_entry_point_func`, which by design does **not** revert the OS execution if the inner entry point fails.

The critical flaw is that the return value of this call is entirely discarded:

```cairo
// Lines 160-164
let remaining_gas = DEFAULT_INITIAL_GAS_COST;
non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
    block_context=block_context, execution_context=&execution_context
);
return ();
```

Compare this to the `__validate__` step in the same file, where the return value **is** checked:

```cairo
let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(...)
if (is_deprecated == 0) {
    assert retdata_size = 1;
    assert retdata[0] = VALIDATED;
}
```

Additionally, the sender's fee token balance is **never verified** before the transfer is attempted. The only check performed is that `actual_fee <= max_fee` (line 135), which bounds the amount but does not confirm the sender has sufficient balance.

After `charge_fee` returns — regardless of whether the ERC20 transfer succeeded or failed — execution continues to `%{ EndTx %}`, and the transaction is committed to the block output as successfully processed.

This is the direct analog of the H03 report: just as `finalizeGrant` and `cashOutOrg` proceeded to mark state as complete without checking transfer results, `charge_fee` proceeds to finalize the transaction without confirming the fee was actually collected.

---

### Impact Explanation

**Critical — Direct loss of funds.**

A user with zero or insufficient fee token balance can submit V3 transactions with non-zero resource bounds. The OS will:
1. Execute the transaction body.
2. Attempt the fee transfer (which fails silently).
3. Mark the transaction as complete and include it in the proven block output.

The sequencer receives no fee tokens. Since this is enforced at the OS/proof level, a proven block can contain transactions for which no fees were paid. This constitutes a direct, permanent loss of sequencer revenue and breaks the economic security model of the protocol.

---

### Likelihood Explanation

**High.** Any unprivileged transaction sender can trigger this path by submitting a V3 transaction with non-zero resource bounds from an account with insufficient fee token balance. No special privileges, leaked keys, or operator collusion are required. The attacker-controlled entry path is the standard transaction submission flow.

---

### Recommendation

1. Capture and assert the return value of the ERC20 `transfer` call inside `charge_fee`, analogous to how `VALIDATED` is asserted after `__validate__`. The ERC20 `transfer` function returns a `bool`; assert it equals `TRUE`.
2. Before executing the transfer, verify that the sender's fee token balance is greater than or equal to `actual_fee` using a `balanceOf` call or a state read.
3. If the transfer fails, the OS should either revert the entire transaction (preferred) or at minimum halt block production for that block, since a block with unpaid fees is economically invalid.

---

### Proof of Concept

1. Deploy an account contract with zero STRK/ETH fee token balance.
2. Submit a V3 invoke transaction with non-zero `l2_gas` resource bounds (so `compute_max_possible_fee` returns non-zero and `charge_fee` is entered).
3. The OS calls `charge_fee`:
   - `assert_nn_le(calldata.amount.low, max_fee)` passes (actual fee ≤ max fee).
   - `non_reverting_select_execute_entry_point_func` executes the ERC20 `transfer` — which fails due to zero balance, returning `false` or reverting internally.
   - Because `non_reverting_select_execute_entry_point_func` does not propagate the failure and the return value is discarded, `charge_fee` returns normally.
4. `%{ EndTx %}` is reached; the transaction is finalized in the block.
5. The proven block output includes the transaction as successfully executed, with no fee tokens transferred to the sequencer.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L360-365)
```text
    // Charge fee.
    charge_fee(block_context=block_context, tx_execution_context=updated_tx_execution_context);

    %{ EndTx %}

    return ();
```
