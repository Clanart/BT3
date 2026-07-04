### Title
Unchecked Return Value of Fee Transfer Call in `charge_fee` Silently Allows Fee-Free Transaction Execution — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

The `charge_fee` function in `transaction_impls.cairo` calls `non_reverting_select_execute_entry_point_func` to execute the ERC20 `transfer` entry point on the fee token contract, but **completely discards the return value**. If the fee token transfer reverts or fails for any reason, the OS silently continues, finalizing the transaction without actually collecting the fee. This is the direct Cairo/StarkNet OS analog of the Solidity "unchecked ERC20 transfer return value" vulnerability class.

---

### Finding Description

In `charge_fee`, the ERC20 transfer is executed as follows:

```cairo
let remaining_gas = DEFAULT_INITIAL_GAS_COST;
non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
    block_context=block_context, execution_context=&execution_context
);
return ();
``` [1](#0-0) 

The function `non_reverting_select_execute_entry_point_func` returns a tuple `(retdata_size, retdata, is_deprecated)`. At every other call site in the same file — specifically in `execute_deploy_account_transaction` and `execute_declare_transaction` — the return value **is** captured and the `VALIDATED` magic value is asserted:

```cairo
let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(
    block_context=block_context, execution_context=validate_deploy_execution_context
);
if (is_deprecated == 0) {
    assert retdata_size = 1;
    assert retdata[0] = VALIDATED;
}
``` [2](#0-1) [3](#0-2) 

In `charge_fee`, no such check is performed. Because the function is "non-reverting" by design (it does not propagate the callee's revert upward to the OS), a failed ERC20 transfer causes the callee's state changes to be rolled back (no tokens deducted), but the OS has no mechanism to detect this failure and halt or revert the outer transaction. The OS simply returns from `charge_fee` and the transaction is finalized as if the fee was paid.

`charge_fee` is invoked for all three account transaction types:

- `execute_invoke_function_transaction` — line 361
- `execute_deploy_account_transaction` — line 687
- `execute_declare_transaction` — line 822 [4](#0-3) [5](#0-4) [6](#0-5) 

The `charge_fee` function itself is defined at: [7](#0-6) 

---

### Impact Explanation

**Direct loss of funds (Critical).** If the fee token ERC20 `transfer` call reverts — for any reason — the sequencer receives no fee, but the transaction is still proven and finalized on-chain. The OS proof is valid even though no fee was collected. The sequencer/protocol suffers a direct, unrecoverable loss of the fee amount for every such transaction. Because the OS is the authoritative provable program, there is no off-chain mechanism that can override a valid proof.

---

### Likelihood Explanation

The fee token (`fee_token_address` from `StarknetOsConfig`) is STRK or ETH, which are well-audited. However:

1. The OS is supposed to be **trustless and self-contained** — it must not rely on the fee token being well-behaved. Any future upgrade to the fee token, or a bug in its Sierra class, could cause transfers to fail silently.
2. A user whose account balance is exactly zero (or whose balance is drained between validation and execution via a reentrancy-like cross-transaction race) could trigger a failed transfer.
3. The `assert_nn_le(calldata.amount.low, max_fee)` check at line 135 only verifies the charged amount does not exceed `max_fee`; it does not verify the user actually holds sufficient balance at fee-collection time. [8](#0-7) 

---

### Recommendation

Capture and assert the return value of `non_reverting_select_execute_entry_point_func` inside `charge_fee`, analogous to how validate entry points are handled. Specifically, assert that the call did not revert and that the returned data indicates success (e.g., `retdata[0] == TRUE` for an ERC20 transfer). If the transfer fails, the OS should treat the transaction as failed rather than silently proceeding.

---

### Proof of Concept

1. Attacker submits an invoke transaction with valid signature and nonce, but arranges for the fee token `transfer` to revert at execution time (e.g., by draining the account balance via a prior transaction in the same block, or by exploiting a bug in the fee token contract).
2. The OS executes the transaction body successfully.
3. `charge_fee` is called. `non_reverting_select_execute_entry_point_func` executes the ERC20 `transfer`, which reverts internally. The revert log rolls back the token deduction. The return value `(retdata_size, retdata, is_deprecated)` is discarded.
4. `charge_fee` returns normally. The OS finalizes the transaction.
5. A valid STARK proof is generated for a block in which the attacker's transaction was executed without paying any fee. The sequencer has no recourse.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L821-824)
```text
    // Charge fee.
    charge_fee(
        block_context=block_context, tx_execution_context=validate_declare_execution_context
    );
```
