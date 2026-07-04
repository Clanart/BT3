### Title
Unconstrained `is_reverted` Flag Allows Prover to Bypass Invoke Transaction Execution — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo`)

---

### Summary

The `check_is_reverted` function in `execution_constraints.cairo` is a complete no-op. The `is_reverted` flag that controls whether an invoke transaction's `__execute__` entry point is called is loaded from an unconstrained hint and never verified by the Cairo constraint system. A malicious prover (sequencer) can set this flag to `TRUE` for any invoke transaction, causing execution to be silently skipped while fees are still charged, producing a valid ZK proof of an incorrect state transition.

---

### Finding Description

In `execute_invoke_function_transaction`, the `is_reverted` flag is loaded from the hint `%{ IsReverted %}` into a `local` variable, then passed to `check_is_reverted`:

```cairo
// transaction_impls.cairo lines 338–358
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
    ...
}
// Charge fee — called unconditionally regardless of is_reverted.
charge_fee(block_context=block_context, tx_execution_context=updated_tx_execution_context);
```

The function that is supposed to validate this flag is:

```cairo
// execution_constraints.cairo lines 20–22
func check_is_reverted(is_reverted: felt) {
    return ();
}
```

It is a literal no-op — it accepts the parameter and immediately returns, adding zero constraints to the Cairo proof system.

**How Cairo's constraint system makes this exploitable:** In Cairo, `if (x == 0)` does not independently constrain `x`. The prover chooses which branch to execute and sets `x` consistently with that choice. Because `is_reverted` is written by a hint (unconstrained memory) and `check_is_reverted` adds no assertions, the prover can freely set `is_reverted = 1` and take the "skip execution" branch for any invoke transaction. The resulting proof is cryptographically valid — the verifier on L1 cannot distinguish it from a proof where the transaction legitimately reverted.

The same pattern exists for L1 handler transactions in `execute_l1_handler_transaction` (lines 384–390), where `is_reverted` is also hint-loaded with no constraint function called at all, and the entire handler (including `consume_l1_to_l2_message`) is skipped.

**Analog to the reference report:** The original bug is that user-specified slippage limits are committed to in the order but not enforced at match time. Here, the user's invoke transaction is committed to in the transaction hash (including resource bounds and calldata), but the OS does not enforce that execution actually occurs — the prover can bypass it entirely. The "keeper" analog is the sequencer's off-chain honesty assumption, which the ZK proof is supposed to eliminate.

---

### Impact Explanation

**Critical — Direct loss of funds.**

1. A malicious sequencer sets `is_reverted = TRUE` for a victim's invoke transaction (e.g., a token transfer or DeFi interaction).
2. The `__execute__` entry point is never called; no state changes occur.
3. `charge_fee` is called unconditionally and deducts the full fee from the user's account via an ERC-20 transfer.
4. The resulting proof is valid and accepted by the L1 verifier.
5. The user loses the fee and receives no execution — permanently, with no on-chain recourse.

For L1 handler transactions: if `is_reverted = TRUE` is set for an L1→L2 deposit handler, `consume_l1_to_l2_message` is never called, the L1 message is never recorded as consumed on L2, and the deposited L1 funds are permanently frozen (the L1 contract has already locked them).

---

### Likelihood Explanation

The ZK proof is the protocol's primary trust mechanism — it is supposed to make the sequencer trustless. This bug breaks that guarantee at the proof level. Any sequencer operator who discovers this can exploit it silently and produce proofs that pass L1 verification. The exploit requires no leaked keys, no external dependencies, and no network-level attack — only the sequencer's normal role of constructing the OS execution trace and hints.

---

### Recommendation

`check_is_reverted` must add a real constraint. The correct approach is to determine `is_reverted` from the actual execution result rather than from a pre-execution hint. If pre-execution skip is required for gas efficiency, the OS must prove that the transaction *would have* reverted — for example, by running execution in a separate segment and constraining the output, or by requiring the prover to provide a witness that the entry point fails (e.g., out-of-gas proof). At minimum, `check_is_reverted` must assert that `is_reverted` is a boolean (`0` or `1`) and tie it to a verifiable execution outcome.

---

### Proof of Concept

1. User submits an invoke transaction (e.g., `transfer(recipient, amount)`) with valid signature and sufficient resource bounds.
2. Malicious sequencer includes the transaction in a block but sets the hint `IsReverted = True` for it.
3. In the OS Cairo program, `local is_reverted` is set to `1` by the hint.
4. `check_is_reverted(1)` returns immediately — no constraint added.
5. The `if (is_reverted == FALSE)` branch is not taken; `non_reverting_select_execute_entry_point_func` is never called.
6. `charge_fee` executes, deducting the fee from the user's account.
7. The Cairo proof is generated and verified on L1 — it is valid.
8. The state update is applied: user's fee balance is reduced, but the `transfer` never executed. Recipient receives nothing. User loses funds with no recourse. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo (L20-22)
```text
func check_is_reverted(is_reverted: felt) {
    return ();
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L111-135)
```text
func charge_fee{
    range_check_ptr,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    outputs: OsCarriedOutputs*,
}(block_context: BlockContext*, tx_execution_context: ExecutionContext*) {
    alloc_locals;

    local tx_info: TxInfo* = tx_execution_context.execution_info.tx_info;
    let max_fee = compute_max_possible_fee(tx_info=tx_info);

    if (max_fee == 0) {
        return ();
    }

    local low_actual_fee;
    %{ LoadActualFee %}
    local calldata: TransferCallData = TransferCallData(
        recipient=block_context.block_info_for_execute.sequencer_address,
        amount=Uint256(low=low_actual_fee, high=0),
    );

    // Verify that the charged amount is not larger than the transaction's max_fee field.
    assert_nn_le(calldata.amount.low, max_fee);
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L383-391)
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
