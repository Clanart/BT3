### Title
Hardcoded `L1_HANDLER_L2_GAS_MAX_AMOUNT` Gas Budget for L1 Handler Transactions Not Validated Against Configurable Block Gas Limit — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

The StarkNet OS hardcodes the gas budget for every L1 handler transaction to `L1_HANDLER_L2_GAS_MAX_AMOUNT = 100,000,000` in `constants.cairo`. This value is used unconditionally in `execute_l1_handler_transaction` without any validation against the block's configurable L2 gas limit. If the protocol's per-block L2 gas limit is configured to a value smaller than `L1_HANDLER_L2_GAS_MAX_AMOUNT`, every L1 handler transaction will exceed the block gas budget, making it impossible to process any L1→L2 message. This permanently freezes funds sent from L1 to L2.

---

### Finding Description

In `constants.cairo`, the gas budget for L1 handler transactions is hardcoded: [1](#0-0) 

This constant is then used directly and unconditionally in `execute_l1_handler_transaction`: [2](#0-1) 

Unlike regular invoke transactions — where the gas is derived from the user-supplied `resource_bounds[L2_GAS_INDEX].max_amount` and then capped by `cap_remaining_gas` against `VALIDATE_MAX_SIERRA_GAS` / `EXECUTE_MAX_SIERRA_GAS` — L1 handler transactions receive a flat, hardcoded gas allocation with no reference to any block-level configurable gas limit: [3](#0-2) 

The `execute_l1_handler_transaction` function is called for every `L1_HANDLER` transaction type in the block: [4](#0-3) 

The `BlockContext` struct does not carry a configurable per-block L2 gas limit field that the OS enforces against the L1 handler's gas allocation: [5](#0-4) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

L1→L2 messages are processed exclusively through L1 handler transactions. If the protocol's block L2 gas limit is configured (or reduced) to a value below `L1_HANDLER_L2_GAS_MAX_AMOUNT = 100,000,000`, the OS will unconditionally assign `100,000,000` gas to every L1 handler, causing every such transaction to exceed the block gas budget. No L1→L2 message can ever be processed. Funds sent from L1 to L2 via the messaging bridge are permanently frozen, as the L1 contract has already consumed the ETH/STRK and the L2 handler can never execute.

---

### Likelihood Explanation

The block L2 gas limit is a configurable protocol parameter. A protocol upgrade, governance action, or misconfiguration that reduces the block L2 gas limit below `100,000,000` (e.g., to reduce block times or resource usage) would silently trigger this condition. The hardcoded value in the OS is baked into the program hash and cannot be changed without an OS upgrade, creating a window where the configurable limit and the hardcoded OS constant are out of sync.

---

### Recommendation

Replace the hardcoded `L1_HANDLER_L2_GAS_MAX_AMOUNT` constant with a value read from the `BlockContext` (e.g., a `max_l2_gas_per_l1_handler` field), so that the gas budget for L1 handler transactions is always consistent with the block's configurable L2 gas limit. This mirrors the fix applied in the GMX report: make the parameter configurable rather than hardcoded.

---

### Proof of Concept

1. The protocol configures the block L2 gas limit to `X < 100,000,000` (e.g., `50,000,000`).
2. An L1 user sends a message to L2 via the StarkNet core contract on L1.
3. The sequencer attempts to include the resulting L1 handler transaction in a block.
4. The OS executes `execute_l1_handler_transaction`, which sets `remaining_gas = L1_HANDLER_L2_GAS_MAX_AMOUNT = 100,000,000`.
5. This exceeds the block's configured L2 gas limit of `50,000,000`.
6. The block is invalid; the sequencer cannot include the L1 handler.
7. All L1→L2 messages are permanently stuck. The user's funds on L1 have already been consumed by the L1 contract; the L2 handler never executes.

The root cause is at:
- `constants.cairo` line 19: `const L1_HANDLER_L2_GAS_MAX_AMOUNT = 100000000;`
- `transaction_impls.cairo` line 445: `let remaining_gas = L1_HANDLER_L2_GAS_MAX_AMOUNT;` [6](#0-5) [7](#0-6)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants.cairo (L18-19)
```text
const L1_HANDLER_VERSION = 0;
const L1_HANDLER_L2_GAS_MAX_AMOUNT = 100000000;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L322-332)
```text
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L444-448)
```text
    consume_l1_to_l2_message(execution_context=tx_execution_context, nonce=nonce);
    let remaining_gas = L1_HANDLER_L2_GAS_MAX_AMOUNT;
    non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
        block_context=block_context, execution_context=tx_execution_context
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transactions_inner.cairo (L45-49)
```text
    if (tx_type == 'L1_HANDLER') {
        // Handle the L1-handler transaction.
        execute_l1_handler_transaction(block_context=block_context);
        %{ ExitTx %}
        return execute_transactions_inner(block_context=block_context, n_txs=n_txs - 1);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/block_context.cairo (L42-50)
```text
struct BlockContext {
    os_global_context: OsGlobalContext,
    // Information about the block.
    block_info_for_execute: BlockInfo*,
    // A version of `block_info` that will be returned by the 'get_execution_info'
    // syscall during '__validate__'.
    // Some of the fields, which cannot be used in validate mode, are zeroed out.
    block_info_for_validate: BlockInfo*,
}
```
