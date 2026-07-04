### Title
Unchecked ERC20 Transfer Return Value in `charge_fee` Allows Silent Fee Bypass — (File: `execution/transaction_impls.cairo`)

---

### Summary

`charge_fee` in `transaction_impls.cairo` executes the ERC20 fee token's `transfer` function via `non_reverting_select_execute_entry_point_func` but **completely discards the return value**. If the fee token returns `false` (boolean failure) without reverting, the OS proceeds as if the fee was successfully charged. The sequencer receives no payment while the transaction's state changes are committed.

---

### Finding Description

In `charge_fee`, lines 160–164:

```cairo
let remaining_gas = DEFAULT_INITIAL_GAS_COST;
non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
    block_context=block_context, execution_context=&execution_context
);
return ();
``` [1](#0-0) 

The function signature of `non_reverting_select_execute_entry_point_func` returns `(retdata_size: felt, retdata: felt*, is_deprecated: felt)`: [2](#0-1) 

All three return values are silently dropped. The ERC20 `transfer` entry point returns a boolean success value in `retdata[0]`. If the fee token returns `false` without reverting (a valid ERC20 behavior), the OS has no constraint that catches this — the proof remains valid, the transaction is finalized, and the sequencer receives nothing.

**Contrast with `run_validate`**, which calls the same helper but explicitly constrains the return value:

```cairo
let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(...);
if (is_deprecated == 0) {
    assert retdata_size = 1;
    assert retdata[0] = VALIDATED;
}
``` [3](#0-2) 

The `charge_fee` path has no equivalent assertion. `non_reverting_select_execute_entry_point_func` only asserts `is_reverted = 0` (i.e., the call did not panic): [4](#0-3) 

A `false` return without a revert passes that check and is otherwise unconstrained.

---

### Impact Explanation

**Critical — Direct loss of funds.**

If the fee token's `transfer` returns `false` without reverting:
- The transaction executes fully; all state changes are committed to the OS output.
- The sequencer's address receives zero tokens.
- The block proof is valid and accepted by the verifier.

At scale, an attacker who can trigger this condition executes arbitrary transactions at zero cost, draining sequencer revenue and enabling unbounded free state mutation.

---

### Likelihood Explanation

**Low-to-Medium.** The current canonical STRK fee token panics on insufficient balance, so the bug is not immediately exploitable against the deployed token. However:

1. The fee token address is a protocol-level configuration (`block_context.os_global_context.starknet_os_config.fee_token_address`). [5](#0-4) 
2. The fee token's class hash is read live from `contract_state_changes` at execution time. [6](#0-5) 
3. If the fee token is ever upgraded (via `replace_class`) to a version that returns `false` instead of panicking — whether through a governance action, a bug in a new implementation, or a supply-chain compromise — every transaction in every block becomes fee-free from the sequencer's perspective.
4. The OS itself provides no backstop: the missing `assert retdata[0] = 1` is a protocol-level gap, not a runtime check that can be patched without an OS upgrade.

---

### Recommendation

Capture and constrain the return value of the fee transfer, mirroring the pattern used in `run_validate`:

```cairo
let remaining_gas = DEFAULT_INITIAL_GAS_COST;
let (retdata_size, retdata, is_deprecated) =
    non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
        block_context=block_context, execution_context=&execution_context
    );
// ERC20 transfer must return true.
if (is_deprecated == 0) {
    assert retdata_size = 1;
    assert retdata[0] = 1;  // TRUE
}
```

---

### Proof of Concept

1. The fee token contract is upgraded (or initially deployed) with a `transfer` implementation that returns `Span([0])` (i.e., `false`) instead of panicking when the sender's balance is insufficient.
2. A user submits an invoke transaction with `max_l2_gas_amount > 0` and `max_price_per_unit > 0`, making `compute_max_possible_fee` return a non-zero value, so `charge_fee` is entered. [7](#0-6) 
3. The sequencer's hint `LoadActualFee` sets `low_actual_fee` to any value ≤ `max_fee`; `assert_nn_le` passes. [8](#0-7) 
4. `non_reverting_select_execute_entry_point_func` executes the fee token's `transfer`. The token returns `false` without reverting; `assert is_reverted = 0` passes.
5. The return tuple `(retdata_size, retdata, is_deprecated)` is dropped with no constraint. The OS returns from `charge_fee` normally.
6. The block proof is generated and verified. The sequencer's balance in the fee token is unchanged; the user's transaction is finalized at zero cost.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L121-125)
```text
    let max_fee = compute_max_possible_fee(tx_info=tx_info);

    if (max_fee == 0) {
        return ();
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L127-135)
```text
    local low_actual_fee;
    %{ LoadActualFee %}
    local calldata: TransferCallData = TransferCallData(
        recipient=block_context.block_info_for_execute.sequencer_address,
        amount=Uint256(low=low_actual_fee, high=0),
    );

    // Verify that the charged amount is not larger than the transaction's max_fee field.
    assert_nn_le(calldata.amount.low, max_fee);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L138-141)
```text
    local fee_token_address = block_context.os_global_context.starknet_os_config.fee_token_address;
    let (fee_state_entry: StateEntry*) = dict_read{dict_ptr=contract_state_changes}(
        key=fee_token_address
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L160-164)
```text
    let remaining_gas = DEFAULT_INITIAL_GAS_COST;
    non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
        block_context=block_context, execution_context=&execution_context
    );
    return ();
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
