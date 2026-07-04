### Title
Unchecked ERC20 Transfer Return Value in `charge_fee` Allows Silent Fee Payment Failure — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

The `charge_fee` function in the StarkNet OS Cairo program calls `non_reverting_select_execute_entry_point_func` to execute the fee token's ERC20 `transfer` entry point, but **completely discards the return value**. If the fee token's `transfer` function returns `false` (failure) without reverting — which is valid per the ERC20 standard — the OS proof remains valid, the state is committed, and the sequencer receives no fee. This is a direct analog to the H04 bug class: unchecked ERC20 transfer return values leading to silent accounting failures.

---

### Finding Description

In `transaction_impls.cairo`, the `charge_fee` function (lines 111–165) is responsible for deducting fees from the transaction sender by invoking the fee token contract's `transfer` entry point:

```cairo
let remaining_gas = DEFAULT_INITIAL_GAS_COST;
non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
    block_context=block_context, execution_context=&execution_context
);
return ();
``` [1](#0-0) 

The function `non_reverting_select_execute_entry_point_func` has the return signature:

```cairo
) -> (retdata_size: felt, retdata: felt*, is_deprecated: felt)
``` [2](#0-1) 

The ERC20 `transfer` entry point returns a boolean success value in `retdata[0]`. The `charge_fee` call site captures **none** of these return values. The ERC20 standard only mandates a boolean return value; tokens that return `false` on failure (rather than reverting) will cause the OS to silently proceed as if the fee was paid.

**Contrast with how `__validate__` and `__validate_deploy__` return values are checked:**

```cairo
let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(
    block_context=block_context, execution_context=validate_execution_context
);
if (is_deprecated == 0) {
    assert retdata_size = 1;
    assert retdata[0] = VALIDATED;
}
``` [3](#0-2) 

The OS enforces the `VALIDATED` magic value for `__validate__` but applies no equivalent check on the fee transfer's success boolean. This is a structural inconsistency in the OS's handling of entry point return values.

The `non_reverting_select_execute_entry_point_func` wrapper only asserts `is_reverted = 0` (i.e., the entry point did not panic/revert): [4](#0-3) 

A fee token that returns `false` without reverting satisfies `is_reverted = 0`, passes through this wrapper, and the failure is never surfaced to `charge_fee`.

The `charge_fee` function is invoked for all three account transaction types:
- `execute_invoke_function_transaction` (line 361)
- `execute_deploy_account_transaction` (line 687)
- `execute_declare_transaction` (line 822) [5](#0-4) [6](#0-5) [7](#0-6) 

---

### Impact Explanation

**Impact: Critical — Direct loss of funds.**

If the fee token's `transfer` returns `false` without reverting:

1. The OS Cairo proof is still valid — no assertion fails.
2. The state transition is committed on-chain with the fee token balances unchanged (sender's balance not reduced, sequencer's balance not increased).
3. The sequencer receives zero fee for processing the transaction.
4. The user's transaction is fully executed and its state changes are permanently committed.

This constitutes a direct, permanent loss of fee revenue for the sequencer/protocol, and allows users to obtain transaction execution for free. At scale, this breaks the economic security model of the network.

---

### Likelihood Explanation

**Likelihood: Medium.**

The StarkNet fee token (STRK/ETH) is a protocol-controlled contract. The current OpenZeppelin-based ERC20 implementation reverts on transfer failure rather than returning `false`. However:

- The ERC20 standard explicitly permits returning `false` on failure; the OS makes no architectural guarantee that the fee token will always revert.
- The fee token address is configurable via `block_context.os_global_context.starknet_os_config.fee_token_address`. [8](#0-7) 

- If the fee token contract is ever upgraded to a non-reverting implementation (e.g., a new version that returns `false` on insufficient balance), this vulnerability becomes immediately exploitable by any unprivileged transaction sender who has insufficient fee token balance.
- The OS itself provides no defense-in-depth against this class of failure.

---

### Recommendation

In `charge_fee`, capture and validate the return value of the fee token transfer, analogous to how `__validate__` return values are checked. For non-deprecated fee tokens, assert that exactly one return value is present and that it equals `TRUE` (1):

```cairo
let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func{
    remaining_gas=remaining_gas
}(block_context=block_context, execution_context=&execution_context);

if (is_deprecated == 0) {
    assert retdata_size = 1;
    // Ensure the ERC20 transfer returned true (success).
    assert retdata[0] = 1;
}
```

This mirrors the pattern already used for `__validate__` and `__validate_deploy__` return value checks, and ensures that a fee token returning `false` causes the OS proof to fail rather than silently proceeding.

---

### Proof of Concept

1. Deploy a fee token contract on StarkNet whose `transfer` function returns `(0,)` (false) instead of reverting when the sender has insufficient balance.
2. Submit an invoke transaction from an account with zero fee token balance, with `max_l1_gas_amount > 0` (so `max_fee != 0` and `charge_fee` is entered).
3. The OS executes `charge_fee`:
   - `assert_nn_le(actual_fee, max_fee)` passes (actual_fee is set by the prover hint).
   - `non_reverting_select_execute_entry_point_func` calls the fee token's `transfer`.
   - The fee token returns `(0,)` — failure — without reverting.
   - `is_reverted = 0` (no revert), so `non_reverting_select_execute_entry_point_func` returns normally.
   - `charge_fee` discards the return value and returns.
4. The OS proof is generated and verified successfully.
5. The block is committed: the user's transaction state changes are applied, but the fee token balances are unchanged — the sequencer received no fee. [9](#0-8)

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
