### Title
Missing Boolean Constraint on Hint-Provided `is_reverted` Enables Forced Execution of Reverted Transactions — (`File: execution_constraints.cairo`, `transaction_impls.cairo`)

### Summary

The `check_is_reverted` function in `execution_constraints.cairo` is completely empty — it accepts the hint-provided `is_reverted` value but applies zero constraints. This is the direct structural analog of the reported missing `isNeg * (1-isNeg) = 0` constraint: a value used as a boolean gate on a critical execution branch is never constrained to `{0, 1}`. A malicious prover can freely set `is_reverted` to any field element, forcing execution of transactions that should be reverted or suppressing execution of transactions that should run, producing invalid state transitions accepted by the L1 verifier.

### Finding Description

In `execution_constraints.cairo`, the function that is supposed to validate the `is_reverted` flag is a no-op:

```cairo
func check_is_reverted(is_reverted: felt) {
    return ();
}
``` [1](#0-0) 

This function is called in `execute_invoke_function_transaction` immediately before `is_reverted` gates the entire `__execute__` entry point:

```cairo
local is_reverted;
%{ IsReverted %}
check_is_reverted(is_reverted);
if (is_reverted == FALSE) {
    // Execute only non-reverted transactions.
    ...
    non_reverting_select_execute_entry_point_func(...);
    ...
} else {
    ...
    tempvar _dummy_return_value: non_reverting_select_execute_entry_point_func.Return;
}
// Charge fee.
charge_fee(...);
``` [2](#0-1) 

Because `check_is_reverted` does nothing, `is_reverted` is a free variable. The prover can set it to any field element and the resulting proof will satisfy all constraints. There is no `assert is_reverted * (1 - is_reverted) = 0` or equivalent range check anywhere.

The same unconstrained pattern appears in `execute_l1_handler_transaction`, where `check_is_reverted` is not even called — `is_reverted` is used raw from the hint:

```cairo
local is_reverted;
%{ IsReverted %}
// Skip the execution step for reverted transaction.
if (is_reverted != FALSE) {
    %{ EndTx %}
    return ();
}
``` [3](#0-2) 

Contrast this with the correctly constrained boolean pattern used elsewhere in the same OS for `use_kzg_da` and `full_output`:

```cairo
assert use_kzg_da * use_kzg_da = use_kzg_da;
assert full_output * full_output = full_output;
``` [4](#0-3) 

Those values are properly constrained to `{0, 1}`. `is_reverted` is not.

### Impact Explanation

**Attack vector A — Force execution of a reverted transaction (Direct loss of funds):**
A malicious prover sets `is_reverted = 0` (FALSE) for a transaction whose `__execute__` entry point would normally revert (e.g., a transfer that exceeds balance, a reentrancy guard, or any assertion failure). Because the OS skips the revert and runs `non_reverting_select_execute_entry_point_func`, the state changes from that execution are committed. The fee is charged regardless, so the sequencer is paid. The resulting state root — containing the illegitimate state change — is committed to L1 via the proof, which the L1 verifier accepts because no constraint was violated.

**Attack vector B — Suppress execution of a valid L1 handler (Permanent freezing / loss of funds):**
For L1 handler transactions, a malicious prover sets `is_reverted = 1` (non-zero). The OS skips execution and returns immediately. The L1-to-L2 message is consumed (marked processed in the output) but the handler never runs. Funds locked in the L1 bridge contract that were contingent on the L1 handler executing are permanently frozen — the message cannot be replayed because it is marked consumed. [5](#0-4) 

### Likelihood Explanation

The StarkNet OS is designed for a decentralized proving model. In that model, the prover is not a trusted party — it is any entity that generates a proof for a fee. The `proof_facts` mechanism already present in the codebase demonstrates that client-side proving is an intended use case. [6](#0-5) 

Any prover who processes a block containing a target transaction can exploit this. The prover does not need any special key or privileged access — only the ability to run the OS and submit a proof. The missing constraint produces a proof that is indistinguishable from a correct one to the L1 verifier.

### Recommendation

Add a boolean constraint inside `check_is_reverted` mirroring the pattern already used for `use_kzg_da` and `full_output`:

```cairo
func check_is_reverted(is_reverted: felt) {
    assert is_reverted * (1 - is_reverted) = 0;
    return ();
}
```

Additionally, add the same call to `check_is_reverted` in `execute_l1_handler_transaction`, which currently omits it entirely.

### Proof of Concept

1. Deploy a contract whose function unconditionally reverts (e.g., `assert 1 = 0`).
2. Submit an invoke transaction calling that function with valid signature and sufficient gas.
3. As the prover, run the StarkNet OS with the hint `%{ IsReverted %}` returning `0` (FALSE) instead of the correct `1`.
4. Since `check_is_reverted` applies no constraint, the Cairo constraint system is fully satisfied.
5. The generated proof is valid; the L1 verifier accepts it.
6. The state root committed to L1 reflects the execution of the reverted transaction — an illegitimate state change — with no on-chain evidence of fraud.

For the L1 handler variant: submit an L1-to-L2 message carrying value. As the prover, set `is_reverted = 1` for the corresponding L1 handler. The message is marked consumed in the OS output, the proof is accepted on L1, but the handler never executed — the bridged funds are permanently lost.

### Citations

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L491-518)
```text
func consume_l1_to_l2_message{outputs: OsCarriedOutputs*}(
    execution_context: ExecutionContext*, nonce: felt
) {
    assert_not_zero(execution_context.calldata_size);
    // The payload is the calldata without the from_address argument (which is the first).
    let payload: felt* = execution_context.calldata + 1;
    tempvar payload_size = execution_context.calldata_size - 1;

    tempvar execution_info = execution_context.execution_info;

    // Write the given transaction to the output.
    assert [outputs.messages_to_l2] = MessageToL2Header(
        from_address=[execution_context.calldata],
        to_address=execution_info.contract_address,
        nonce=nonce,
        selector=execution_info.selector,
        payload_size=payload_size,
    );

    let message_payload = cast(outputs.messages_to_l2 + MessageToL2Header.SIZE, felt*);
    memcpy(dst=message_payload, src=payload, len=payload_size);

    let (outputs) = os_carried_outputs_new(
        messages_to_l1=outputs.messages_to_l1,
        messages_to_l2=outputs.messages_to_l2 + MessageToL2Header.SIZE +
        outputs.messages_to_l2.payload_size,
    );
    return ();
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os_utils.cairo (L165-166)
```text
    assert use_kzg_da * use_kzg_da = use_kzg_da;
    assert full_output * full_output = full_output;
```
