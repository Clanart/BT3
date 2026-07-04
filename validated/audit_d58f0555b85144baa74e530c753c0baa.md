### Title
`check_is_reverted` Is a No-Op, Allowing Prover to Freely Set Transaction Revert Status - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo`)

---

### Summary

The `check_is_reverted` function in `execution_constraints.cairo` is an empty no-op. It is called in `execute_invoke_function_transaction` to validate the hint-supplied `is_reverted` flag, but imposes zero Cairo constraints on it. A malicious prover (sequencer) can freely set `is_reverted = TRUE` for any valid invoke transaction, causing the `__execute__` step to be skipped while the fee is still charged — a direct loss of funds for the user. Conversely, setting `is_reverted = FALSE` for a transaction that should be reverted causes incorrect state changes to be committed.

---

### Finding Description

In `execution_constraints.cairo`, the function meant to validate the revert status of a transaction is completely empty:

```cairo
func check_is_reverted(is_reverted: felt) {
    return ();
}
``` [1](#0-0) 

This function is called inside `execute_invoke_function_transaction` in `transaction_impls.cairo` immediately after the `is_reverted` value is loaded from a hint:

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
    ...
}
``` [2](#0-1) 

Because `check_is_reverted` imposes no Cairo constraint, the `is_reverted` value is entirely determined by the prover-supplied hint `%{ IsReverted %}` with no proof-level enforcement. In a ZK proof system, hints are non-deterministic inputs; the Cairo constraints are the only mechanism that can bind a hint value to a correct result. With an empty `check_is_reverted`, the proof system cannot distinguish a correctly-reverted transaction from one that was arbitrarily marked reverted by a malicious prover.

The same pattern exists for `execute_l1_handler_transaction`, where `is_reverted` is consumed directly from a hint with no validation call at all:

```cairo
local is_reverted;
%{ IsReverted %}
if (is_reverted != FALSE) {
    %{ EndTx %}
    return ();
}
``` [3](#0-2) 

For L1 handlers, a prover-forced `is_reverted = TRUE` causes the function to return before `consume_l1_to_l2_message` is called, silently dropping the L1-to-L2 message and any associated deposit. [4](#0-3) 

---

### Impact Explanation

**Direct loss of funds (Critical).**

For invoke transactions: a malicious prover sets `is_reverted = TRUE` for a user's valid transaction. The `__execute__` entry point is skipped, but `charge_fee` is still called unconditionally after the branch:

```cairo
// Charge fee.
charge_fee(block_context=block_context, tx_execution_context=updated_tx_execution_context);
``` [5](#0-4) 

The user pays the full fee but receives no execution. The state changes they intended are never applied. This constitutes a direct, provable loss of funds that is committed into a valid ZK proof — the verifier cannot detect it because the Cairo constraints do not enforce the correctness of `is_reverted`.

For L1 handlers: a prover sets `is_reverted = TRUE` for an L1-to-L2 message (e.g., an ETH deposit). The handler returns early, the message is never consumed, and the deposited funds are permanently lost on L2.

---

### Likelihood Explanation

The entry path requires a malicious sequencer/prover. In StarkNet's ZK proof model, the sequencer is the prover, and the entire purpose of the Cairo OS program is to constrain the sequencer's behavior so that even a malicious sequencer cannot produce a valid proof for an invalid state transition. The empty `check_is_reverted` breaks this guarantee for the revert-status dimension. Any invoke transaction submitted by any unprivileged user is subject to this manipulation. No special privilege beyond being the block producer is required, and the block producer role is the exact adversary the ZK proof is designed to constrain.

---

### Recommendation

`check_is_reverted` must impose a real Cairo constraint that ties the hint-supplied `is_reverted` value to a verifiable on-chain condition. The correct approach depends on the protocol's definition of reversion (e.g., insufficient gas after validate, explicit failure flag from `__validate__`), but at minimum the function must contain an assertion that makes it impossible to produce a valid proof with an incorrect `is_reverted` value. For example, if reversion is determined by remaining gas after validate:

```cairo
func check_is_reverted(is_reverted: felt, remaining_gas: felt, gas_threshold: felt) {
    if (is_reverted != FALSE) {
        assert_nn_le(remaining_gas, gas_threshold - 1);  // enforce gas exhaustion
    }
    return ();
}
```

Similarly, the L1 handler path must add an equivalent validation before branching on `is_reverted`.

---

### Proof of Concept

1. User submits a valid invoke transaction with sufficient gas and a correct signature.
2. The OS runs `__validate__` successfully (non-reverting, as enforced by `non_reverting_select_execute_entry_point_func`).
3. The prover supplies hint `IsReverted` → `is_reverted = 1` (TRUE).
4. `check_is_reverted(1)` is called — it returns immediately with no assertion.
5. The `if (is_reverted == FALSE)` branch is not taken; `__execute__` is skipped.
6. `charge_fee` runs and deducts the full fee from the user's account.
7. The OS produces a valid ZK proof for this block. The verifier accepts it.
8. On-chain, the user's fee is deducted and the transaction is recorded as reverted, but the intended state changes (e.g., a token transfer) never occurred.

The prover can repeat this for every invoke transaction in a block, draining fees from all users while executing nothing. [1](#0-0) [6](#0-5)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo (L20-22)
```text
func check_is_reverted(is_reverted: felt) {
    return ();
}
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L443-448)
```text
    // Consume L1-to-L2 message.
    consume_l1_to_l2_message(execution_context=tx_execution_context, nonce=nonce);
    let remaining_gas = L1_HANDLER_L2_GAS_MAX_AMOUNT;
    non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
        block_context=block_context, execution_context=tx_execution_context
    );
```
