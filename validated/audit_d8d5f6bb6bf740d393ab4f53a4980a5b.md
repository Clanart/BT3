### Title
`check_is_reverted` Is a No-Op, Allowing Prover to Arbitrarily Bypass Transaction Execution While Charging Fees — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo`)

---

### Summary

The `check_is_reverted` function in `execution_constraints.cairo` is completely empty. It places zero constraints on the hint-supplied `is_reverted` value used in `execute_invoke_function_transaction`. A malicious prover (sequencer) can freely set `is_reverted = 1` for any transaction that would otherwise succeed, causing the execution step to be skipped while the user's nonce is still consumed and fees are still charged. The resulting proof is cryptographically valid and accepted by L1, resulting in direct, permanent loss of user funds.

---

### Finding Description

In `execute_invoke_function_transaction` (`transaction_impls.cairo`), the execution flow is:

1. Nonce is incremented unconditionally.
2. `is_reverted` is loaded from a prover-controlled hint.
3. `check_is_reverted(is_reverted)` is called — **this is a no-op**.
4. Execution is conditionally skipped based on `is_reverted`.
5. Fee is charged unconditionally. [1](#0-0) 

The function that is supposed to validate the `is_reverted` value:

```cairo
func check_is_reverted(is_reverted: felt) {
    return ();
}
``` [2](#0-1) 

This function accepts `is_reverted` as an argument and immediately returns, placing **no Cairo constraint** on the value. Since `is_reverted` is set exclusively by the hint `%{ IsReverted %}` (prover-controlled), and `check_is_reverted` enforces nothing, the prover has unconstrained freedom to choose `is_reverted = 1` for any transaction.

The nonce increment happens before the revert check: [3](#0-2) 

Fee charging happens after the revert check, unconditionally: [4](#0-3) 

The analog to the external report is direct: just as a failed migration in the bonding curve does not prevent subsequent sells that drain liquidity below the required threshold (a state-transition bypass), here a "failed" (prover-declared reverted) transaction does not prevent the OS from consuming the user's nonce and fee — the preconditions for the user's funds are silently drained without the intended state transition occurring.

---

### Impact Explanation

**Impact: Critical — Direct loss of funds.**

A malicious prover can, for any invoke transaction:
- Set `is_reverted = 1` via the hint.
- Skip the `__execute__` entry point entirely (no state changes for the user).
- Still consume the user's nonce (preventing resubmission of the same transaction).
- Still charge the user the full fee via `charge_fee`.
- Produce a proof that is cryptographically valid (no Cairo constraint is violated).
- Have L1 accept the proof.

The user permanently loses the fee and their intended state change never occurs. Their nonce is advanced, so they cannot replay the transaction. This is a direct, permanent loss of funds provable on-chain.

---

### Likelihood Explanation

**Likelihood: Low.**

The ZK proof model exists precisely so that the sequencer/prover cannot be trusted. The OS program is the enforcement layer. A bug in the OS program that removes a constraint is exactly the class of vulnerability the proof system is designed to prevent. The sequencer controls the hint `%{ IsReverted %}` and, with `check_is_reverted` being a no-op, faces no cryptographic barrier to exploiting this. The attack requires no leaked keys, no external dependencies, and no network-level access — only the sequencer's normal operation with a malicious hint value.

---

### Recommendation

Implement `check_is_reverted` to enforce that `is_reverted` is a boolean (0 or 1) and, critically, that it is consistent with the actual execution outcome. The function should assert that `is_reverted * (1 - is_reverted) == 0` at minimum, and ideally derive `is_reverted` from the actual entry point return value (`failure_flag`) rather than from a free hint. The `execute_entry_point` function already returns `is_reverted` from `entry_point_return_values.failure_flag`; the transaction-level `is_reverted` should be constrained to match this value. [5](#0-4) 

---

### Proof of Concept

1. User submits an invoke transaction with a valid signature, valid nonce, and sufficient fee. The transaction would succeed (e.g., a token transfer).
2. The malicious sequencer includes the transaction in a block.
3. During OS execution, the sequencer sets `%{ IsReverted %} → is_reverted = 1`.
4. `check_is_reverted(1)` is called — returns immediately, no constraint violated.
5. The `if (is_reverted == FALSE)` branch is not taken; `non_reverting_select_execute_entry_point_func` is never called.
6. `check_and_increment_nonce` has already run (line 311) — nonce is consumed.
7. `charge_fee` runs (line 361) — fee is deducted from the user's account.
8. The proof is generated and submitted to L1. L1 verifies the proof as valid.
9. The user's token transfer never happened. Their fee is gone. Their nonce is advanced. They cannot replay the transaction. [6](#0-5) [2](#0-1)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L311-365)
```text
    check_and_increment_nonce(tx_info=tx_info);

    check_proof_facts(
        proof_facts_size=proof_facts_size,
        proof_facts=proof_facts,
        current_block_number=block_context.block_info_for_execute.block_number,
        virtual_os_config_hash=block_context.os_global_context.virtual_os_config_hash,
    );

    %{ StartTx %}

    let initial_user_gas_bound = get_initial_user_gas_bound(common_tx_fields=common_tx_fields);
    let remaining_gas = initial_user_gas_bound;

    // Validate.
    with remaining_gas {
        cap_remaining_gas(max_gas=VALIDATE_MAX_SIERRA_GAS);
        let pre_validate_gas = remaining_gas;
        run_validate(block_context=block_context, tx_execution_context=tx_execution_context);
    }
    let validate_gas_consumed = pre_validate_gas - remaining_gas;
    tempvar remaining_gas = initial_user_gas_bound - validate_gas_consumed;

    let updated_tx_execution_context = update_class_hash_in_execution_context(
        execution_context=tx_execution_context
    );

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L260-260)
```text
    local is_reverted = entry_point_return_values.failure_flag;
```
