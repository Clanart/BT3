### Title
`compute_max_possible_fee` Unchecked Felt Arithmetic Causes `assert_nn_le` Range-Check Failure in `charge_fee`, Enabling Network Halt — (File: `execution/transaction_impls.cairo`)

---

### Summary

The `compute_max_possible_fee` function computes the maximum fee from user-controlled resource bounds using unchecked felt arithmetic. For valid protocol-specified resource bounds (u64 `max_amount` × u128 `max_price_per_unit`), the result can exceed 2^128. The subsequent `assert_nn_le(calldata.amount.low, max_fee)` check in `charge_fee` relies on Cairo range checks bounded to [0, 2^128), which fail when `max_fee ≥ 2^128`, causing the OS to fail to generate a valid proof for any block containing such a transaction.

---

### Finding Description

In `charge_fee` at line 135 of `transaction_impls.cairo`:

```cairo
assert_nn_le(calldata.amount.low, max_fee);
```

`max_fee` is produced by `compute_max_possible_fee` (lines 87–102) using pure felt arithmetic with no overflow guard:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
    (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
    l1_data_gas_bounds.max_price_per_unit;
``` [1](#0-0) 

The StarkNet protocol specifies `max_amount` as u64 (up to 2^64 − 1) and `max_price_per_unit` as u128 (up to 2^128 − 1). Their product alone can reach 2^192, far exceeding 2^128. No range check is applied to the resource bounds when they are loaded from hints in `get_account_tx_common_fields`. [2](#0-1) 

Cairo's `assert_nn_le(a, b)` (imported from `starkware.cairo.common.math`) expands to:
1. `assert_nn(a)` — range-checks `a ∈ [0, 2^128)`
2. `assert_le(a, b)` — range-checks `b − a ∈ [0, 2^128)`

When `max_fee ≥ 2^128` and `actual_fee < 2^128`, the value `max_fee − actual_fee ≥ 2^128` cannot satisfy the range check, causing the OS to produce an invalid proof. [3](#0-2) 

The resource bounds are loaded without any range enforcement in the OS: [4](#0-3) 

---

### Impact Explanation

Any block containing a transaction where `compute_max_possible_fee ≥ 2^128` cannot be proven by the OS. The sequencer cannot finalize such a block. An attacker who repeatedly submits such transactions prevents the network from confirming any new transactions, constituting a **total network shutdown** (High impact per the allowed scope).

---

### Likelihood Explanation

An unprivileged transaction sender can craft a v3 transaction with resource bounds within the protocol-specified ranges (u64 `max_amount`, u128 `max_price_per_unit`) such that their product exceeds 2^128 — for example, `max_amount = 2^32`, `max_price_per_unit = 2^100` yields `max_fee = 2^132`. The OS code applies no range check

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L87-102)
```text
func compute_max_possible_fee(tx_info: TxInfo*) -> felt {
    tempvar resource_bounds: ResourceBounds* = tx_info.resource_bounds_start;
    let n_resource_bounds = (tx_info.resource_bounds_end - resource_bounds) / ResourceBounds.SIZE;

    // Only V3 transactions with all resource bounds are supported.
    assert tx_info.version = 3;
    assert n_resource_bounds = 3;

    tempvar l1_gas_bounds: ResourceBounds = resource_bounds[L1_GAS_INDEX];
    tempvar l2_gas_bounds: ResourceBounds = resource_bounds[L2_GAS_INDEX];
    tempvar l1_data_gas_bounds = resource_bounds[L1_DATA_GAS_INDEX];

    return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
        (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
        l1_data_gas_bounds.max_price_per_unit;
}
```

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L170-198)
```text
func get_account_tx_common_fields(
    block_context: BlockContext*, tx_hash_prefix: felt, sender_address: felt
) -> CommonTxFields* {
    alloc_locals;
    local resource_bounds: ResourceBounds*;
    local tip;
    local paymaster_data_length;
    local paymaster_data: felt*;
    local nonce_data_availability_mode;
    local fee_data_availability_mode;
    local nonce;
    %{ LoadCommonTxFields %}
    %{ LoadTxNonceAccount %}
    tempvar common_tx_fields = new CommonTxFields(
        tx_hash_prefix=tx_hash_prefix,
        version=3,
        sender_address=sender_address,
        chain_id=block_context.os_global_context.starknet_os_config.chain_id,
        nonce=nonce,
        tip=tip,
        n_resource_bounds=3,
        resource_bounds=resource_bounds,
        paymaster_data_length=paymaster_data_length,
        paymaster_data=paymaster_data,
        nonce_data_availability_mode=nonce_data_availability_mode,
        fee_data_availability_mode=fee_data_availability_mode,
    );
    return common_tx_fields;
}
```
