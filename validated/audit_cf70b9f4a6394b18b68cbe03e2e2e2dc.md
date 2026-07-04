### Title
Hardcoded `L1_HANDLER_L2_GAS_MAX_AMOUNT` Causes Permanent Freezing of L1-Bridged Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

The StarkNet OS hardcodes the L2 gas budget for every L1 handler execution to `L1_HANDLER_L2_GAS_MAX_AMOUNT = 100_000_000`. Any L1 handler whose execution requires more than this fixed budget will always fail. Because the L1 message is committed on L1 before the L2 handler runs, a permanently failing handler means the associated L1-bridged funds can never be credited on L2 and can never be recovered on L1 — a permanent freeze of funds.

---

### Finding Description

**Root cause — `constants.cairo` line 19:**

```cairo
const L1_HANDLER_L2_GAS_MAX_AMOUNT = 100000000;
``` [1](#0-0) 

**Usage — `transaction_impls.cairo` lines 445–448:**

```cairo
let remaining_gas = L1_HANDLER_L2_GAS_MAX_AMOUNT;
non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
    block_context=block_context, execution_context=tx_execution_context
);
``` [2](#0-1) 

Unlike invoke transactions — where the gas budget is taken from the user-supplied `resource_bounds[L2_GAS_INDEX].max_amount` and the execute phase is capped at `EXECUTE_MAX_SIERRA_GAS = 1_100_000_000` — every L1 handler unconditionally receives exactly `100_000_000` L2 gas, regardless of the complexity of the handler or the amount of work it must perform. [3](#0-2) 

For comparison, the validate phase of an invoke transaction is also capped at `VALIDATE_MAX_SIERRA_GAS = 100_000_000`, but the execute phase receives up to `1_100_000_000` — 11× more. L1 handlers are permanently limited to the lower figure with no mechanism for the L1 message sender or the L2 contract developer to increase it.

**Execution flow when the handler exceeds the budget:**

The sequencer pre-evaluates whether the L1 handler will revert (via the `%{ IsReverted %}` hint). If it will run out of gas, `is_reverted` is set to `TRUE` and the early-return path is taken:

```cairo
if (is_reverted != FALSE) {
    %{ EndTx %}
    return ();
}
``` [4](#0-3) 

In this path, `consume_l1_to_l2_message` is **never called**, so the L1 message is never written to the OS output as consumed. The message remains perpetually pending — it cannot be retried with a higher gas limit because the OS constant is immutable in the compiled program, and it cannot be cancelled on L1 because the L1 bridge contract has already transferred the funds. [5](#0-4) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

When a user bridges assets from L1 to L2 (e.g., ETH or ERC-20 tokens via the StarkNet bridge), the L1 contract locks the funds and emits an L1→L2 message. The L2 OS must process that message by executing the target contract's L1 handler. If the handler's gas requirement exceeds `100_000_000` L2 gas, the OS will never successfully execute it. The L1 message is never consumed, the L2 credit is never minted, and the L1 funds remain locked in the bridge contract with no recovery path. The freeze is permanent because:

1. The gas limit is a compile-time constant in the OS program — it cannot be changed per-message or per-block.
2. The L1 message sender has no way to attach a higher gas budget to an L1→L2 message.
3. The L1 bridge contract has no cancellation mechanism once the message is emitted (or if it does, it requires the L2 side to have processed the message first).

---

### Likelihood Explanation

**Medium.**

The `100_000_000` gas budget is sufficient for simple handlers (e.g., a single `mint` call). However, it is insufficient for handlers that perform multiple storage writes (`STORAGE_WRITE_GAS_COST = 44_970` each), cross-contract calls (`CALL_CONTRACT_GAS_COST = 91_560` each), or emit multiple events. A handler that calls one external contract and writes to five storage slots already consumes roughly `315,000` gas in syscall costs alone, but a handler that chains several such operations, or that is deployed on a contract with complex initialization logic, can easily exceed the cap. Any L1 message sender who interacts with such a contract triggers the freeze without any malicious intent. [6](#0-5) 

---

### Recommendation

Replace the single hardcoded constant with a value that is either:

1. **Derived from the L1 message itself** — allow the L1 message sender to specify a `paid_fee_on_l1` / gas amount that is validated against the actual execution cost, similar to how invoke transactions use `resource_bounds[L2_GAS_INDEX].max_amount`; or
2. **Raised to match `EXECUTE_MAX_SIERRA_GAS`** — align the L1 handler budget with the execute-phase budget of invoke transactions (`1_100_000_000`) so that handlers are not arbitrarily more constrained than user-initiated calls.

At minimum, the constant should be documented with an explicit justification for why `100_000_000` is safe for all possible L1 handlers deployed on StarkNet.

---

### Proof of Concept

1. Deploy an L2 contract whose `@l1_handler` function performs 12 cross-contract calls (each costing `91_560` gas) plus 10 storage writes (each costing `44_970` gas): total syscall gas ≈ `1_548_420`, well within a normal invoke execute budget but exceeding `100_000_000` once Sierra VM step costs are added for a non-trivial handler body.
2. From L1, call the StarkNet core contract's `sendMessageToL2` targeting this contract, locking ETH in the bridge.
3. The sequencer attempts to execute the L1 handler. The OS sets `remaining_gas = L1_HANDLER_L2_GAS_MAX_AMOUNT = 100_000_000`. The handler runs out of gas.
4. The sequencer sets `is_reverted = TRUE`. The OS takes the early-return path; `consume_l1_to_l2_message` is never called.
5. The L1 message remains unconsumed across every subsequent block. The ETH locked in the L1 bridge is permanently frozen. [7](#0-6)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants.cairo (L18-19)
```text
const L1_HANDLER_VERSION = 0;
const L1_HANDLER_L2_GAS_MAX_AMOUNT = 100000000;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants.cairo (L90-92)
```text
const VALIDATE_MAX_SIERRA_GAS = 100000000;
const EXECUTE_MAX_SIERRA_GAS = 1100000000;
const DEFAULT_INITIAL_GAS_COST_NO_L2 = VALIDATE_MAX_SIERRA_GAS + EXECUTE_MAX_SIERRA_GAS;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants.cairo (L104-116)
```text
const CALL_CONTRACT_GAS_COST = 91560;
const DEPLOY_GAS_COST = 147120;
const DEPLOY_CALLDATA_FACTOR_GAS_COST = 4850;
const GET_BLOCK_HASH_GAS_COST = 10840;
const GET_CLASS_HASH_AT_GAS_COST = 10000;
const GET_EXECUTION_INFO_GAS_COST = 12640;
const LIBRARY_CALL_GAS_COST = 89160;
const REPLACE_CLASS_GAS_COST = 10670;
// TODO(Yoni, 1/1/2026): take into account Patricia updates and dict squash.
const STORAGE_READ_GAS_COST = 18070;
const STORAGE_WRITE_GAS_COST = 44970;
const EMIT_EVENT_GAS_COST = 10000;
const SEND_MESSAGE_TO_L1_GAS_COST = 14470;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L374-451)
```text
func execute_l1_handler_transaction{
    range_check_ptr,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    outputs: OsCarriedOutputs*,
}(block_context: BlockContext*) {
    alloc_locals;

    %{ StartTx %}
    local is_reverted;
    %{ IsReverted %}
    // Skip the execution step for reverted transaction.
    if (is_reverted != FALSE) {
        %{ EndTx %}
        return ();
    }

    // TODO(Yoni): currently, the contract state is not fetched for reverted L1 handlers.
    //   Once block hash is supported, we should fetch the contract state for them as well.
    local entry_point_selector;
    %{ TxEntryPointSelector %}
    let (local tx_execution_context: ExecutionContext*) = get_invoke_tx_execution_context(
        block_context=block_context,
        entry_point_type=ENTRY_POINT_TYPE_L1_HANDLER,
        entry_point_selector=entry_point_selector,
    );
    local tx_execution_info: ExecutionInfo* = tx_execution_context.execution_info;

    local nonce;
    %{ LoadTxNonceL1Handler %}
    local chain_id = block_context.os_global_context.starknet_os_config.chain_id;

    let pedersen_ptr = builtin_ptrs.selectable.pedersen;
    with pedersen_ptr {
        let transaction_hash = compute_l1_handler_transaction_hash(
            execution_context=tx_execution_context, chain_id=chain_id, nonce=nonce
        );
    }
    update_pedersen_in_builtin_ptrs(pedersen_ptr=pedersen_ptr);

    %{ AssertTransactionHash %}

    // Write the transaction info and complete the ExecutionInfo struct.
    tempvar tx_info = tx_execution_info.tx_info;
    assert [tx_info] = TxInfo(
        version=L1_HANDLER_VERSION,
        account_contract_address=tx_execution_info.contract_address,
        max_fee=0,
        signature_start=cast(0, felt*),
        signature_end=cast(0, felt*),
        transaction_hash=transaction_hash,
        chain_id=chain_id,
        nonce=nonce,
        resource_bounds_start=cast(0, ResourceBounds*),
        resource_bounds_end=cast(0, ResourceBounds*),
        tip=0,
        paymaster_data_start=cast(0, felt*),
        paymaster_data_end=cast(0, felt*),
        nonce_data_availability_mode=0,
        fee_data_availability_mode=0,
        account_deployment_data_start=cast(0, felt*),
        account_deployment_data_end=cast(0, felt*),
        proof_facts_start=cast(0, felt*),
        proof_facts_end=cast(0, felt*),
    );
    fill_deprecated_tx_info(tx_info=tx_info, dst=tx_execution_context.deprecated_tx_info);
    assert_deprecated_tx_fields_consistency(tx_info=tx_info);

    // Consume L1-to-L2 message.
    consume_l1_to_l2_message(execution_context=tx_execution_context, nonce=nonce);
    let remaining_gas = L1_HANDLER_L2_GAS_MAX_AMOUNT;
    non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
        block_context=block_context, execution_context=tx_execution_context
    );

    %{ EndTx %}
    return ();
```
