### Title
`check_is_reverted` Is a No-Op in Production OS, Leaving `is_reverted` Hint Unconstrained — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo`)

---

### Summary

The production OS version of `check_is_reverted` immediately returns without asserting anything, leaving the hint-provided `is_reverted` value completely unconstrained. A malicious prover can set `is_reverted = TRUE` for any invoke transaction, causing its `__execute__` to be skipped while fees are still charged and the nonce is still incremented — producing a valid STARK proof for a block where user transactions were silently dropped.

---

### Finding Description

In `execution_constraints.cairo`, the production OS defines:

```cairo
func check_is_reverted(is_reverted: felt) {
    return ();
}
``` [1](#0-0) 

The function accepts `is_reverted` but performs no assertion whatsoever — it is a pure no-op. This is the direct analog of the external report's pattern: the validation that should execute never does.

By contrast, the virtual OS version correctly constrains the value:

```cairo
// Checks that the transaction is not reverted.
func check_is_reverted(is_reverted: felt) {
    with_attr error_message("Reverted transactions are not supported in virtual OS mode") {
        assert is_reverted = FALSE;
    }
    return ();
}
``` [2](#0-1) 

In `execute_invoke_function_transaction`, `is_reverted` is loaded from a hint and then passed to `check_is_reverted`:

```cairo
local is_reverted;
%{ IsReverted %}
check_is_reverted(is_reverted);
if (is_reverted == FALSE) {
    // Execute only non-reverted transactions.
    ...
} else {
    // Skip execution entirely.
    ...
}
// Charge fee regardless.
charge_fee(...);
``` [3](#0-2) 

Because `check_is_reverted` does nothing, `is_reverted` is never constrained by any Cairo assertion. In Cairo's proving model, hint values are prover-supplied and are only trusted if the program asserts constraints on them. Without any assertion, the prover can freely assign any non-zero value to `is_reverted` and still produce a valid proof.

The nonce increment (`check_and_increment_nonce`) and fee charge (`charge_fee`) both execute unconditionally regardless of `is_reverted`: [4](#0-3) [5](#0-4) 

---

### Impact Explanation

**Direct loss of funds (Critical).**

A malicious prover sets `is_reverted = 1` (or any non-zero felt) for a victim's invoke transaction. The OS skips `__execute__`, so the user's intended state changes never happen. The user's nonce is incremented (replay protection consumed) and their fee is deducted. The resulting STARK proof is valid — no constraint is violated — so the L1 verifier accepts it. The user has paid fees and lost their nonce slot for a transaction that was never executed.

---

### Likelihood Explanation

The prover in StarkNet is the entity that runs the OS Cairo program and supplies all hint values. The OS is the trustless verification layer whose purpose is to constrain prover behavior so that even a dishonest prover cannot produce a valid proof for an incorrect state transition. Because `check_is_reverted` imposes zero constraints, this invariant is broken for the revert-status of invoke transactions. Any prover who controls hint injection — including a compromised or malicious sequencer — can exploit this without any additional privilege beyond running the prover.

---

### Recommendation

Replace the no-op body with a boolean constraint. At minimum, assert that `is_reverted` is a valid boolean (0 or 1):

```cairo
func check_is_reverted(is_reverted: felt) {
    assert is_reverted * (1 - is_reverted) = 0;
    return ();
}
```

Additionally, the OS should verify that `is_reverted = TRUE` is only accepted when the execution actually reverted (e.g., by running the transaction and checking the revert log), so the prover cannot fabricate a revert for a transaction that would have succeeded.

---

### Proof of Concept

1. Prover constructs a block containing a victim's invoke transaction (e.g., an ERC-20 transfer).
2. When the OS program reaches `%{ IsReverted %}` for that transaction, the prover sets `is_reverted = 1`.
3. `check_is_reverted(1)` is called — it immediately returns, no assertion fires.
4. The `if (is_reverted == FALSE)` branch is not taken; `__execute__` is skipped.
5. `check_and_increment_nonce` and `charge_fee` still execute, consuming the user's nonce and deducting fees.
6. The prover generates a valid STARK proof. The L1 verifier accepts it.
7. On-chain state reflects: nonce incremented, fee deducted, but the ERC-20 transfer never happened. Funds are lost. [1](#0-0) [3](#0-2)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo (L20-22)
```text
func check_is_reverted(is_reverted: felt) {
    return ();
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints__virtual.cairo (L6-12)
```text
// Checks that the transaction is not reverted.
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
