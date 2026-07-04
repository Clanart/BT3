### Title
Unchecked `compute_max_possible_fee` Overflow Causes Unprovable Block via Broken `assert_nn_le` Comparison — (File: `execution/transaction_impls.cairo`)

### Summary

`compute_max_possible_fee` can return a felt value exceeding `2^128 - 1`. The subsequent `assert_nn_le(actual_fee, max_fee)` call in `charge_fee` internally requires `max_fee - actual_fee` to fit in a range-check cell (≤ `2^128 - 1`). When `max_fee > 2 * (2^128 - 1)`, no valid `actual_fee` satisfies both constraints simultaneously, making the block permanently unprovable.

### Finding Description

`compute_max_possible_fee` in `transaction_impls.cairo` computes:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
    (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
    l1_data_gas_bounds.max_price_per_unit;
``` [1](#0-0) 

The individual field bounds enforced by `pack_resource_bounds` (called during hash computation) are:
- `max_amount` ≤ `2^64 - 1` (via `assert_nn_le`)
- `max_price_per_unit` ≤ `2^128 - 1` (via `assert_nn`, which range-checks to `[0, 2^128-1]`)
- `tip` ≤ `2^64 - 1` (via `assert_nn_le`) [2](#0-1) 

Therefore `max_fee` can reach up to `3 × (2^64 - 1) × (2^128 - 1) ≈ 3 × 2^192`, far exceeding `2^128 - 1`. No overflow modulo the Cairo field prime occurs because `3 × 2^192 ≪ P ≈ 2^251`.

In `charge_fee`, the OS then executes:

```cairo
local low_actual_fee;
%{ LoadActualFee %}
local calldata: TransferCallData = TransferCallData(
    recipient=block_context.block_info_for_execute.sequencer_address,
    amount=Uint256(low=low_actual_fee, high=0),
);
assert_nn_le(calldata.amount.low, max_fee);
``` [3](#0-2) 

`assert_nn_le(a, b)` decomposes into:
1. `assert_nn(a)` → places `a` in a range-check cell, constraining `actual_fee ∈ [0, 2^128 - 1]`
2. `assert_le(a, b)` → places `b - a` in a range-check cell, requiring `max_fee - actual_fee ∈ [0, 2^128 - 1]`

For both constraints to hold simultaneously: `actual_fee ≥ max_fee - (2^128 - 1)` AND `actual_fee ≤ 2^128 - 1`.

When `max_fee > 2 × (2^128 - 1) = 2^129 - 2`, the lower bound exceeds the upper bound. **No valid `actual_fee` exists.** The proof cannot be generated.

### Impact Explanation

A block containing such a transaction becomes permanently unprovable. The sequencer cannot produce a valid STARK proof for that block, halting the network's ability to confirm new transactions. This matches the **High: Network not being able to confirm new transactions (total network shutdown)** impact category.

### Likelihood Explanation

An unprivileged transaction sender submits a V3 transaction with `max_amount = 3` and `max_price_per_unit = 2^128 - 1` for any one resource. Both values are individually within their validated ranges. The sequencer, seeing a transaction declaring a very large maximum fee, has no OS-level reason to reject it. When the sequencer sets `actual_fee` (capped at `2^128 - 1` as a `Uint256.low`), the `assert_le` range-check fails, making the block unprovable. The attack requires only a single crafted transaction and no privileged access.

### Recommendation

Add an explicit upper-bound check on `max_fee` before the `assert_nn_le` comparison in `charge_fee`, or change the comparison to use a proper `Uint256` comparison that does not rely on `max_fee` fitting in a single range-check cell. For example:

```cairo
// After computing max_fee, assert it fits in a range-checkable value:
assert_nn_le(max_fee, MAX_FEE_BOUND - 1);  // where MAX_FEE_BOUND <= 2^128
```

Alternatively, represent `max_fee` as a `Uint256` throughout and use `uint256_le` for the comparison.

### Proof of Concept

1. Craft a V3 transaction with:
   - `l1_gas_bounds.max_amount = 3`
   - `l1_gas_bounds.max_price_per_unit = 2^128 - 1`
   - All other resource bounds at 0
2. Both values pass `pack_resource_bounds` validation (3 ≤ 2^64 - 1; 2^128 - 1 passes `assert_nn`).
3. `compute_max_possible_fee` returns `3 × (2^128 - 1) = 3 × 2^128 - 3`.
4. In `charge_fee`, the sequencer sets `actual_fee = 2^128 - 1` (maximum allowed by `assert_nn`).
5. `assert_le(2^128 - 1, 3 × 2^128 - 3)` places `(3 × 2^128 - 3) - (2^128 - 1) = 2 × 2^128 - 2` into a range-check cell.
6. `2 × 2^128 - 2 > 2^128 - 1` → range-check fails → proof generation fails → block is unprovable → network halt. [4](#0-3)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L87-135)
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

// Charges a fee from the user.
// If max_fee is not 0, validates that the selector matches the entry point of an account contract
// and executes an ERC20 transfer on the behalf of that account contract.
//
// Arguments:
// block_context - a global context that is fixed throughout the block.
// tx_execution_context - The execution context of the transaction that pays the fee.
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L103-108)
```text
func pack_resource_bounds{range_check_ptr}(resource_bounds: ResourceBounds) -> felt {
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
    assert_nn(resource_bounds.max_price_per_unit);
    return (resource_bounds.resource * 2 ** 64 + resource_bounds.max_amount) * 2 ** 128 +
        resource_bounds.max_price_per_unit;
}
```
