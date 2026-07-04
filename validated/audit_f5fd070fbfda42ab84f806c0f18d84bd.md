### Title
Unchecked Return Value of Fee Transfer Execution in `charge_fee` — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

The `charge_fee` function in `transaction_impls.cairo` calls `non_reverting_select_execute_entry_point_func` to execute the ERC20 fee token `transfer` entry point, but **completely discards the return value**. If the fee transfer fails (e.g., because the user drained their fee token balance during transaction execution), the OS does not detect the failure and the transaction is still committed to the block without fee payment. This is the direct StarkNet OS analog of the ERC20 `transfer()` unchecked return value vulnerability class.

---

### Finding Description

In `charge_fee` (`transaction_impls.cairo`, lines 160–163), the OS executes the fee token's `transfer` entry point via `non_reverting_select_execute_entry_point_func`:

```cairo
let remaining_gas = DEFAULT_INITIAL_GAS_COST;
non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
    block_context=block_context, execution_context=&execution_context
);
return ();
```

The function `non_reverting_select_execute_entry_point_func` returns a tuple `(retdata_size, retdata, is_deprecated)`. This is confirmed by its usage elsewhere in the same file — for example, in `execute_deploy_account_transaction` (lines 677–684) and `execute_declare_transaction` (lines 804–811), the return values are captured and validated:

```cairo
let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(
    block_context=block_context, execution_context=validate_deploy_execution_context
);
if (is_deprecated == 0) {
    assert retdata_size = 1;
    assert retdata[0] = VALIDATED;
}
```

In `charge_fee`, however, **no return values are captured and no success check is performed**. The fee transfer's success or failure is entirely ignored. Additionally, `charge_fee`'s function signature carries no `revert_log` implicit argument, meaning any partial state changes from a failed fee transfer cannot be rolled back through the standard revert mechanism.

The `charge_fee` function is called after transaction execution in all three account transaction types:
- `execute_invoke_function_transaction` (line 361)
- `execute_deploy_account_transaction` (line 687)
- `execute_declare_transaction` (line 822)

This means the execution of the user's transaction is already committed to the state before `charge_fee` is called. If the fee transfer then fails silently, the transaction's effects persist in the state with no fee collected.

---

### Impact Explanation

**Critical — Direct loss of funds.**

A user can execute a transaction that modifies state (including draining their own fee token balance) and then have the fee transfer silently fail. The OS accepts the block output as valid even though no fee was paid. The sequencer/network loses the fee revenue for that transaction. Because the OS proof is what L1 verifies, a block containing such a transaction would be accepted on L1 as well, making the fee loss permanent and provably final.

---

### Likelihood Explanation

**High.** The attack path is fully controlled by an unprivileged transaction sender:

1. The attacker submits an invoke transaction with enough fee token balance to pass the sequencer's pre-execution validation.
2. Inside the transaction's `__execute__` function, the attacker transfers all their fee token balance to a second address they control (via a `call_contract` syscall to the fee token).
3. After execution completes, `charge_fee` is called. The ERC20 `transfer` to the sequencer fails because the sender's balance is now zero.
4. The OS does not check the return value of the fee transfer call and returns normally.
5. The block is finalized and proven with the transaction included but no fee collected.

No privileged access, leaked keys, or external dependency compromise is required. The attacker only needs to be able to submit a standard invoke transaction.

---

### Recommendation

Capture and assert the return value of `non_reverting_select_execute_entry_point_func` inside `charge_fee`, analogous to how it is done in `execute_deploy_account_transaction` and `execute_declare_transaction`. At minimum, assert that the call did not revert and that the return data indicates success (e.g., `retdata[0] == TRUE` for a standard ERC20 `transfer`). If the fee transfer fails, the OS should treat the entire transaction as invalid or revert the transaction's state changes before finalizing the block output.

---

### Proof of Concept

**Root cause — unchecked return value:** [1](#0-0) 

**Contrast — return value IS checked for validate calls in the same file:** [2](#0-1) 

**`charge_fee` is called after execution is already committed, for all account tx types:** [3](#0-2) [4](#0-3) [5](#0-4) 

**`charge_fee` function signature — no `revert_log`, no return value check:** [6](#0-5)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L360-362)
```text
    // Charge fee.
    charge_fee(block_context=block_context, tx_execution_context=updated_tx_execution_context);

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L686-688)
```text
    // Charge fee.
    charge_fee(block_context=block_context, tx_execution_context=validate_deploy_execution_context);

```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L821-824)
```text
    // Charge fee.
    charge_fee(
        block_context=block_context, tx_execution_context=validate_declare_execution_context
    );
```
