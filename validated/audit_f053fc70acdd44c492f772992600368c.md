### Title
Unverified `is_reverted` Hint Enables Sequencer to Bypass Transaction Execution While Charging Fees — (File: `execution/execution_constraints.cairo`)

---

### Summary

The production `check_is_reverted` function is a complete no-op. A sequencer can supply `is_reverted = TRUE` for any valid transaction via the hint mechanism, causing the OS to skip the `__execute__` entry point while still charging the user's fee. The resulting STARK proof is accepted by the L1 verifier, producing a provably-valid, irreversible loss of user funds.

---

### Finding Description

In `execute_invoke_function_transaction`, the `is_reverted` value is loaded from a sequencer-controlled hint and passed to `check_is_reverted`:

```cairo
// execution_constraints.cairo (production)
func check_is_reverted(is_reverted: felt) {
    return ();   // ← zero assertions, zero validation
}
``` [1](#0-0) 

That value then gates the execute step in `transaction_impls.cairo`:

```cairo
local is_reverted;
%{ IsReverted %}
check_is_reverted(is_reverted);
if (is_reverted == FALSE) {
    // Execute only non-reverted transactions.
    non_reverting_select_execute_entry_point_func(...);
} else {
    // Stack alignment only — no execution.
    ...
}
// Fee is charged unconditionally, regardless of is_reverted.
charge_fee(block_context=block_context, tx_execution_context=updated_tx_execution_context);
``` [2](#0-1) 

Because `check_is_reverted` performs no assertion, the sequencer can set `is_reverted` to any non-zero value for any transaction, skip its execution, and still collect the fee. No Cairo constraint is violated, so the proof remains valid.

The virtual OS version (`execution_constraints__virtual.cairo`) correctly enforces the opposite:

```cairo
func check_is_reverted(is_reverted: felt) {
    with_attr error_message("Reverted transactions are not supported in virtual OS mode") {
        assert is_reverted = FALSE;
    }
    return ();
}
``` [3](#0-2) 

The production path has no equivalent enforcement. The `charge_fee` function only enforces an upper bound (`assert_nn_le(calldata.amount.low, max_fee)`) — it has no lower bound and no dependency on whether execution actually occurred. [4](#0-3) 

---

### Impact Explanation

**Direct loss of funds (Critical).** A malicious sequencer can:

1. Accept a user's signed invoke transaction.
2. Set `is_reverted = TRUE` via the `IsReverted` hint.
3. Skip the `__execute__` entry point entirely.
4. Charge the full fee via `charge_fee` (which runs unconditionally).
5. Produce a valid STARK proof — no constraint is violated.
6. Have the proof accepted by the L1 verifier; the state update is finalized.

The user's ERC-20 balance is debited for the fee, but the transaction's intended state changes are never applied. Because the proof is cryptographically valid, there is no on-chain mechanism to dispute or reverse this.

---

### Likelihood Explanation

Any sequencer running the production OS can exploit this trivially by setting a single hint value. No special privileges, leaked keys, or external dependencies are required. The attacker-controlled entry path is the sequencer's hint provider, which is invoked for every invoke transaction processed by the OS.

---

### Recommendation

Implement `check_is_reverted` in the production `execution_constraints.cairo` to cryptographically verify that the transaction's execution actually reverted — for example, by checking the execution trace output or by requiring the sequencer to provide a verifiable proof of reversion that is constrained within the Cairo program. At minimum, the function must assert that `is_reverted` is consistent with the actual execution outcome recorded in the OS state, mirroring the enforcement already present in the virtual OS variant.

---

### Proof of Concept

1. User submits a valid V3 invoke transaction with non-zero resource bounds (fee > 0).
2. Sequencer sets hint `IsReverted` → `is_reverted = 1` (TRUE) for that transaction.
3. `check_is_reverted(1)` returns immediately — no assertion fires.
4. The `if (is_reverted == FALSE)` branch is not taken; `__execute__` is skipped entirely.
5. `charge_fee` is called unconditionally; the user's ERC-20 balance is reduced by `low_actual_fee`.
6. The OS generates a valid STARK proof (no constraint was violated).
7. The L1 verifier accepts the proof; the state update is finalized on L1.
8. The user has paid the fee but received no execution — funds are permanently lost with no on-chain recourse.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo (L20-22)
```text
func check_is_reverted(is_reverted: felt) {
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L338-365)
```text
    local is_reverted;
    %{ IsReverted %}
    check_is_reverted(is_reverted);
    if (is_reverted == FALSE) {
        // Execute only non-reverted transactions.
        with remaining_gas {
            cap_remaining_gas(max_gas=EXECUTE_MAX_SIERRA_GAS);
            non_reverting_select_execute_entry_point_func(
                block_context=block_context, execution_context=updated_tx_execution_context
            );
        }
    } else {
        // Align the stack with the `if` branch to avoid revoked references.
        tempvar range_check_ptr = range_check_ptr;
        tempvar remaining_gas = remaining_gas;
        tempvar builtin_ptrs = builtin_ptrs;
        tempvar contract_state_changes = contract_state_changes;
        tempvar contract_class_changes = contract_class_changes;
        tempvar outputs = outputs;
        tempvar _dummy_return_value: non_reverting_select_execute_entry_point_func.Return;
    }

    // Charge fee.
    charge_fee(block_context=block_context, tx_execution_context=updated_tx_execution_context);

    %{ EndTx %}

    return ();
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints__virtual.cairo (L7-12)
```text
func check_is_reverted(is_reverted: felt) {
    with_attr error_message("Reverted transactions are not supported in virtual OS mode") {
        assert is_reverted = FALSE;
    }
    return ();
}
```
