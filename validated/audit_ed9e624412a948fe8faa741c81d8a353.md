### Title
Unchecked Return Value from Fee Token Transfer in `charge_fee` — (File: `execution/transaction_impls.cairo`)

### Summary
The `charge_fee` function in `transaction_impls.cairo` executes an ERC20 `transfer()` call via `non_reverting_select_execute_entry_point_func` but **completely discards the return value**. If the fee token's `transfer()` returns `false` (application-level failure) without reverting, the OS does not detect the failure and proceeds as if the fee was successfully paid. This is the direct StarkNet OS analog of the `_safeTransferFrom()` unchecked return value bug described in the external report.

---

### Finding Description

In `charge_fee` (`transaction_impls.cairo`, lines 160–164), the OS executes the fee token's `transfer()` entry point:

```cairo
let remaining_gas = DEFAULT_INITIAL_GAS_COST;
non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
    block_context=block_context, execution_context=&execution_context
);
return ();
```

The function `non_reverting_select_execute_entry_point_func` returns `(retdata_size: felt, retdata: felt*, is_deprecated: felt)`. The ERC20 `transfer()` function is expected to return a boolean success flag in `retdata[0]`. However, **all three return values are silently discarded** — the OS never inspects whether the transfer actually succeeded at the application level.

`non_reverting_select_execute_entry_point_func` (defined in `execute_transaction_utils.cairo`, lines 181–196) only asserts `is_reverted = 0`, meaning it catches the case where the entry point sets `failure_flag = 1` (a Cairo-level revert). It does **not** catch the case where the entry point returns normally but with `retdata[0] = 0` (i.e., the ERC20 `transfer()` returns `false` without reverting).

This is structurally identical to the external report's bug: `_callOptionalReturn()` returns `false` when `transferFrom()` returns `false`, but `_safeTransferFrom()` ignores that return value.

**Contrast with other callers of the same function in the same file**, which correctly validate the return data:

- `run_validate` (`execute_transaction_utils.cairo`, lines 149–156): captures `(retdata_size, retdata, is_deprecated)` and asserts `retdata[0] = VALIDATED`.
- `execute_deploy_account_transaction` (`transaction_impls.cairo`, lines 677–684): captures and asserts `retdata[0] = VALIDATED`.
- `execute_declare_transaction` (`transaction_impls.cairo`, lines 804–812): captures and asserts `retdata[0] = VALIDATED`.

`charge_fee` is the **only** critical call site that discards the return value entirely.

---

### Impact Explanation

If the fee token's `transfer()` function returns `false` (indicating failure at the application level) without reverting:

- `non_reverting_select_execute_entry_point_func` returns successfully (no Cairo-level revert occurred, `is_reverted = 0`).
- `charge_fee` discards the return data and returns normally.
- The OS proceeds to finalize the transaction as if the fee was paid.
- The sequencer's address receives **zero tokens**.
- The user's account balance is **not debited**.

This constitutes **direct loss of funds**: the sequencer/protocol does not receive the fee revenue it is owed for processing the transaction, while the user's transaction is fully executed and committed to state.

---

### Likelihood Explanation

The fee token address is read from `block_context.os_global_context.starknet_os_config.fee_token_address` (`transaction_impls.cairo`, line 138). This is a protocol-level configuration. Current fee tokens (STRK, ETH) are well-behaved and revert on failure.

However, the vulnerability is latent and becomes exploitable under two realistic conditions:

1. **Future fee token support**: The protocol explicitly states it may support additional fee tokens. A non-standard ERC20 that returns `false` on insufficient balance instead of reverting would trigger this bug.
2. **Any user with insufficient balance**: Once such a fee token is in use, any unprivileged transaction sender with a balance below the required fee can submit transactions and have them executed for free. No special privileges are required — the attacker-controlled input is simply the transaction itself.

The root cause is entirely within the OS Cairo code, not in any external dependency.

---

### Recommendation

Capture and validate the return value of `non_reverting_select_execute_entry_point_func` in `charge_fee`, consistent with how all other call sites handle it:

```cairo
let remaining_gas = DEFAULT_INITIAL_GAS_COST;
let (retdata_size, retdata, is_deprecated) =
    non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
        block_context=block_context, execution_context=&execution_context
    );
// For non-deprecated fee tokens, verify the transfer returned true.
if (is_deprecated == 0) {
    assert retdata_size = 1;
    assert retdata[0] = 1;  // ERC20 transfer must return true.
}
return ();
```

---

### Proof of Concept

1. A fee token is configured whose `transfer()` entry point returns `(retdata=[0])` (i.e., `false`) on insufficient balance instead of reverting.
2. An unprivileged user submits any transaction (invoke, declare, deploy_account) with a balance below the required fee.
3. The OS calls `charge_fee`, which constructs an `ExecutionContext` targeting the fee token's `transfer()` selector with `recipient = sequencer_address` and `amount = actual_fee`.
4. `non_reverting_select_execute_entry_point_func` executes the fee token entry point. The entry point returns normally (`failure_flag = 0`) but with `retdata = [0]` (transfer failed at application level).
5. `charge_fee` discards `(retdata_size=1, retdata=[0], is_deprecated=0)` and returns.
6. The OS finalizes the transaction, commits state changes, and emits outputs — all without the fee having been transferred.
7. The sequencer receives no fee. The user's balance is unchanged. The transaction is fully executed.

**Affected location**: `charge_fee`, lines 160–164 of `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`. [1](#0-0) 

**Contrast with correct usage** in `run_validate`: [2](#0-1) 

**`non_reverting_select_execute_entry_point_func` only guards against Cairo-level reverts, not application-level `false` returns**: [3](#0-2)

### Citations

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
