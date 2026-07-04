### Title
Hardcoded Static Gas Limit for L1 Handler Execution Causes Permanent Freezing of Bridged Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

### Summary
`execute_l1_handler_transaction` assigns a hardcoded constant `L1_HANDLER_L2_GAS_MAX_AMOUNT = 100,000,000` as the gas budget for every L1 handler execution, regardless of the actual computational requirements of the handler. Unlike account transactions (invoke, deploy_account, declare), which derive their gas budget dynamically from the user-specified `resource_bounds[L2_GAS_INDEX].max_amount`, L1 handlers have no mechanism for the L1 message sender to specify a gas budget. Any L1 handler requiring more than 100M gas can never execute, permanently freezing any funds locked in the corresponding L1→L2 message.

### Finding Description

In `execute_l1_handler_transaction`, after consuming the L1-to-L2 message, the OS assigns a fixed gas budget:

```cairo
// Consume L1-to-L2 message.
consume_l1_to_l2_message(execution_context=tx_execution_context, nonce=nonce);
let remaining_gas = L1_HANDLER_L2_GAS_MAX_AMOUNT;
non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
    block_context=block_context, execution_context=tx_execution_context
);
``` [1](#0-0) 

The constant is defined as:

```cairo
const L1_HANDLER_L2_GAS_MAX_AMOUNT = 100000000;
``` [2](#0-1) 

This is 11× lower than `EXECUTE_MAX_SIERRA_GAS = 1,100,000,000` used for regular account transaction execute phases: [3](#0-2) 

For account transactions, the gas budget is derived dynamically from the user's declared resource bounds:

```cairo
func get_initial_user_gas_bound(common_tx_fields: CommonTxFields*) -> felt {
    assert common_tx_fields.n_resource_bounds = 3;
    return common_tx_fields.resource_bounds[L2_GAS_INDEX].max_amount;
}
``` [4](#0-3) 

No equivalent mechanism exists for L1 handlers. The L1 message sender on L1 has no way to specify an L2 gas budget.

Critically, `non_reverting_select_execute_entry_point_func` asserts `is_reverted = 0`:

```cairo
assert is_reverted = 0;
``` [5](#0-4) 

If the L1 handler runs out of gas, the execution reverts, and this assertion fails, making the entire block proof invalid. The sequencer is therefore forced to pre-mark such transactions as `is_reverted = TRUE` via the `IsReverted` hint. However, `check_is_reverted` performs no actual constraint:

```cairo
func check_is_reverted(is_reverted: felt) {
    return ();
}
``` [6](#0-5) 

When `is_reverted != FALSE`, the function returns early **before** `consume_l1_to_l2_message` is called: [7](#0-6) 

This means the L1 message is never consumed, the handler never executes, and any funds locked in the L1 message (e.g., ETH deposited via a bridge) are permanently frozen with no recovery path.

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any L1→L2 bridge deposit or message that triggers an L1 handler requiring more than 100M L2 gas will be permanently unexecutable. The L1 message is never consumed, the L2 state is never updated, and the funds locked in the L1 bridge contract cannot be recovered. There is no retry mechanism or gas-bump mechanism available to the L1 message sender.

### Likelihood Explanation

**Medium.** A malicious or inadvertent L2 contract deployer can deploy a contract whose L1 handler performs complex operations (multiple storage writes, cross-contract calls, cryptographic operations) exceeding 100M gas. Users who subsequently send funds through an L1 bridge to that contract will have their funds permanently frozen. The 100M gas cap is 11× lower than the execute cap for regular transactions, making it plausible that non-trivial L1 handlers exceed it. The attacker role (contract deployer) is explicitly in scope.

### Recommendation

Replace the hardcoded `L1_HANDLER_L2_GAS_MAX_AMOUNT` with a dynamic gas amount derived from the L1 message itself (e.g., a gas field embedded in the L1→L2 message payload or a protocol-level field in the `MessageToL2Header`). This mirrors how account transactions use `resource_bounds[L2_GAS_INDEX].max_amount` to let the sender specify their gas budget. At minimum, the cap should be raised to match `EXECUTE_MAX_SIERRA_GAS` to eliminate the asymmetry between L1 handler and regular execute gas limits.

### Proof of Concept

1. Deploy an L2 contract with an `@l1_handler` function that performs operations consuming >100M L2 gas (e.g., 10+ storage writes + cross-contract calls).
2. From L1, call the bridge/core contract to send an L1→L2 message targeting that contract, locking 1 ETH in the L1 bridge.
3. The sequencer attempts to include the L1 handler transaction. Since the handler requires >100M gas, execution would revert, causing `assert is_reverted = 0` to fail and invalidating the block proof.
4. The sequencer is forced to mark `is_reverted = TRUE`, causing `execute_l1_handler_transaction` to return early before `consume_l1_to_l2_message`.
5. The L1 message is never consumed. The 1 ETH remains locked in the L1 bridge with no recovery path. The message cannot be retried with a higher gas limit because no such mechanism exists in the protocol.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L75-78)
```text
func get_initial_user_gas_bound(common_tx_fields: CommonTxFields*) -> felt {
    assert common_tx_fields.n_resource_bounds = 3;
    return common_tx_fields.resource_bounds[L2_GAS_INDEX].max_amount;
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L384-390)
```text
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants.cairo (L19-19)
```text
const L1_HANDLER_L2_GAS_MAX_AMOUNT = 100000000;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants.cairo (L90-92)
```text
const VALIDATE_MAX_SIERRA_GAS = 100000000;
const EXECUTE_MAX_SIERRA_GAS = 1100000000;
const DEFAULT_INITIAL_GAS_COST_NO_L2 = VALIDATE_MAX_SIERRA_GAS + EXECUTE_MAX_SIERRA_GAS;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L195-195)
```text
    assert is_reverted = 0;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo (L20-22)
```text
func check_is_reverted(is_reverted: felt) {
    return ();
}
```
