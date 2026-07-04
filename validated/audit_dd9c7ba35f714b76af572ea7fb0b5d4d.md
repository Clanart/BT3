### Title
Unconstrained `is_reverted` Flag Allows Malicious Prover to Skip Invoke Transaction Execution — (File: `execution/transaction_impls.cairo` + `execution/execution_constraints.cairo`)

---

### Summary

In `execute_invoke_function_transaction`, the `is_reverted` flag is populated exclusively by a prover-controlled hint (`%{ IsReverted %}`) and the function `check_is_reverted` is a **no-op** (empty body, just `return ()`). A malicious prover can set `is_reverted = 1` for any invoke transaction, causing the `__execute__` entry point to be skipped entirely while the nonce is still incremented and fees are still charged, producing a valid proof for an incorrect state transition.

---

### Finding Description

In `execute_invoke_function_transaction` the execution flow is:

1. Validate the transaction (`run_validate`).
2. Increment the nonce (`check_and_increment_nonce`).
3. Set `is_reverted` via a hint: `local is_reverted; %{ IsReverted %}`.
4. Call `check_is_reverted(is_reverted)` — which is defined as:

```cairo
func check_is_reverted(is_reverted: felt) {
    return ();
}
```

This function performs **no assertion whatsoever**.

5. Branch on `is_reverted`:
   - `FALSE` → call `non_reverting_select_execute_entry_point_func` (actual execution).
   - non-zero → skip execution entirely (only `tempvar` stack alignment).
6. Charge fee (`charge_fee`).

Because `check_is_reverted` is a no-op and `is_reverted` is only set by a hint, the prover can freely choose `is_reverted = 1` for any transaction. Cairo's `if` branching only constrains that the chosen branch is consistent with the value of `is_reverted`; it does not constrain `is_reverted` itself to match the actual execution outcome. When the `else` branch is taken, `non_reverting_select_execute_entry_point_func` is never called, so no execution-result constraint is ever applied.

This is the direct analog of the `Rv32HintStoreChip` bug: just as the prover could omit `is_buffer_start` to bypass the execution-bridge constraint on timestamps and addresses, here the prover can set `is_reverted = 1` to bypass the execution constraint entirely, while the surrounding accounting (nonce, fee) still proceeds.

The virtual OS version correctly constrains this flag:

```cairo
func check_is_reverted(is_reverted: felt) {
    with_attr error_message("Reverted transactions are not supported in virtual OS mode") {
        assert is_reverted = FALSE;
    }
    return ();
}
```

But the **production non-virtual OS** version is empty.

---

### Impact Explanation

A malicious prover can produce a **cryptographically valid proof** for a block in which every invoke transaction is marked as reverted, regardless of what the actual execution would produce. Concretely:

- **Permanent freezing of funds**: All ERC-20 transfers, withdrawals, and DeFi interactions are silently skipped. The state root reflects no storage changes. Funds are permanently inaccessible.
- **Direct loss of funds**: `charge_fee` is called unconditionally after the if/else block. Users lose gas fees for transactions that were never executed. Nonces are incremented, so the transactions cannot be replayed.

The L1 verifier accepts the proof because no Cairo constraint is violated. The incorrect state root is committed on-chain.

---

### Likelihood Explanation

The prover controls hint execution in the Cairo OS program. The `%{ IsReverted %}` hint is the sole source of `is_reverted`. Since `check_is_reverted` imposes no constraint, any entity running the OS program (the sequencer/prover) can exploit this for every invoke transaction in every block. No special key material or external dependency is required — only the ability to run the OS program with a modified hint, which is the prover's normal role.

---

### Recommendation

`check_is_reverted` in `execution_constraints.cairo` must be made non-trivial for the production OS. The `is_reverted` value must be constrained to match the actual execution outcome. One approach: always run the execute entry point and derive `is_reverted` from `entry_point_return_values.failure_flag` (as is already done inside `execute_entry_point`), then apply the revert log if needed. Alternatively, add an explicit assertion that ties the hint-provided `is_reverted` to a verifiable execution result before the branch is taken.

---

### Proof of Concept

A malicious prover replaces the `IsReverted` hint implementation to unconditionally return `True` (1) for all invoke transactions. Because `check_is_reverted` is a no-op, the Cairo program takes the `else` branch for every invoke transaction:

- No storage writes occur (`contract_state_changes` is unchanged).
- No L1 messages are emitted (`outputs` is unchanged).
- The nonce is incremented (done before the branch).
- Fees are charged (done after the branch).

The resulting proof is valid — no Cairo `assert` is violated. The L1 verifier accepts the proof. The committed state root reflects a world where no invoke transaction ever executed, while all user funds have been drained as fees.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L338-358)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L360-365)
```text
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints__virtual.cairo (L7-12)
```text
func check_is_reverted(is_reverted: felt) {
    with_attr error_message("Reverted transactions are not supported in virtual OS mode") {
        assert is_reverted = FALSE;
    }
    return ();
}
```
