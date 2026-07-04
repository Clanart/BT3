### Title
`check_is_reverted` Is a No-Op, Allowing Unconstrained Bypass of Invoke Transaction Execution — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo`)

---

### Summary

The `check_is_reverted` function in `execution_constraints.cairo` is completely empty — it contains no assertions, no range checks, and no constraints of any kind. The `is_reverted` flag used in `execute_invoke_function_transaction` is set exclusively by a prover-controlled hint (`%{ IsReverted %}`). Because `check_is_reverted` imposes zero Cairo-level constraints on this value, a malicious prover can freely set `is_reverted = 1` for any valid invoke transaction, causing the execute step to be skipped entirely while `charge_fee` is still called unconditionally. This is a state-transition bypass: a constraint that should be enforced during the execute phase is simply absent.

---

### Finding Description

In `transaction_impls.cairo`, `execute_invoke_function_transaction` loads `is_reverted` from a hint and passes it to `check_is_reverted`:

```cairo
local is_reverted;
%{ IsReverted %}
check_is_reverted(is_reverted);
if (is_reverted == FALSE) {
    // Execute only non-reverted transactions.
    with remaining_gas {
        cap_remaining_gas(max_gas=EXECUTE_MAX_SIERRA_GAS);
        non_reverting_select_execute_entry_point_func(...);
    }
} else {
    // Skip execution entirely.
    tempvar _dummy_return_value: non_reverting_select_execute_entry_point_func.Return;
}
// Charge fee regardless of is_reverted.
charge_fee(block_context=block_context, tx_execution_context=updated_tx_execution_context);
``` [1](#0-0) 

The function that is supposed to validate this value is:

```cairo
func check_is_reverted(is_reverted: felt) {
    return ();
}
``` [2](#0-1) 

This is a complete no-op. The Cairo VM only enforces that the branch taken is *consistent* with the value of `is_reverted` (i.e., if `is_reverted == 0`, the first branch is taken; otherwise the second). It does **not** enforce that `is_reverted` reflects the actual execution outcome. Since `is_reverted` is a `local` variable set entirely by a prover-controlled hint with no subsequent assertion, the prover can assign it any value.

The analog to the original report is exact: just as the dCDS `lockingPeriod` was recorded during deposit but never enforced during withdrawal, here `is_reverted` is recorded from a hint during the execute phase but `check_is_reverted` — the function whose sole purpose is to enforce it — does nothing.

Fee charging is unconditional and occurs after the `if/else` block regardless of which branch was taken: [3](#0-2) 

---

### Impact Explanation

**Critical — Direct loss of funds.**

A malicious prover can set `is_reverted = 1` for any valid invoke transaction. The execute step (`non_reverting_select_execute_entry_point_func`) is skipped — no state changes occur — but `charge_fee` still executes and deducts the fee from the user's account. The user pays for a transaction that had zero effect. Because the proof is valid from the Cairo VM's perspective (the branch taken is consistent with `is_reverted = 1`), this incorrect state transition is accepted by the verifier.

Conversely, setting `is_reverted = 0` for a transaction that should have been reverted causes the execute step to run and commits state changes that should have been discarded, enabling a second class of incorrect state transitions.

---

### Likelihood Explanation

The StarkNet sequencer is currently centralized and also acts as the prover. A malicious sequencer can exploit this on any block, for any user's invoke transaction, with no special on-chain privileges required beyond being the prover. The exploit requires only setting a single hint value and produces a proof that passes verification. There is no off-chain detection mechanism that would distinguish a legitimately reverted transaction from one that was forced into the reverted branch by the prover.

---

### Recommendation

`check_is_reverted` must be implemented to actually constrain `is_reverted`. The correct approach is to derive `is_reverted` from the execution result rather than from an independent hint, or to add a Cairo assertion that ties the hint value to a verifiable on-chain condition (e.g., the gas remaining after validation, or the actual return value of the execute entry point). The function should never be a no-op.

---

### Proof of Concept

1. User submits a valid invoke transaction (e.g., an ERC-20 transfer).
2. Malicious prover sets `is_reverted = 1` via the `%{ IsReverted %}` hint in `execute_invoke_function_transaction`.
3. `check_is_reverted(1)` is called and immediately returns — no constraint is applied.
4. The `if (is_reverted == FALSE)` branch is not taken; `non_reverting_select_execute_entry_point_func` is never called; no state changes occur.
5. `charge_fee` is called unconditionally and deducts the fee from the user's account.
6. The generated proof is valid: the Cairo VM verifies only that `is_reverted = 1` is consistent with the second branch being taken, which it is.
7. The verifier accepts the proof. The committed state shows the user's fee was deducted but the ERC-20 transfer never happened — direct loss of funds with a cryptographically valid proof.

### Citations

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo (L20-22)
```text
func check_is_reverted(is_reverted: felt) {
    return ();
}
```
