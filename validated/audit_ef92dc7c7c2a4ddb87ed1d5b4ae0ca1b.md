### Title
Silent Fee Transfer Failure in `charge_fee` Allows Transactions to Be Processed Without Paying Fees — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

The `charge_fee` function in the StarkNet OS calls `non_reverting_select_execute_entry_point_func` to execute the ERC-20 fee transfer, but **completely discards the return value**. Because the function is explicitly designed to not revert the Cairo VM on failure (it handles reverts internally), a failed fee transfer produces no observable effect on the OS execution path. The OS proceeds as if the fee was successfully charged, committing the transaction's state changes while the user's fee token balance remains untouched.

---

### Finding Description

In `charge_fee`, the ERC-20 `transfer` call is dispatched via `non_reverting_select_execute_entry_point_func`:

```cairo
// transaction_impls.cairo, lines 160-163
let remaining_gas = DEFAULT_INITIAL_GAS_COST;
non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
    block_context=block_context, execution_context=&execution_context
);
return ();
``` [1](#0-0) 

The function signature (as observed at call sites that *do* capture the return value) returns `(retdata_size: felt, retdata: felt*, is_deprecated: felt)`. When the underlying entry point fails (e.g., the ERC-20 transfer reverts due to insufficient balance), `non_reverting_select_execute_entry_point_func` internally calls `handle_revert` to undo the storage changes of the failed transfer, then returns normally. The caller `charge_fee` receives no signal of failure and returns normally itself.

Contrast this with the `execute_declare_transaction` path, where the same function's return value **is** captured and validated:

```cairo
// transaction_impls.cairo, lines 804-812
let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(
    block_context=block_context, execution_context=validate_declare_execution_context
);
if (is_deprecated == 0) {
    assert retdata_size = 1;
    assert retdata[0] = VALIDATED;
}
``` [2](#0-1) 

The inconsistency is clear: validation return values are checked; fee transfer return values are not.

The `charge_fee` function is invoked at the end of every account transaction type — invoke, deploy-account, and declare: [3](#0-2) [4](#0-3) [5](#0-4) 

---

### Impact Explanation

When the fee transfer silently fails:

1. The transaction's primary execution state changes are **committed** to the OS state update.
2. The fee token storage changes from the failed transfer are **reverted** internally by `handle_revert`.
3. The user's fee token balance is **not reduced**.
4. The sequencer **does not receive** the fee.
5. The OS generates a valid proof for this state transition, which L1 accepts.

The result is that a transaction is provably executed and its effects committed on-chain without the user paying any fee. This constitutes **direct loss of funds** for the sequencer and undermines the economic security of the protocol.

---

### Likelihood Explanation

A realistic unprivileged attack path exists via intra-block balance drainage:

1. Attacker submits transaction **A** (invoke) with non-zero L2 gas resource bounds. The sequencer validates fee payment off-chain at inclusion time — it passes.
2. In the same block, the attacker also submits transaction **B** (e.g., a transfer of all fee tokens to another address), ordered before **A**.
3. When the OS executes block transactions in order: **B** drains the fee token balance, then **A** executes its main logic successfully, then `charge_fee` for **A** attempts the ERC-20 transfer — which fails due to zero balance.
4. The failure is silent. The OS proof is valid. L1 accepts it.

No malicious sequencer is required; an honest sequencer ordering two valid transactions from the same sender in the same block is sufficient.

---

### Recommendation

Capture and assert the return value of `non_reverting_select_execute_entry_point_func` inside `charge_fee`, analogous to how it is handled in `execute_declare_transaction`. Specifically, verify that `retdata_size == 1` and `retdata[0] == TRANSFER_SUCCESS_SELECTOR` (or equivalent success sentinel), and treat any other outcome as a fatal OS error that halts proof generation for the block.

---

### Proof of Concept

**Step 1.** Attacker holds address `A` with fee token balance = 100 units.

**Step 2.** Attacker submits two transactions in the same block:
- Tx1 (ordered first): transfer 100 fee tokens from `A` to `A2` (drains balance).
- Tx2 (ordered second): invoke any contract function, with resource bounds specifying a fee of 50 units.

**Step 3.** The sequencer validates both transactions at inclusion time. At that moment, `A` has 100 tokens, so both pass the off-chain fee check.

**Step 4.** The OS executes Tx1 — fee token balance of `A` drops to 0.

**Step 5.** The OS executes Tx2 — main execution succeeds. Then `charge_fee` is called:
- `non_reverting_select_execute_entry_point_func` dispatches the ERC-20 `transfer(sequencer, 50)`.
- The transfer fails (balance = 0). `handle_revert` undoes the storage write. The function returns normally.
- `charge_fee` returns normally. No assertion fires.

**Step 6.** The OS state update includes Tx2's effects but **not** the fee deduction. A valid STARK proof is generated.

**Step 7.** L1 verifies the proof and commits the state. Tx2 is executed on-chain for free. [6](#0-5)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L361-361)
```text
    charge_fee(block_context=block_context, tx_execution_context=updated_tx_execution_context);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L687-687)
```text
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L822-824)
```text
    charge_fee(
        block_context=block_context, tx_execution_context=validate_declare_execution_context
    );
```
