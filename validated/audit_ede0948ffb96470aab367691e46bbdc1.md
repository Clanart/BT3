### Title
Unchecked ERC20 Transfer Return Value in `charge_fee` Silently Allows Fee-Free Transaction Execution — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`charge_fee` invokes the fee token's ERC20 `transfer` entry point via `non_reverting_select_execute_entry_point_func` but **completely discards the return value**. For deprecated (Cairo 0) fee token contracts, a failed transfer returns `false` (0) without reverting. Because `non_reverting_select_execute_entry_point_func` only asserts `is_reverted = 0` — not that the transfer succeeded — the OS generates a valid proof for a block in which the fee was never actually paid.

---

### Finding Description

In `charge_fee`, the ERC20 `transfer` call is dispatched as:

```cairo
let remaining_gas = DEFAULT_INITIAL_GAS_COST;
non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
    block_context=block_context, execution_context=&execution_context
);
return ();
``` [1](#0-0) 

The function signature of `non_reverting_select_execute_entry_point_func` returns `(retdata_size: felt, retdata: felt*, is_deprecated: felt)`:

```cairo
func non_reverting_select_execute_entry_point_func{...}(...) -> (
    retdata_size: felt, retdata: felt*, is_deprecated: felt
) {
    ...
    assert is_reverted = 0;
    return (retdata_size, retdata, is_deprecated);
}
``` [2](#0-1) 

The `assert is_reverted = 0` only guarantees the entry point did not **revert**. It does **not** check the boolean return value of the ERC20 `transfer` function. For deprecated (Cairo 0) ERC20 contracts, `transfer` returns `(success: felt)` where `success = 0` signals failure — without reverting. In that case `is_reverted = 0` and `retdata[0] = 0`, and `non_reverting_select_execute_entry_point_func` returns successfully. The caller `charge_fee` discards all return values and returns, treating the failed transfer as a success.

The deprecated execution path explicitly supports this: `deprecated_execute_entry_point` returns `(is_reverted=0, retdata_size, retdata)` even when the contract function returns a falsy value. [3](#0-2) 

**Contrast with `run_validate`**, which correctly captures and asserts the return value:

```cairo
let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(...);
if (is_deprecated == 0) {
    assert retdata_size = 1;
    assert retdata[0] = VALIDATED;
}
``` [4](#0-3) 

`charge_fee` has no equivalent check on `retdata[0]`.

---

### Impact Explanation

**Critical — Direct loss of funds.**

When the fee token is a deprecated Cairo 0 ERC20 contract whose `transfer` returns `false` on insufficient balance (rather than reverting), the OS produces a valid STARK proof for a block containing transactions whose fees were never collected. The sequencer address receives nothing, yet the proof is accepted by the verifier. This constitutes a direct, provable loss of protocol fee revenue with no on-chain recourse.

---

### Likelihood Explanation

The OS explicitly supports deprecated fee token contracts via the `is_deprecated` branch in `select_execute_entry_point_func`. [5](#0-4) 

Any deployment where `fee_token_address` points to a Cairo 0 ERC20 that follows the return-false-on-failure pattern (rather than assert/revert) is directly vulnerable. The attacker-controlled input is simply submitting a transaction with a balance below the charged fee amount — a condition reachable by any unprivileged transaction sender.

---

### Recommendation

Capture and assert the return value of the ERC20 `transfer` call inside `charge_fee`, mirroring the pattern used in `run_validate`:

```cairo
let (retdata_size, retdata, is_deprecated) =
    non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
        block_context=block_context, execution_context=&execution_context
    );
// For deprecated contracts, transfer returns (success: felt).
if (is_deprecated != 0) {
    assert retdata_size = 1;
    assert retdata[0] = 1;  // TRUE — transfer must succeed
}
```

For Cairo 1 fee tokens, a failed transfer already causes a revert (caught by `assert is_reverted = 0`), so the additional check is only strictly necessary for the deprecated path, but adding it uniformly is safer.

---

### Proof of Concept

1. Fee token is configured as a deprecated Cairo 0 ERC20 contract at `block_context.os_global_context.starknet_os_config.fee_token_address`.
2. Unprivileged user submits a V3 invoke transaction with `max_amount > 0` but holds a fee token balance below the `low_actual_fee` loaded by `LoadActualFee`.
3. OS executes the transaction body, then calls `charge_fee`.
4. `charge_fee` constructs `TransferCallData` with `amount.low = low_actual_fee` and calls `non_reverting_select_execute_entry_point_func` targeting `TRANSFER_ENTRY_POINT_SELECTOR`. [6](#0-5) 

5. The deprecated ERC20 `transfer` executes, finds insufficient balance, and returns `(retdata_size=1, retdata=[0])` with `is_reverted=0`.
6. `non_reverting_select_execute_entry_point_func` asserts `is_reverted = 0` — passes — and returns `(retdata_size=1, retdata=[0], is_deprecated=1)`.
7. `charge_fee` discards all return values and returns normally.
8. The OS finalises the block and produces a valid proof. The sequencer's balance is unchanged; the fee was never transferred.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L129-135)
```text
    local calldata: TransferCallData = TransferCallData(
        recipient=block_context.block_info_for_execute.sequencer_address,
        amount=Uint256(low=low_actual_fee, high=0),
    );

    // Verify that the charged amount is not larger than the transaction's max_fee field.
    assert_nn_le(calldata.amount.low, max_fee);
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_entry_point.cairo (L111-113)
```text
}(block_context: BlockContext*, execution_context: ExecutionContext*) -> (
    is_reverted: felt, retdata_size: felt, retdata: felt*
) {
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/entry_point_utils.cairo (L36-43)
```text
    if (is_deprecated != FALSE) {
        let (is_reverted, retdata_size, retdata: felt*) = deprecated_execute_entry_point(
            block_context=block_context, execution_context=execution_context
        );
        return (
            is_reverted=is_reverted, retdata_size=retdata_size, retdata=retdata, is_deprecated=1
        );
    }
```
