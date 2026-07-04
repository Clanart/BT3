### Title
Empty `check_is_reverted` Allows Prover to Bypass Transaction Execution While Charging Fees — (File: `execution/execution_constraints.cairo`)

---

### Summary

The `check_is_reverted` function in `execution_constraints.cairo` is a complete no-op. The `is_reverted` value it receives is loaded from a prover-controlled hint and is entirely unconstrained by any Cairo assertion. A malicious prover (sequencer) can set `is_reverted = TRUE` for any valid invoke transaction, causing the `__execute__` entry point to be skipped while the user's nonce is still incremented and their fee is still charged.

---

### Finding Description

In `execute_invoke_function_transaction`, the OS loads `is_reverted` from a hint and immediately calls `check_is_reverted` before branching on its value:

```cairo
local is_reverted;
%{ IsReverted %}
check_is_reverted(is_reverted);
if (is_reverted == FALSE) {
    // Execute only non-reverted transactions.
    ...
    non_reverting_select_execute_entry_point_func(...);
} else {
    // Align the stack — execution is skipped entirely.
    tempvar _dummy_return_value: non_reverting_select_execute_entry_point_func.Return;
}

// Charge fee — runs unconditionally regardless of is_reverted.
charge_fee(block_context=block_context, tx_execution_context=updated_tx_execution_context);
``` [1](#0-0) 

The function `check_is_reverted` is defined as:

```cairo
func check_is_reverted(is_reverted: felt) {
    return ();
}
``` [2](#0-1) 

In Cairo's ZK proof model, hints are non-deterministic prover inputs. The Cairo code is the sole mechanism for constraining them. Because `check_is_reverted` contains no `assert`, no range check, and no comparison, the value of `is_reverted` is completely unconstrained. The prover can assign it any value and the resulting execution trace will be accepted as valid by the verifier.

The analog to the reported bug is exact: just as `RevenueHandler.claim()` omits the cooldown check and allows an action that should be blocked, `check_is_reverted` omits all validation and allows the prover to declare any transaction reverted without proof.

The nonce increment (`check_and_increment_nonce`) runs unconditionally before the branch: [3](#0-2) 

And `charge_fee` runs unconditionally after the branch: [4](#0-3) 

So when `is_reverted` is forced to `TRUE`:
- The user's nonce is consumed.
- The user's fee is deducted.
- The `__execute__` entry point is never called.
- The intended state change (e.g., token transfer, contract interaction) never occurs.

---

### Impact Explanation

**Direct loss of funds (Critical):** A malicious sequencer/prover can mark any user's invoke transaction as reverted. The user loses the fee and their nonce advances, but receives no execution. This is theft of gas fees and suppression of intended state changes.

**Network not being able to confirm new transactions (High):** If the sequencer applies `is_reverted = TRUE` universally, no invoke transaction's `__execute__` phase ever runs. The network continues to accept and finalize blocks, but no user-initiated state changes are ever committed, constituting a functional network halt.

---

### Likelihood Explanation

In StarkNet's current architecture the sequencer controls the prover. The sequencer constructs the OS execution trace and provides all hints, including `IsReverted`. Since `check_is_reverted` imposes zero constraints, exploiting this requires only that the sequencer set the hint value to `1` for targeted transactions. No cryptographic break, no key leak, and no external dependency is required. The entry path is a standard invoke transaction submitted by any unprivileged user.

---

### Recommendation

`check_is_reverted` must be implemented to actually constrain `is_reverted`. The correct approach is to derive `is_reverted` from a provable source — for example, from the return values of the validate step — and assert consistency. At minimum, the function should assert that `is_reverted` is a boolean:

```cairo
func check_is_reverted(is_reverted: felt) {
    assert is_reverted * (is_reverted - 1) = 0;
    // Additional: assert consistency with validate return value or
    // other provable execution outcome.
    return ();
}
```

The deeper fix is to derive `is_reverted` from the actual execution result rather than from a free hint, so the prover cannot choose it independently of the execution trace.

---

### Proof of Concept

1. User submits a valid invoke transaction (e.g., a token transfer).
2. Malicious sequencer sets the `IsReverted` hint to `1` for this transaction in the OS execution trace.
3. `check_is_reverted(1)` is called — it returns immediately with no assertion.
4. The `if (is_reverted == FALSE)` branch is not taken; `non_reverting_select_execute_entry_point_func` is never called.
5. `charge_fee` executes normally, deducting the fee from the user's account.
6. `check_and_increment_nonce` has already run, consuming the nonce.
7. The OS produces a valid proof (no Cairo constraint was violated).
8. The L1 verifier accepts the proof and commits the state transition.
9. Result: user's fee is gone, nonce is incremented, token transfer never happened.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L311-311)
```text
    check_and_increment_nonce(tx_info=tx_info);
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo (L20-22)
```text
func check_is_reverted(is_reverted: felt) {
    return ();
}
```
