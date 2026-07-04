### Title
Hardcoded `L1_HANDLER_L2_GAS_MAX_AMOUNT` Gas Ceiling Causes Permanent Fund Loss on L1→L2 Message Execution Failure - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants.cairo`)

---

### Summary

The StarkNet OS program uses a compile-time constant `L1_HANDLER_L2_GAS_MAX_AMOUNT = 100000000` as the sole gas budget for every L1 handler transaction. Because L1→L2 messages are written to the OS output (marked as consumed) **before** execution, any L1 handler that exhausts this fixed budget will have its message permanently consumed on L1 while the corresponding L2 state change never occurs — resulting in direct, irrecoverable loss of bridged funds.

---

### Finding Description

In `execute_l1_handler_transaction`, the OS unconditionally sets the gas budget for every L1 handler execution to the compile-time constant `L1_HANDLER_L2_GAS_MAX_AMOUNT`:

```cairo
// constants.cairo line 19
const L1_HANDLER_L2_GAS_MAX_AMOUNT = 100000000;

// transaction_impls.cairo lines 444-448
consume_l1_to_l2_message(execution_context=tx_execution_context, nonce=nonce);
let remaining_gas = L1_HANDLER_L2_GAS_MAX_AMOUNT;
non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
    block_context=block_context, execution_context=tx_execution_context
);
```

The critical ordering flaw is that `consume_l1_to_l2_message` (line 444) writes the L1→L2 message to `outputs.messages_to_l2` — permanently marking it as consumed from the L1 core contract's perspective — **before** the entry point is executed with the fixed gas budget (line 445). If the entry point runs out of gas, the OS continues (the function is `non_reverting`), but the L2 state change (e.g., minting bridged tokens) never happens.

Unlike regular invoke transactions, which derive their gas budget from the user-supplied `resource_bounds[L2_GAS_INDEX].max_amount` (see `get_initial_user_gas_bound`, `transaction_impls.cairo` line 77), L1 handler transactions have no per-message gas parameter. The 100M gas ceiling is a single global constant baked into the compiled OS program. It cannot be adjusted without deploying a new OS program version and updating the allowed program hash list.

For comparison, `EXECUTE_MAX_SIERRA_GAS = 1100000000` (1.1B) is the cap for regular execute calls — L1 handlers receive only ~9% of that budget. As the protocol evolves (new syscalls, increased per-operation costs, or complex handler contracts), this fixed ceiling becomes a permanent protocol-level constraint with no per-message override.

---

### Impact Explanation

**Critical — Direct loss of funds / Permanent freezing of funds.**

1. An L1 user sends ETH or ERC-20 tokens through a bridge contract on L1, which emits an L1→L2 message targeting an L2 handler.
2. The sequencer includes the L1 handler transaction. The OS calls `consume_l1_to_l2_message`, writing the message to the proven output. The L1 core contract will treat this message as consumed once the proof is verified.
3. The OS then calls `non_reverting_select_execute_entry_point_func` with `remaining_gas = L1_HANDLER_L2_GAS_MAX_AMOUNT`. If the handler requires more gas (complex logic, many storage writes, large calldata processing), it fails with `ERROR_OUT_OF_GAS`.
4. Because the function is non-reverting, the OS continues. The message is already in the output — the L1 contract cannot cancel it (cancellation requires the message to be unconsumed).
5. The bridged funds are permanently lost: the L1 deposit is consumed, but no L2 credit was issued.

The L1 message cancellation mechanism (`startL1ToL2MessageCancellation` / `cancelL1ToL2Message`) is unavailable because it only applies to messages not yet consumed. Once the OS proof is submitted and verified, the message is irrevocably consumed.

---

### Likelihood Explanation

**Medium-High.** The entry path is fully unprivileged: any L1 address can send an L1→L2 message. The likelihood increases over time because:

- Gas costs for individual operations (storage writes, syscalls) can increase with protocol upgrades, causing previously-safe handlers to exceed the ceiling.
- L1 handler contracts with complex logic (multi-step DeFi bridges, NFT minting with metadata, etc.) may legitimately require more than 100M gas units.
- The constant cannot be patched without a full OS upgrade, creating a window during which all affected L1 handler messages are silently consumed and lost.
- An attacker who identifies a bridge contract whose handler exceeds the gas limit can repeatedly trigger the message (or front-run legitimate users) to drain bridged funds.

---

### Recommendation

1. **Read the gas limit from the block context** rather than from a compile-time constant, so it can be updated via governance without an OS program upgrade.
2. **Reverse the execution order**: execute the entry point first, and only write the message to `outputs.messages_to_l2` if execution succeeds. If execution fails, leave the message unconsumed so the L1 sender can cancel it.
3. **Allow per-message gas specification**: include a `l2_gas_limit` field in the L1→L2 message header (analogous to `resource_bounds` in invoke transactions), so senders can specify the gas they are willing to pay for.

---

### Proof of Concept

**Root cause — hardcoded constant:** [1](#0-0) 

**Message consumed before execution (critical ordering):** [2](#0-1) 

**`consume_l1_to_l2_message` writes to outputs unconditionally:** [3](#0-2) 

**Contrast: regular invoke transactions use a user-supplied gas bound (not a hardcoded constant):** [4](#0-3) 

**Attack scenario:**

1. Deploy an L2 contract whose `@l1_handler` function performs ≥100M gas of work (e.g., 2,000+ storage writes at `STORAGE_WRITE_GAS_COST = 44970` each ≈ 89.9M gas, plus syscall overhead).
2. From L1, send a message to that contract with a token deposit.
3. The sequencer includes the L1 handler transaction. `consume_l1_to_l2_message` marks the message consumed in the proven output.
4. `non_reverting_select_execute_entry_point_func` runs out of gas and returns without minting tokens.
5. The proof is submitted to L1. The L1 core contract marks the message consumed. The deposited tokens are permanently lost.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants.cairo (L18-19)
```text
const L1_HANDLER_VERSION = 0;
const L1_HANDLER_L2_GAS_MAX_AMOUNT = 100000000;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L74-78)
```text
// Returns the transaction's initial gas derived from its resource bounds.
func get_initial_user_gas_bound(common_tx_fields: CommonTxFields*) -> felt {
    assert common_tx_fields.n_resource_bounds = 3;
    return common_tx_fields.resource_bounds[L2_GAS_INDEX].max_amount;
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
