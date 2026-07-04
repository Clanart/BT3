### Title
`check_is_reverted` Is Called But Never Enforces Revert Status, Making It Useless — (`execution/execution_constraints.cairo`)

---

### Summary

The function `check_is_reverted` in `execution_constraints.cairo` is an empty stub. It is called in `execute_invoke_function_transaction` to validate the hint-provided `is_reverted` value before using it to gate the execute step of a transaction. Because the function body is `return ()`, the revert status is never cryptographically constrained by the Cairo proof. The hint value flows unchecked into a branch that decides whether the transaction's `__execute__` entry point runs, while fees are charged unconditionally regardless of the branch taken.

---

### Finding Description

In `execution_constraints.cairo`, the function that is supposed to validate the revert status is completely empty:

```cairo
func check_is_reverted(is_reverted: felt) {
    return ();
}
``` [1](#0-0) 

In `transaction_impls.cairo`, `execute_invoke_function_transaction` loads `is_reverted` from a prover-controlled hint, calls `check_is_reverted` (which does nothing), and then uses the unchecked value to decide whether to run the execute entry point:

```cairo
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
    ...
    tempvar _dummy_return_value: non_reverting_select_execute_entry_point_func.Return;
}

// Charge fee.
charge_fee(block_context=block_context, tx_execution_context=updated_tx_execution_context);
``` [2](#0-1) 

The fee is charged unconditionally after the branch, regardless of whether `is_reverted` is `TRUE` or `FALSE`. [3](#0-2) 

The `is_reverted` value is sourced entirely from the hint `%{ IsReverted %}` and is never constrained by any Cairo assertion. The call to `check_is_reverted` is the only place where such a constraint could be enforced, but the function is an empty stub.

This is the direct structural analog to the reported Blueberry bug: a value (`sellSlippage` / `is_reverted`) is passed to a validation function, the function is called, but the function body performs no enforcement, leaving the protection completely inert.

---

### Impact Explanation

**Critical — Direct loss of funds.**

A malicious prover (sequencer) can set `is_reverted = TRUE` for any invoke transaction that would otherwise succeed. The OS proof accepts this because `check_is_reverted` imposes no constraint. The consequence:

- The `__execute__` entry point is skipped entirely.
- `charge_fee` still runs and transfers the actual fee from the user's account to the sequencer.
- The user's transaction has no effect on state, but the user's token balance is reduced by the fee.

Conversely, the prover can set `is_reverted = FALSE` for a transaction whose execution would revert, forcing the execute step to run and committing state changes that the contract logic intended to roll back.

---

### Likelihood Explanation

The StarkNet OS proof is the mechanism that is supposed to prevent the sequencer from lying about execution results. The entire security model of the protocol depends on the OS constraining what the sequencer can prove. Because `check_is_reverted` is empty, the OS proof places no constraint on the revert status of any invoke transaction. Any sequencer that generates a proof can freely choose the revert status of every invoke transaction in a block without the proof becoming invalid. This is not a theoretical edge case; it is a structural gap in the proof's coverage of a critical execution branch.

---

### Recommendation

`check_is_reverted` must be given a body that cryptographically ties the hint-provided `is_reverted` value to the actual execution outcome. The standard approach in Cairo OS code is to derive the revert status from the execution trace (e.g., from the return value or a revert log written during execution) and assert equality with the hint value, so that a prover cannot supply a false value without producing an invalid proof.

---

### Proof of Concept

1. Sequencer includes a valid invoke transaction `T` from user `U` in a block.
2. When generating the OS proof, the sequencer sets the hint `IsReverted` to `TRUE` for `T`.
3. The OS executes: `check_is_reverted(TRUE)` → returns immediately (no assertion).
4. The `if (is_reverted == FALSE)` branch is not taken; `__execute__` is skipped.
5. `charge_fee` runs, transferring `actual_fee` tokens from `U` to the sequencer.
6. The proof is valid because no Cairo assertion was violated.
7. On-chain, the proof is accepted; `U` has paid a fee for a transaction that had no effect. [1](#0-0) [2](#0-1)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo (L20-22)
```text
func check_is_reverted(is_reverted: felt) {
    return ();
}
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
