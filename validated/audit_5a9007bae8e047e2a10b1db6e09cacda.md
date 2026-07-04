Now I have enough context. Let me look at the constants and the `entry_point_utils` to complete the picture.

### Title
Fee Token Transfer Return Value Not Verified in `charge_fee` — Silently Bypasses Fee Collection - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

The `charge_fee` function in the StarkNet OS calls the fee token's `transfer` entry point via `non_reverting_select_execute_entry_point_func` but **completely discards the return value**. The ERC20 `transfer` selector on StarkNet returns a success boolean. If the fee token's `transfer` returns `false` (a valid, non-reverting failure per the ERC20 standard), the OS never detects the failure and proceeds as if the fee was collected. This is the direct structural analog to H-07: just as that report showed that ignoring the return value of a token transfer breaks fee enforcement, here the OS-level fee charge silently succeeds even when the underlying token transfer fails.

---

### Finding Description

In `charge_fee`, after constructing the `ExecutionContext` targeting `TRANSFER_ENTRY_POINT_SELECTOR` on the fee token contract, the OS calls:

```cairo
non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
    block_context=block_context, execution_context=&execution_context
);
``` [1](#0-0) 

The function signature of `non_reverting_select_execute_entry_point_func` returns `(retdata_size: felt, retdata: felt*, is_deprecated: felt)`: [2](#0-1) 

All three return values are silently dropped. The OS only asserts `is_reverted = 0` (i.e., the call did not revert), but **never inspects `retdata[0]`** to confirm the transfer returned `true`.

Contrast this with every `__validate__`-family call in the same file, which explicitly checks the return value:

```cairo
if (is_deprecated == 0) {
    assert retdata_size = 1;
    assert retdata[0] = VALIDATED;
}
``` [3](#0-2) [4](#0-3) 

The `TRANSFER_ENTRY_POINT_SELECTOR` constant confirms the OS is calling the standard ERC20 `transfer` function: [5](#0-4) 

The `VALIDATED` constant shows the OS knows how to enforce return values when it chooses to: [6](#0-5) 

---

### Impact Explanation

**Critical. Direct loss of funds.**

If the fee token's `transfer` entry point returns `(false,)` — a valid, non-reverting ERC20 response indicating failure — the OS proceeds through `charge_fee` without collecting any fee. The transaction is fully executed and its state changes are committed, but no fee is transferred to the sequencer. Every transaction in a block could be processed for free if the fee token exhibits this behavior, draining sequencer revenue and breaking the economic security model of the protocol.

---

### Likelihood Explanation

The current canonical fee tokens (STRK, ETH) on StarkNet mainnet are well-implemented and revert on failure rather than returning `false`. However:

1. The OS protocol does not enforce that the fee token must revert on failure — it only enforces that the call does not revert (`is_reverted = 0`).
2. The `fee_token_address` is a configurable field in `StarknetOsConfig`. Any future fee token upgrade or alternative token that follows the ERC20 convention of returning `false` on failure (rather than reverting) would trigger this silently.
3. A subtle bug in the fee token contract (e.g., a storage exhaustion or balance underflow that returns `false` instead of panicking) would be invisible to the OS. [7](#0-6) 

---

### Recommendation

After calling `non_reverting_select_execute_entry_point_func` in `charge_fee`, capture and validate the return data, mirroring the pattern used for `__validate__`:

```cairo
let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func{
    remaining_gas=remaining_gas
}(block_context=block_context, execution_context=&execution_context);

// For non-deprecated fee tokens, assert the transfer returned true (1).
if (is_deprecated == 0) {
    assert retdata_size = 1;
    assert retdata[0] = 1;  // ERC20 transfer must return true.
}
```

This mirrors the existing `VALIDATED` check pattern and ensures the OS cannot be tricked by a fee token that returns `false` without reverting.

---

### Proof of Concept

1. Deploy a fee token contract whose `transfer` entry point always returns `(false,)` without reverting (valid ERC20 behavior).
2. Configure `fee_token_address` in `StarknetOsConfig` to point to this contract.
3. Submit any V3 invoke transaction with non-zero resource bounds.
4. The OS executes `charge_fee`:
   - Constructs `ExecutionContext` with `selector = TRANSFER_ENTRY_POINT_SELECTOR`.
   - Calls `non_reverting_select_execute_entry_point_func` — the fee token returns `(false,)`, `is_reverted = 0`.
   - The assertion `is_reverted = 0` passes.
   - Return values `(retdata_size=1, retdata=[0], is_deprecated=0)` are discarded.
5. The OS continues to `EndTx` with no fee collected.
6. The transaction's state changes are committed; the sequencer receives zero fee. [8](#0-7)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L111-165)
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

    // TODO(ilya, 01/01/2026): Consider caching the fee_token_class_hash.
    local fee_token_address = block_context.os_global_context.starknet_os_config.fee_token_address;
    let (fee_state_entry: StateEntry*) = dict_read{dict_ptr=contract_state_changes}(
        key=fee_token_address
    );
    let (__fp__, _) = get_fp_and_pc();
    // Use block_info directly from block_context, so that charge_fee will always run in
    // execute-mode rather than validate-mode.
    local execution_context: ExecutionContext = ExecutionContext(
        entry_point_type=ENTRY_POINT_TYPE_EXTERNAL,
        class_hash=fee_state_entry.class_hash,
        calldata_size=TransferCallData.SIZE,
        calldata=&calldata,
        execution_info=new ExecutionInfo(
            block_info=block_context.block_info_for_execute,
            tx_info=tx_info,
            caller_address=tx_info.account_contract_address,
            contract_address=fee_token_address,
            selector=TRANSFER_ENTRY_POINT_SELECTOR,
        ),
        deprecated_tx_info=tx_execution_context.deprecated_tx_info,
    );

    let remaining_gas = DEFAULT_INITIAL_GAS_COST;
    non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
        block_context=block_context, execution_context=&execution_context
    );
    return ();
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L677-684)
```text
        let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(
            block_context=block_context, execution_context=validate_deploy_execution_context
        );
    }
    if (is_deprecated == 0) {
        assert retdata_size = 1;
        assert retdata[0] = VALIDATED;
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L149-156)
```text
    let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(
        block_context=block_context, execution_context=validate_execution_context
    );
    if (is_deprecated == 0) {
        %{ CheckRetdataForDebug %}
        assert retdata_size = 1;
        assert retdata[0] = VALIDATED;
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L181-196)
```text
func non_reverting_select_execute_entry_point_func{
    range_check_ptr,
    remaining_gas: felt,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    outputs: OsCarriedOutputs*,
}(block_context: BlockContext*, execution_context: ExecutionContext*) -> (
    retdata_size: felt, retdata: felt*, is_deprecated: felt
) {
    let revert_log = init_revert_log();
    let (is_reverted, retdata_size, retdata, is_deprecated) = select_execute_entry_point_func{
        revert_log=revert_log
    }(block_context=block_context, execution_context=execution_context);
    assert is_reverted = 0;
    return (retdata_size, retdata, is_deprecated);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants.cairo (L49-52)
```text
// get_selector_from_name('transfer').
const TRANSFER_ENTRY_POINT_SELECTOR = (
    0x83afd3f4caedc6eebf44246fe54e38c95e3179a5ec9ea81740eca5b482d12e
);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants.cairo (L144-145)
```text
// The expected return value of the `__validate*__` functions of a Cairo 1.0 account contract.
const VALIDATED = 'VALID';
```
