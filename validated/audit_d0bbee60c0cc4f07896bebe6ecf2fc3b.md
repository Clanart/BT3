### Title
Fee Evasion via Felt Arithmetic Overflow in `compute_max_possible_fee` — (File: `execution/transaction_impls.cairo`)

---

### Summary

The `compute_max_possible_fee` function in `transaction_impls.cairo` computes the maximum chargeable fee using raw felt arithmetic over user-controlled resource bound fields, with no range checks. A transaction sender can craft resource bounds whose product sum wraps to zero modulo the field prime, causing `charge_fee` to return early and skip the ERC-20 fee transfer entirely.

---

### Finding Description

`compute_max_possible_fee` is defined as:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
    (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
    l1_data_gas_bounds.max_price_per_unit;
``` [1](#0-0) 

All six operands (`max_amount`, `max_price_per_unit` for each of the three resource types) and `tip` are felt values loaded directly from hints with no `assert_nn_le` or `assert_nn` range checks anywhere in the call chain: [2](#0-1) 

The result is consumed immediately in `charge_fee`:

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);
if (max_fee == 0) {
    return ();
}
``` [3](#0-2) 

If `max_fee` evaluates to zero in felt arithmetic, the function returns immediately — the ERC-20 `transfer` call is never made, no fee is deducted from the sender, and no fee is credited to the sequencer.

The field prime is P = 2²⁵¹ + 17·2¹⁹² + 1. Because all multiplications are mod P, a user can choose felt values for the six resource-bound fields (all in [0, P)) such that:

```
A·B + C·(D + T) + E·F ≡ 0  (mod P)
```

For example, fix C, D, T, E, F freely and set `A = 1`, `B = P − (C·(D+T) + E·F) mod P`. Every value is a valid felt in [0, P). The transaction hash commits to these values; the user signs it. If the sequencer's mempool does not independently range-check resource bounds to [0, 2¹²⁸), the transaction is accepted and the OS skips fee charging.

---

### Impact Explanation

When `max_fee` overflows to 0, `charge_fee` exits before executing the ERC-20 transfer: [4](#0-3) 

The sequencer receives zero tokens for processing the transaction. Because the OS proof is still valid (the Cairo constraints are satisfied), the L1 verifier accepts the block. The sequencer suffers a direct, provable loss of fee revenue — **direct loss of funds** — with no on-chain recourse.

---

### Likelihood Explanation

- The user controls all six resource-bound felt fields and the tip; crafting an overflow combination is straightforward arithmetic.
- The OS imposes no range checks on these fields at any point in the execution path.
- Exploitation requires only that the sequencer's mempool does not independently validate resource bounds to uint128 range. Many sequencer implementations delegate this check to the OS, making the gap realistic.
- The attack is repeatable: every V3 transaction type (invoke, deploy-account, declare) passes through `compute_max_possible_fee` → `charge_fee`.

---

### Recommendation

Before computing the fee, range-check each resource bound field to [0, 2¹²⁸):

```cairo
assert_nn_le(l1_gas_bounds.max_amount, MAX_UINT128);
assert_nn_le(l1_gas_bounds.max_price_per_unit, MAX_UINT128);
// ... repeat for l2 and l1_data bounds, and tip
```

This ensures the felt multiplication cannot overflow the field prime and that `max_fee` faithfully represents the user's declared maximum.

---

### Proof of Concept

1. Choose `l2_gas_bounds.max_amount = 0`, `l1_data_gas_bounds.max_amount = 0`, `tip = 0`.
2. Set `l1_gas_bounds.max_amount = 1`, `l1_gas_bounds.max_price_per_unit = P` (≡ 0 mod P, but encoded as the felt `0` — trivial case) **or** choose any pair (A, B) with A·B ≡ P (mod P) = 0.
3. Sign and submit the V3 invoke transaction.
4. The OS computes `max_fee = 1·0 + 0 + 0 = 0`.
5. `charge_fee` hits `if (max_fee == 0) { return (); }` and exits.
6. No ERC-20 transfer occurs; the transaction executes for free. [5](#0-4)

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
