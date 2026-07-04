### Title
Unchecked Return Value of Fee Token Transfer in `charge_fee` — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

The `charge_fee` function in the StarkNet OS executes an ERC20 `transfer` call on the fee token contract but completely discards the return value. If the fee token's `transfer` entry point returns `false` (indicating failure) without reverting, the OS proceeds as if the fee was successfully charged, allowing transactions to be processed without the sequencer receiving payment.

---

### Finding Description

In `transaction_impls.cairo`, the `charge_fee` function is responsible for deducting fees from the transaction sender by calling the fee token contract's `transfer` entry point: [1](#0-0) 

```cairo
let remaining_gas = DEFAULT_INITIAL_GAS_COST;
non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
    block_context=block_context, execution_context=&execution_context
);
return ();
```

The function `non_reverting_select_execute_entry_point_func` returns `(retdata_size: felt, retdata: felt*, is_deprecated: felt)`: [2](#0-1) 

For a Cairo 1 ERC20 token, `transfer` returns a `bool` in `retdata[0]`. The `non_reverting_select_execute_entry_point_func` wrapper only asserts `is_reverted = 0` (i.e., the call did not panic/revert): [3](#0-2) 

It does **not** inspect `retdata[0]`. The entire return tuple is silently discarded at the `charge_fee` call site. This is structurally identical to the reported vulnerability: a token transfer is executed without checking whether it actually succeeded.

The execution context is constructed to call `TRANSFER_ENTRY_POINT_SELECTOR` on `fee_token_address`: [4](#0-3) 

---

### Impact Explanation

If the fee token contract's `transfer` entry point returns `false` (a valid ERC20 non-reverting failure) rather than panicking, the OS does not detect the failure. The transaction is fully processed and committed to the block — including state changes and L2 outputs — while the sequencer receives no fee. This constitutes **direct loss of funds**: the sequencer bears the cost of executing and proving the transaction without compensation. At scale, an attacker who can trigger this condition repeatedly drains sequencer revenue.

---

### Likelihood Explanation

The current StarkNet fee token (STRK, OpenZeppelin Cairo 1 ERC20) panics on insufficient balance, so `is_reverted = 1` would be caught by `non_reverting_select_execute_entry_point_func`. However:

1. The OS code places **no enforcement** that the fee token must revert rather than return `false`. The `fee_token_address` is read from `starknet_os_config` and trusted blindly.
2. Any future upgrade or replacement of the fee token contract that follows the ERC20 spec (returning `false` on failure instead of reverting) would immediately expose this path.
3. The structural absence of a return-value check is a latent vulnerability that becomes exploitable the moment the fee token's behavior diverges from the current implementation.

The entry path is fully unprivileged: any transaction sender triggers `charge_fee` on every invoke, deploy-account, or declare transaction.

---

### Recommendation

After calling `non_reverting_select_execute_entry_point_func` in `charge_fee`, assert that the transfer returned a truthy value:

```cairo
let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func{
    remaining_gas=remaining_gas
}(block_context=block_context, execution_context=&execution_context);
// For non-deprecated (Cairo 1) fee tokens, verify the boolean return value.
if (is_deprecated == 0) {
    assert retdata_size = 1;
    assert retdata[0] = 1;  // transfer must return true
}
```

This mirrors the pattern already applied to `__validate__` and `__validate_declare__` return values elsewhere in the same file: [5](#0-4) 

---

### Proof of Concept

1. Deploy a custom ERC20 contract as the fee token whose `transfer` function always returns `false` (without panicking) when the caller has insufficient balance.
2. Submit an invoke transaction from an account with zero STRK balance. The OS calls `charge_fee`, which calls `transfer` on the fee token.
3. `transfer` returns `(false,)` — `retdata[0] = 0` — without reverting. `is_reverted = 0`, so `non_reverting_select_execute_entry_point_func` passes.
4. `charge_fee` discards the return value and returns normally.
5. The transaction is committed to the block. The sequencer receives no fee. The attacker's transaction executes for free.

The root cause is at: [1](#0-0)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L145-164)
```text
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L681-684)
```text
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
