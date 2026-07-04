### Title
Missing `is_reverted` Hint Validation Allows Malicious Prover to Skip Transaction Execution While Charging Fees — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo`)

---

### Summary

The `check_is_reverted` function in `execution_constraints.cairo` is completely empty. The `is_reverted` flag controlling whether a transaction's execute step runs is loaded from an unverified prover hint in `execute_invoke_function_transaction`. Because the OS never constrains this hint with any Cairo assertion, a malicious prover can set `is_reverted = TRUE` for any valid invoke transaction, causing the execute step to be skipped while the nonce is still incremented and the fee is still charged — a direct loss of funds for users.

---

### Finding Description

In `execute_invoke_function_transaction`, the OS loads `is_reverted` from a hint and then calls `check_is_reverted` before branching:

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
    ...
    tempvar _dummy_return_value: non_reverting_select_execute_entry_point_func.Return;
}

// Charge fee.
charge_fee(block_context=block_context, tx_execution_context=updated_tx_execution_context);
``` [1](#0-0) 

The function `check_is_reverted` that is supposed to validate this hint is:

```cairo
func check_is_reverted(is_reverted: felt) {
    return ();
}
``` [2](#0-1) 

It contains **no Cairo assertions whatsoever**. The hint value is accepted unconditionally. By contrast, the analogous `check_proof_facts` function in the same file performs real cryptographic validation: [3](#0-2) 

The same pattern exists in `execute_l1_handler_transaction`, where `is_reverted` is loaded from a hint with no `check_is_reverted` call at all — the entire L1 handler (including `consume_l1_to_l2_message`) is skipped if the hint is non-zero: [4](#0-3) 

The execution flow for invoke transactions before the `is_reverted` branch already commits irreversible side effects:
- `check_and_increment_nonce` increments the account nonce
- `run_validate` runs the `__validate__` entry point [5](#0-4) 

After the branch, `charge_fee` is called unconditionally regardless of `is_reverted`: [6](#0-5) 

---

### Impact Explanation

A malicious prover can set `is_reverted = TRUE` for any valid invoke transaction in the hint. The resulting proof is valid because no Cairo constraint checks this value. The L1 verifier accepts the proof. The on-chain effect is:

1. The user's nonce is incremented (transaction is consumed).
2. The user's fee is charged.
3. The user's `__execute__` entry point never runs — no intended state changes occur.

This constitutes **direct loss of funds**: the user pays a fee for a transaction that produces no effect. For L1 handler transactions, the L1-to-L2 message is never consumed, potentially causing **permanent freezing of funds** locked in the L1 bridge if the message cannot be re-submitted.

---

### Likelihood Explanation

The StarkNet OS is the mechanism that prevents a malicious sequencer/prover from producing a valid proof that misrepresents execution. The entire ZK-proof security model depends on the OS constraining all prover-supplied hints. Because `check_is_reverted` is empty, the OS provides zero constraint on this critical branching decision. Any sequencer operator who controls proof generation can exploit this without any additional capability. The likelihood is **medium**: it requires a malicious sequencer, but the OS is specifically designed to make sequencer misbehavior unprovable — this design goal is violated here.

---

### Recommendation

`check_is_reverted` must be implemented to enforce that the hint is consistent with actual execution. The correct approach is to always run the execute entry point and derive `is_reverted` from the actual `failure_flag` returned by `execute_entry_point`, rather than accepting it as an unverified prover hint. If the optimization of skipping execution for known-reverted transactions is desired, the OS must provide a Cairo-level proof of revert (e.g., by running execution in a sandboxed segment and asserting the failure flag).

---

### Proof of Concept

1. A malicious sequencer selects a valid user invoke transaction (signature valid, nonce correct, sufficient gas).
2. In the hint `%{ IsReverted %}`, the sequencer sets `is_reverted = 1` (TRUE).
3. `check_is_reverted(1)` is called — it immediately returns with no assertion.
4. The `if (is_reverted == FALSE)` branch is not taken; `non_reverting_select_execute_entry_point_func` is never called.
5. `charge_fee` executes, deducting the fee from the user's account.
6. The Cairo proof is generated and verified on L1 — it is valid because no constraint was violated.
7. The user's nonce is incremented and fee is deducted, but their transaction produced no state changes.
8. Repeated across many transactions, this drains user balances without executing any user-intended logic.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L311-332)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L338-363)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L383-390)
```text
    %{ StartTx %}
    local is_reverted;
    %{ IsReverted %}
    // Skip the execution step for reverted transaction.
    if (is_reverted != FALSE) {
        %{ EndTx %}
        return ();
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo (L20-22)
```text
func check_is_reverted(is_reverted: felt) {
    return ();
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo (L34-81)
```text
func check_proof_facts{range_check_ptr, contract_state_changes: DictAccess*}(
    proof_facts_size: felt,
    proof_facts: felt*,
    current_block_number: felt,
    virtual_os_config_hash: felt,
) {
    if (proof_facts_size == 0) {
        return ();
    }

    assert_le(ProofHeader.SIZE + VirtualOsOutputHeader.SIZE, proof_facts_size);

    // Validate the proof header.
    let proof_header = cast(proof_facts, ProofHeader*);
    assert is_program_hash_allowed(proof_header.program_hash) = TRUE;
    // Proof version and variant are for future compatibility.
    assert [proof_header] = ProofHeader(
        proof_version=PROOF_VERSION,
        proof_variant=VIRTUAL_SNOS,
        program_hash=proof_header.program_hash,
    );

    // Validate the virtual OS output header.
    let os_output_header = cast(&proof_facts[ProofHeader.SIZE], VirtualOsOutputHeader*);

    with_attr error_message("Virtual OS output version is not supported") {
        assert os_output_header.output_version = VIRTUAL_OS_OUTPUT_VERSION;
    }

    // Validate that the proof facts block number is not too recent.
    // (This is a sanity check - the following non-zero check ensures that the block hash is
    // not trivial).
    assert_nn_le(
        os_output_header.base_block_number, current_block_number - STORED_BLOCK_HASH_BUFFER
    );
    // Not all block hashes are stored in the contract; Make sure the requested one is not trivial.
    assert_not_zero(os_output_header.base_block_hash);

    // validate that the proof facts block hash is the true hash of the proof facts block number.
    read_block_hash_from_storage(
        block_number=os_output_header.base_block_number,
        expected_block_hash=os_output_header.base_block_hash,
    );

    // validate that the proof facts config hash is the true hash of the OS config.
    assert os_output_header.starknet_os_config_hash = virtual_os_config_hash;

    return ();
```
