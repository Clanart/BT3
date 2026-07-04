### Title
Empty `check_is_reverted` Allows Prover to Arbitrarily Skip Invoke Transaction Execution While Charging Fees - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo`)

---

### Summary

The production StarkNet OS `check_is_reverted` function is a no-op stub. The `is_reverted` flag for invoke transactions is loaded from an unconstrained hint and never verified against any on-chain state or execution result. A malicious prover (sequencer) can set `is_reverted = TRUE` for any invoke transaction, causing the execute step to be silently skipped while the user's nonce is still incremented and their fee is still charged. This breaks the soundness guarantee of the proof system and constitutes a direct loss of funds.

---

### Finding Description

In `execution_constraints.cairo`, the production OS defines `check_is_reverted` as:

```cairo
func check_is_reverted(is_reverted: felt) {
    return ();
}
``` [1](#0-0) 

The function accepts the `is_reverted` argument and immediately returns — no assertion, no range check, no constraint of any kind.

Compare this to the virtual OS version in `execution_constraints__virtual.cairo`, which correctly enforces the constraint:

```cairo
func check_is_reverted(is_reverted: felt) {
    with_attr error_message("Reverted transactions are not supported in virtual OS mode") {
        assert is_reverted = FALSE;
    }
    return ();
}
``` [2](#0-1) 

The virtual version asserts a specific value. The production version asserts nothing.

In `transaction_impls.cairo`, `execute_invoke_function_transaction` loads `is_reverted` from a hint and calls `check_is_reverted`:

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
    // Align the stack with the `if` branch to avoid revoked references.
    tempvar range_check_ptr = range_check_ptr;
    ...
    tempvar _dummy_return_value: non_reverting_select_execute_entry_point_func.Return;
}

// Charge fee.
charge_fee(block_context=block_context, tx_execution_context=updated_tx_execution_context);
``` [3](#0-2) 

Because `check_is_reverted` does nothing, `is_reverted` is a free variable in the proof. The prover can set it to any non-zero value. When `is_reverted != FALSE`, the entire execute step is bypassed — the else branch only aligns the stack and produces no state changes. Critically, `check_and_increment_nonce` and `charge_fee` are both called regardless of `is_reverted`: [4](#0-3) [5](#0-4) 

This means: nonce incremented, fee charged, execution skipped — and a valid proof is generated for this invalid state transition.

---

### Impact Explanation

**Critical. Direct loss of funds.**

For every invoke transaction in a block, a malicious prover can:
1. Set `is_reverted = TRUE` (any non-zero felt value) via the `%{ IsReverted %}` hint.
2. The OS skips the execute step entirely (the else branch is a no-op).
3. `charge_fee` still executes and transfers the user's fee to the sequencer.
4. `check_and_increment_nonce` still increments the user's nonce.
5. A valid STARK proof is produced for this block.
6. The L1 verifier accepts the proof.

The user's funds (fee) are permanently transferred to the sequencer, their nonce is consumed, and their intended transaction has no effect. This is irreversible once the proof is accepted on L1. Applied across all invoke transactions in a block, the sequencer can drain all user fees while providing no execution.

---

### Likelihood Explanation

The root cause is a missing Cairo `assert` statement in a function that is called on every invoke transaction. The hint `%{ IsReverted %}` is provided by the prover with no cryptographic or Cairo-level constraint binding it to the actual execution outcome. Any entity operating a sequencer node and generating proofs can exploit this without any special precondition, leaked key, or external dependency. The only requirement is control over the hint-providing layer of the prover, which is inherent to operating a sequencer.

---

### Recommendation

Replace the empty stub in `execution_constraints.cairo` with a constraint that verifies `is_reverted` against the actual execution result. At minimum, the production OS must verify that if `is_reverted = TRUE`, the execute entry point would indeed revert when run. One approach is to mirror the pattern used in `non_reverting_select_execute_entry_point_func` — run the execute step with a revert log and assert the outcome matches the hint-provided `is_reverted` value. Alternatively, if the production OS intends to support reverts, it must add a Cairo-level assertion that ties `is_reverted` to a verifiable on-chain condition (e.g., the result of actually running the execute entry point and observing whether it reverted).

---

### Proof of Concept

**Setup:** Attacker operates a sequencer node and controls the hint-providing layer.

**Steps:**

1. User submits a valid invoke transaction (e.g., a token transfer) with a non-zero fee bound.
2. The sequencer includes the transaction in a block.
3. During OS proof generation, the prover sets `%{ IsReverted %}` to return `1` (any non-zero felt) for this transaction.
4. `check_is_reverted(1)` is called — it returns immediately with no assertion.
5. The `if (is_reverted == FALSE)` branch is not taken; the else branch executes, producing no state changes from the execute step.
6. `charge_fee` runs normally, transferring the user's fee to the sequencer address.
7. `check_and_increment_nonce` has already run, consuming the user's nonce.
8. The OS produces a valid proof. The L1 verifier accepts it.

**Result:** The user's fee is taken, their nonce is consumed, and their transaction has no effect. The sequencer has stolen the fee with a valid proof. Repeated across all transactions in a block, the sequencer can extract all user fees while providing zero execution.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo (L20-22)
```text
func check_is_reverted(is_reverted: felt) {
    return ();
}
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L311-312)
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
