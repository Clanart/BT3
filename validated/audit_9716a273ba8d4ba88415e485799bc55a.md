### Title
No-Op `check_is_reverted` Allows Sequencer to Silently Skip Transaction Execution While Charging Fees — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo`)

---

### Summary

The `check_is_reverted` function in `execution_constraints.cairo` is a complete no-op. The transaction-level `is_reverted` flag for invoke transactions is supplied entirely by a sequencer-controlled hint (`%{ IsReverted %}`), and the OS never cryptographically enforces that this flag reflects the actual execution outcome. A malicious sequencer can mark any transaction as reverted, causing the execute phase to be skipped while fees are still charged, and produce a valid STARK proof for this incorrect state transition.

---

### Finding Description

In `execute_invoke_function_transaction`, after the validate phase completes, the sequencer provides the `is_reverted` flag via a hint:

```cairo
local is_reverted;
%{ IsReverted %}
check_is_reverted(is_reverted);
``` [1](#0-0) 

The function `check_is_reverted` is then called to validate this hint. Its full implementation is:

```cairo
func check_is_reverted(is_reverted: felt) {
    return ();
}
``` [2](#0-1) 

This function performs **zero validation**. It accepts any value of `is_reverted` without constraint.

The flag then gates the entire execute phase:

```cairo
if (is_reverted == FALSE) {
    with remaining_gas {
        cap_remaining_gas(max_gas=EXECUTE_MAX_SIERRA_GAS);
        non_reverting_select_execute_entry_point_func(
            block_context=block_context, execution_context=updated_tx_execution_context
        );
    }
} else {
    // stack alignment only — no execution
    ...
}

// Charge fee — always runs regardless of is_reverted.
charge_fee(block_context=block_context, tx_execution_context=updated_tx_execution_context);
``` [3](#0-2) 

`charge_fee` always executes unconditionally after the branch, using `DEFAULT_INITIAL_GAS_COST` as gas and calling `non_reverting_select_execute_entry_point_func` (which asserts the fee transfer does not revert):

```cairo
let remaining_gas = DEFAULT_INITIAL_GAS_COST;
non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
    block_context=block_context, execution_context=&execution_context
);
``` [4](#0-3) 

The nonce is also incremented unconditionally before the branch:

```cairo
check_and_increment_nonce(tx_info=tx_info);
``` [5](#0-4) 

**Analogy to the external report**: The external report describes a fallback mechanism (ClaimAccount PDAs) being applied universally to all relayers when only one cannot receive direct refunds. Here, the "reverted" fallback path (skip execution, charge fee) is applied universally to any transaction the sequencer designates — even transactions that would have succeeded — because the OS never verifies the hint.

---

### Impact Explanation

**Impact: Critical — Direct loss of funds.**

A malicious sequencer can:
1. Include a user's valid invoke transaction in a block.
2. Set `is_reverted = TRUE` via the `%{ IsReverted %}` hint.
3. The OS skips the `__execute__` entry point entirely.
4. The OS still charges the full fee via `charge_fee`.
5. The nonce is incremented, preventing replay.
6. A valid STARK proof is produced for this incorrect state transition.
7. The proof is accepted on L1.

The user pays the full fee and their nonce is consumed, but their transaction is never executed. Funds are permanently lost with no recourse, since the L1 verifier accepts the proof.

---

### Likelihood Explanation

The StarkNet OS proof system exists precisely to constrain the sequencer. The sequencer is not a trusted party — the OS proof is the enforcement mechanism. Since `check_is_reverted` is a no-op, this constraint is entirely absent. Any sequencer operator who controls the hint environment can exploit this against any user transaction at will, with no additional prerequisites (no key compromise, no Sybil attack, no external dependency). The attack is silent and produces a valid proof.

---

### Recommendation

`check_is_reverted` must be implemented to cryptographically enforce that `is_reverted` reflects the actual execution outcome. The OS should derive `is_reverted` from the entry point's `failure_flag` return value (as done inside `execute_entry_point`) rather than accepting it as an unchecked hint. The hint should be used only as a performance optimization (to skip execution when the sequencer knows it will revert), but the OS must verify the claim by running the execution and checking the actual `failure_flag`.

---

### Proof of Concept

1. Sequencer receives a valid user invoke transaction `T` with sufficient gas and a correct signature.
2. Sequencer runs the validate phase normally (it must, since `run_validate` calls `non_reverting_select_execute_entry_point_func` which would fail if validation reverts).
3. Sequencer sets the hint `is_reverted = 1` (TRUE) for the execute phase.
4. `check_is_reverted(1)` is called — returns immediately, no assertion fires.
5. The `if (is_reverted == FALSE)` branch is not taken; `__execute__` is never called.
6. `charge_fee` runs, deducting the actual fee from the user's account.
7. The nonce is incremented; the transaction cannot be replayed.
8. The Cairo program terminates successfully; a valid STARK proof is generated.
9. The proof is submitted to L1 and accepted. The user's funds are lost. [2](#0-1) [6](#0-5)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L160-163)
```text
    let remaining_gas = DEFAULT_INITIAL_GAS_COST;
    non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
        block_context=block_context, execution_context=&execution_context
    );
```

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
