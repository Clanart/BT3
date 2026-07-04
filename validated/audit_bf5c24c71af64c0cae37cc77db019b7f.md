### Title
Missing `check_is_reverted` Validation in `execute_l1_handler_transaction` Allows Malicious Prover to Silently Drop L1→L2 Messages — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`execute_invoke_function_transaction` validates the hint-provided `is_reverted` value via `check_is_reverted()` before branching on it. `execute_l1_handler_transaction` loads the same hint-provided `is_reverted` value and branches on it **without** calling `check_is_reverted`. This is the direct structural analog of the reported `balanceOfNFT` / `balanceOfNFTAt` inconsistency: a protection check is applied in one code path but omitted from a parallel code path that handles the same field.

---

### Finding Description

In `transaction_impls.cairo`, the two transaction execution functions handle the `is_reverted` hint differently:

**`execute_invoke_function_transaction`** (lines 338–358):
```cairo
local is_reverted;
%{ IsReverted %}
check_is_reverted(is_reverted);          // <-- hint is validated
if (is_reverted == FALSE) {
    // Execute only non-reverted transactions.
    ...
}
``` [1](#0-0) 

**`execute_l1_handler_transaction`** (lines 383–390):
```cairo
local is_reverted;
%{ IsReverted %}
// Skip the execution step for reverted transaction.
if (is_reverted != FALSE) {             // <-- hint is used directly, NO check_is_reverted
    %{ EndTx %}
    return ();
}
``` [2](#0-1) 

`check_is_reverted` is imported from `execution_constraints.cairo` and is the OS-level enforcement that the prover-supplied `is_reverted` hint is consistent with the actual execution outcome. Without it, the prover can supply any value for `is_reverted` for an L1 handler transaction and the OS will accept it unchallenged.

When `is_reverted != FALSE`, `execute_l1_handler_transaction` returns immediately — **before** `consume_l1_to_l2_message` is called: [3](#0-2) 

`consume_l1_to_l2_message` is the function that writes the L1→L2 message into `outputs.messages_to_l2`, which is the OS output that the L1 verifier uses to mark messages as consumed on L1: [4](#0-3) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

If a malicious prover sets `is_reverted = 1` (or any non-zero value) for an L1 handler transaction that should have succeeded:

1. The L1 handler body is never executed — no L2 state changes occur (e.g., no token minting for a bridge deposit).
2. `consume_l1_to_l2_message` is never called, so the message is absent from the OS output.
3. The L1 verifier does not see the message as consumed, so the L1 bridge contract retains the user's deposited funds indefinitely.
4. The L2 side never receives the tokens.

The user's L1 funds are permanently frozen: not reflected on L2, and the L1 message is never acknowledged as consumed by the verifier.

---

### Likelihood Explanation

**Medium.**

The StarkNet OS is the trustless enforcement layer — it is specifically designed to produce a valid proof even against a malicious sequencer/prover. The hint system is the attack surface: hints are prover-supplied and must be validated by OS assertions. The `check_is_reverted` call exists precisely to close this attack surface for invoke transactions. Its absence for L1 handlers means any entity running the Cairo VM (i.e., the sequencer acting as prover) can exploit this without any external dependency, leaked key, or network-level attack. The attacker-controlled entry path is the `IsReverted` hint for any L1 handler transaction in a block.

---

### Recommendation

Apply `check_is_reverted(is_reverted)` in `execute_l1_handler_transaction` immediately after the `%{ IsReverted %}` hint, mirroring the pattern in `execute_invoke_function_transaction`:

```cairo
local is_reverted;
%{ IsReverted %}
check_is_reverted(is_reverted);   // add this line
if (is_reverted != FALSE) {
    %{ EndTx %}
    return ();
}
```

This ensures the OS enforces that the prover cannot falsely claim an L1 handler was reverted.

---

### Proof of Concept

1. Attacker controls the sequencer/prover node.
2. A user submits an L1→L2 message (e.g., a bridge deposit) that triggers an L1 handler on L2.
3. The sequencer includes the L1 handler transaction in a block.
4. When running the Cairo OS, the prover supplies `is_reverted = 1` via the `IsReverted` hint for this L1 handler.
5. Because `check_is_reverted` is not called, the OS accepts this value without validation.
6. `execute_l1_handler_transaction` returns early at line 388–390 without executing the handler body or calling `consume_l1_to_l2_message`.
7. The generated proof is valid (the OS accepted the hint).
8. The L1 verifier processes the proof: the L1→L2 message is absent from the OS output, so it is never marked consumed on L1.
9. The user's deposited funds remain locked in the L1 bridge contract permanently, and no tokens are minted on L2.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L443-448)
```text
    // Consume L1-to-L2 message.
    consume_l1_to_l2_message(execution_context=tx_execution_context, nonce=nonce);
    let remaining_gas = L1_HANDLER_L2_GAS_MAX_AMOUNT;
    non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
        block_context=block_context, execution_context=tx_execution_context
    );
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
