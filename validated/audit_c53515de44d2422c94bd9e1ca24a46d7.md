### Title
Unchecked Fee Transfer Return Value in `charge_fee` Allows Silent Fee Collection Failure — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

The `charge_fee` function in the StarkNet OS calls `non_reverting_select_execute_entry_point_func` to execute the ERC20 fee token `transfer`, but **completely discards the return value**. If the fee transfer fails for any reason (revert or false return), the OS silently proceeds and includes the transaction in the proven block without collecting the fee. This is a direct analog to the "safeTransfer() not used" vulnerability class: an unsafe call whose failure is never checked.

---

### Finding Description

In `transaction_impls.cairo`, `charge_fee` constructs an `ExecutionContext` targeting the fee token contract's `TRANSFER_ENTRY_POINT_SELECTOR` and calls:

```cairo
non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
    block_context=block_context, execution_context=&execution_context
);
return ();
``` [1](#0-0) 

The function `non_reverting_select_execute_entry_point_func` returns `(retdata_size, retdata, is_deprecated)`. Crucially, the ERC20 `transfer` entry point returns a boolean success flag in `retdata[0]`. In `charge_fee`, **all return values are discarded** — neither `retdata[0]` (the boolean success) nor any revert indicator is checked.

Contrast this with how the OS handles the `__validate__` and `__validate_deploy__` entry points, where the return value is explicitly verified:

```cairo
let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(
    block_context=block_context, execution_context=validate_deploy_execution_context
);
if (is_deprecated == 0) {
    assert retdata_size = 1;
    assert retdata[0] = VALIDATED;
}
``` [2](#0-1) 

No equivalent check exists for the fee transfer. The `non_reverting_select_execute_entry_point_func` is specifically designed to swallow reverts from the called contract, meaning even a hard revert from the fee token (e.g., due to insufficient balance) is silently absorbed.

The fee transfer calldata is constructed as:

```cairo
local calldata: TransferCallData = TransferCallData(
    recipient=block_context.block_info_for_execute.sequencer_address,
    amount=Uint256(low=low_actual_fee, high=0),
);
``` [3](#0-2) 

The OS verifies the charged amount does not exceed `max_fee`, but never verifies the transfer actually succeeded. [4](#0-3) 

---

### Impact Explanation

The StarkNet OS is the Cairo program whose execution trace is proven on L1. If the OS does not enforce that the fee transfer succeeded, the STARK proof does not guarantee fee collection. A block can be proven and accepted on L1 where transactions were executed but no fees were transferred to the sequencer. This constitutes **direct loss of funds**: the sequencer/protocol is owed fees for execution but the proven state transition does not reflect their collection.

---

### Likelihood Explanation

A transaction sender can craft a transaction whose execution body drains their own fee token balance (e.g., by calling `transfer` on the fee token contract to send all tokens elsewhere). The sequencer validates balance at submission time; the OS executes the transaction body first, then calls `charge_fee`. At that point the balance is zero, the fee token `transfer` reverts, `non_reverting_select_execute_entry_point_func` swallows the revert, and the OS proceeds. The resulting proof is valid from the verifier's perspective. No privileged access is required — only a standard invoke transaction.

---

### Recommendation

After calling `non_reverting_select_execute_entry_point_func` in `charge_fee`, check the returned `retdata`:

```cairo
let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func{
    remaining_gas=remaining_gas
}(block_context=block_context, execution_context=&execution_context);
if (is_deprecated == 0) {
    // ERC20 transfer must return (true,) — i.e., retdata[0] == 1.
    assert retdata_size = 1;
    assert retdata[0] = 1;
}
```

This mirrors the pattern already used for `__validate__` and `__validate_deploy__` return value checks, and ensures the proof cryptographically commits to successful fee collection.

---

### Proof of Concept

1. Deploy an account contract whose `__execute__` body calls `transfer` on the fee token, sending the entire balance to an attacker-controlled address.
2. Submit an invoke transaction from this account with a non-zero `max_fee` / resource bounds.
3. The sequencer validates sufficient balance at submission time and includes the transaction.
4. The OS executes `__execute__`, draining the fee token balance.
5. The OS calls `charge_fee` → `non_reverting_select_execute_entry_point_func` → fee token `transfer` → reverts (zero balance) → revert swallowed → `charge_fee` returns normally.
6. The OS produces a valid proof for a block in which the transaction executed but no fee was collected.
7. The L1 verifier accepts the proof; the sequencer receives no fee. [5](#0-4)

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
