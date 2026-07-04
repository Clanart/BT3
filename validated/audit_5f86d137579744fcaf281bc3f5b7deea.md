### Title
Unchecked Return Value of Fee Token Transfer Allows Silent Fee Collection Failure — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

The `charge_fee` function in the StarkNet OS program calls `non_reverting_select_execute_entry_point_func` to execute the fee token's `transfer` entry point, but **completely discards the return value**. Because the function is explicitly non-reverting, a failed fee transfer (whether the inner call reverts due to insufficient balance or returns a failure indicator) is silently swallowed. The OS then continues, commits state changes, and produces a valid proof — all without the fee ever being collected. This is the direct Cairo/StarkNet OS analog of the Juicebox M-03 finding.

---

### Finding Description

In `transaction_impls.cairo`, the `charge_fee` function (lines 111–165) is responsible for deducting the transaction fee from the user's fee token balance and sending it to the sequencer. It does so by constructing an `ExecutionContext` targeting the fee token contract's `transfer` selector and invoking it via `non_reverting_select_execute_entry_point_func`:

```cairo
let remaining_gas = DEFAULT_INITIAL_GAS_COST;
non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
    block_context=block_context, execution_context=&execution_context
);
return ();
```

`non_reverting_select_execute_entry_point_func` returns a tuple `(retdata_size, retdata, is_deprecated)`. The `retdata` for a StarkNet ERC20 `transfer` call contains the boolean success value. However, `charge_fee` **does not capture or inspect this return value at all**.

Contrast this with how the same function is called for `__validate_declare__` (lines 804–811):

```cairo
let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(
    block_context=block_context, execution_context=validate_declare_execution_context
);
...
if (is_deprecated == 0) {
    assert retdata_size = 1;
    assert retdata[0] = VALIDATED;
}
```

There, the return value is captured and the `VALIDATED` magic value is asserted. The same pattern is applied to `__validate_deploy__` (lines 677–683). **No such check exists for the fee transfer.**

Because the function is named and designed as "non-reverting," it is specifically built to absorb inner-call failures without propagating a revert to the OS. This means:

- If the fee token's `transfer` call reverts internally (e.g., due to insufficient balance), `non_reverting_select_execute_entry_point_func` catches it and returns a failure indicator in `retdata`.
- If the fee token's `transfer` returns `false` (a non-reverting failure), the same applies.
- In both cases, `charge_fee` ignores the outcome and returns normally.
- The OS proceeds to finalize the block, commit state changes, and generate a valid STARK proof — all without the fee having been transferred.

---

### Impact Explanation

**Critical — Direct loss of funds.**

The fee token transfer is the sole mechanism by which the StarkNet OS enforces that users pay for transaction execution. If this transfer silently fails:

1. The sequencer/fee recipient does not receive the fee.
2. The user's transaction is fully executed and its state changes are committed to the proven state.
3. The STARK proof produced by the OS is valid and will be accepted by the L1 verifier, because the OS itself does not enforce that the fee transfer succeeded.

This breaks the protocol's economic security model at the proof level: the L1 verifier accepts state transitions where fees were never collected. An attacker can obtain free transaction execution — a direct loss of funds from the sequencer/protocol.

---

### Likelihood Explanation

**Medium.**

The standard OpenZeppelin ERC20 on StarkNet reverts on insufficient balance rather than returning `false`. However:

- The `non_reverting_select_execute_entry_point_func` is explicitly designed to absorb reverts. A revert inside the fee token transfer is therefore silently swallowed, making this exploitable with any account that has insufficient fee token balance at the time of OS execution (even if the sequencer pre-validated balance at mempool admission time, the balance could have changed).
- Any fee token contract that returns `false` on failure (a valid ERC20 pattern) would also trigger this silently.
- The vulnerability is reachable by any unprivileged transaction sender: submit a transaction, drain your fee token balance before the block is proven, and the OS will process your transaction for free.

---

### Recommendation

Capture the return value of `non_reverting_select_execute_entry_point_func` in `charge_fee` and assert that the transfer succeeded. Specifically, after the call, assert that `retdata_size == 1` and `retdata[0] == TRUE` (analogous to the `VALIDATED` check done for validate entry points). If the transfer fails, the OS should treat this as a fatal error and halt (or revert the block), not silently continue.

```cairo
// Recommended fix:
let (retdata_size, retdata, is_deprecated) =
    non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
        block_context=block_context, execution_context=&execution_context
    );
// Assert the transfer succeeded.
assert retdata_size = 1;
assert retdata[0] = 1;  // TRUE / success
```

---

### Proof of Concept

1. User submits a V3 invoke transaction with `max_l2_gas_amount > 0` (so `max_fee != 0`).
2. At mempool admission, the sequencer validates the user has sufficient fee token balance.
3. Before the block is proven, the user transfers their entire fee token balance to another address (draining it to zero).
4. The OS runs `charge_fee`. The fee token's `transfer` call reverts internally (insufficient balance).
5. `non_reverting_select_execute_entry_point_func` absorbs the revert and returns `retdata` indicating failure.
6. `charge_fee` ignores the return value and returns normally (line 164: `return ();`).
7. The OS finalizes the block with the user's transaction fully executed and state changes committed.
8. A valid STARK proof is generated and submitted to L1.
9. L1 accepts the proof. The user's transaction was executed for free; the sequencer received no fee.

---

**Root cause references:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
