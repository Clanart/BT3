### Title
Unchecked Product Overflow in `compute_max_possible_fee` Causes `assert_nn_le` to Always Fail, Halting Block Proof - (File: `execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` multiplies `max_amount` (bounded to `[0, 2^64)`) by `max_price_per_unit` (bounded to `[0, 2^128)`) without ensuring the product fits within `2^128`. The result is then passed to `assert_nn_le`, which only works correctly for values up to `2^128`. When a user submits a V3 transaction with large-but-protocol-valid resource bounds, `max_fee` can exceed `2^129`, making `assert_nn_le` unconditionally fail and causing the block proof to abort.

---

### Finding Description

In `compute_max_possible_fee`: [1](#0-0) 

The function computes:

```
max_fee = l1_gas.max_amount * l1_gas.max_price_per_unit
        + l2_gas.max_amount * (l2_gas.max_price_per_unit + tip)
        + l1_data_gas.max_amount * l1_data_gas.max_price_per_unit
```

The bounds enforced by `pack_resource_bounds` (called during transaction hash computation) are: [2](#0-1) 

- `max_amount ≤ 2^64 − 1` (via `assert_nn_le`)
- `max_price_per_unit ∈ [0, 2^128)` (via `assert_nn`, which only checks non-negativity and uses the range-check builtin to bound to `[0, 2^128)`)
- `tip ≤ 2^64 − 1` (via `assert_nn_le` in `hash_fee_fields`) [3](#0-2) 

A single term's maximum is `(2^64 − 1) × (2^128 − 1) ≈ 2^192`. The sum of three terms can reach `≈ 2^193`. Since the Cairo field prime `P ≈ 2^251`, this does **not** wrap in field arithmetic — the result is a genuine integer near `2^193`.

This value is then used in `charge_fee`: [4](#0-3) 

`assert_nn_le(a, b)` from `starkware.cairo.common.math` checks:
1. `a ∈ [0, 2^128)` — via `assert_nn(a)`
2. `b − a ∈ [0, 2^128)` — via `assert_nn(b − a)`

If `max_fee > 2^129`, there is **no** value of `actual_fee ∈ [0, 2^128)` that satisfies both constraints simultaneously:
- Constraint 1 requires `actual_fee < 2^128`
- Constraint 2 requires `actual_fee > max_fee − 2^128 > 2^128`

These are mutually exclusive. The assertion always fails, aborting the Cairo execution and invalidating the block proof.

The `ResourceBounds` struct confirms `max_amount` and `max_price_per_unit` are plain `felt` fields with no inherent type-level bound: [5](#0-4) 

---

### Impact Explanation

**High — Network not being able to confirm new transactions (total network shutdown).**

If a transaction with large resource bounds is included in a block, the OS Cairo program aborts at `assert_nn_le` inside `charge_fee`. A Cairo assertion failure means the STARK proof cannot be generated for that block. The sequencer must discard the block and retry without the offending transaction. If the sequencer's mempool pre-validation does not enforce `max_fee ≤ 2^128`, any such transaction reaching block execution causes a proof failure and a network stall.

---

### Likelihood Explanation

The StarkNet V3 transaction specification (SNIP-8) allows `max_price_per_unit` up to `2^128 − 1` and `max_amount` up to `2^64 − 1`. The OS code validates exactly these bounds in `pack_resource_bounds` but does not validate the **product**. A user can craft a transaction with:

- `max_amount = 2^64 − 1` (maximum allowed)
- `max_price_per_unit = 2^65` (well within `[0, 2^128)`, passes `assert_nn`)

This yields `max_fee ≈ 2^129`, which is sufficient to trigger the failure. The values are within the protocol-defined range, so the transaction passes signature validation and mempool acceptance checks that only verify individual field bounds. The bug is triggered at OS execution time, after the transaction is already committed to the block.

---

### Recommendation

1. **Add an upper bound on `max_price_per_unit`** in `pack_resource_bounds` to ensure the product `max_amount × max_price_per_unit` stays within `2^128`. For example, enforce `max_price_per_unit ≤ 2^64 − 1`, making the maximum product `≈ 2^128 − 1`.

2. **Alternatively**, replace `assert_nn_le(calldata.amount.low, max_fee)` in `charge_fee` with a proper 256-bit comparison using `Uint256` arithmetic, consistent with the `TransferCallData.amount` field which is already typed as `Uint256`.

---

### Proof of Concept

1. Attacker submits a V3 invoke transaction with:
   - `l1_gas_bounds.max_amount = 2^64 − 1`
   - `l1_gas_bounds.max_price_per_unit = 2^65`
   - Other resource bounds = 0

2. `pack_resource_bounds` validates: `max_amount ≤ 2^64 − 1` ✓, `assert_nn(2^65)` ✓ (since `2^65 < 2^128`)

3. `compute_max_possible_fee` returns `(2^64 − 1) × 2^65 ≈ 2^129`

4. `charge_fee` executes `assert_nn_le(actual_fee, 2^129)`:
   - `assert_nn(actual_fee)` requires `actual_fee < 2^128`
   - `assert_nn(2^129 − actual_fee)` requires `2^129 − actual_fee < 2^128`, i.e., `actual_fee > 2^128`
   - Both constraints cannot be satisfied simultaneously → assertion fails

5. Cairo execution aborts → block proof cannot be generated → network halt

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L127-135)
```text
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L110-117)
```text
func hash_fee_fields{range_check_ptr, poseidon_ptr: PoseidonBuiltin*}(
    tip: felt, resource_bounds: ResourceBounds*, n_resource_bounds: felt
) -> felt {
    alloc_locals;

    let (local data_to_hash: felt*) = alloc();
    assert data_to_hash[0] = tip;
    assert_nn_le(tip, 2 ** 64 - 1);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/common/new_syscalls.cairo (L55-62)
```text
struct ResourceBounds {
    // The name of the resource (e.g., 'L1_GAS').
    resource: felt,
    // The maximum amount of the resource allowed for usage during the execution.
    max_amount: felt,
    // The maximum price the user is willing to pay for the resource unit.
    max_price_per_unit: felt,
}
```
