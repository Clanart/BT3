### Title
Unchecked Felt Arithmetic in `compute_max_possible_fee` Causes `assert_nn_le` Failure, Breaking Block Proving — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` performs raw felt arithmetic on user-controlled resource-bound fields without bounding the result. The product of `max_amount` (up to 2^64−1) and `max_price_per_unit` (up to 2^128−1) can exceed 2^129. The result is immediately passed to `assert_nn_le`, which internally calls `assert_nn(b − a)` — a range-check that requires the difference to be in [0, 2^128). When the computed fee exceeds 2^129, no valid `calldata.amount.low` exists that satisfies the range check, causing the OS Cairo program to abort. Because Cairo has no exception handling, a single such transaction causes the entire block proof to fail, halting the network.

---

### Finding Description

`compute_max_possible_fee` is defined as:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit
     + l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip)
     + l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit;
``` [1](#0-0) 

The individual field constraints, enforced in `pack_resource_bounds`, are:

- `max_amount ≤ 2^64 − 1` (via `assert_nn_le`)
- `max_price_per_unit < 2^128` (via `assert_nn`, which uses the range-check builtin) [2](#0-1) 

These per-field checks are correct in isolation, but no check is placed on the **product** or the **sum of products**. The maximum reachable value is approximately:

```
(2^64 − 1) × (2^128 − 1) × 3 ≈ 3 × 2^192
```

This is far below the Stark prime (≈ 2^251), so no felt-level overflow occurs — the result is a valid field element. However, it is far above 2^128, the implicit bound required by the subsequent range check.

The result is then used in `charge_fee`:

```cairo
assert_nn_le(calldata.amount.low, max_fee);
``` [3](#0-2) 

`assert_nn_le(a, b)` decomposes to:
1. `assert_nn(a)` — requires `a < 2^128`
2. `assert_nn(b − a)` — requires `b − a < 2^128`

`calldata.amount.low` is the low 128-bit limb of a `Uint256`, so it is always `< 2^128`. Condition 1 always passes. Condition 2 requires `max_fee − calldata.amount.low < 2^128`. Since `calldata.amount.low < 2^128`, if `max_fee ≥ 2^129`, then:

```
max_fee − calldata.amount.low ≥ 2^129 − (2^128 − 1) = 2^128 + 1
```

This exceeds the range-check bound, so `assert_nn` fails unconditionally — no value of `calldata.amount.low` can satisfy it.

---

### Impact Explanation

**High — Network not being able to confirm new transactions (total network shutdown).**

The OS Cairo program has no exception handling. An `assert` failure aborts the entire program. `charge_fee` is called for every V3 transaction after execution (including reverted ones): [4](#0-3) 

If a single transaction in a block triggers the failure, the OS cannot produce a valid STARK proof for that block. The sequencer's off-chain execution engine does not run the OS Cairo program — it uses a separate simulation path — so the sequencer may not detect the issue before committing the block. The block is then permanently unprovable, halting the chain.

---

### Likelihood Explanation

Any unprivileged user can submit a V3 transaction. The triggering condition requires only that the sum of `max_amount × max_price_per_unit` across resource bounds exceeds 2^129. A minimal example:

- `l1_gas_max_amount = 3`, `l1_gas_max_price_per_unit = 2^128 − 1`
- All other bounds = 0

This gives `compute_max_possible_fee = 3 × (2^128 − 1) > 2^129`. Both values are within the individually validated ranges (`max_amount ≤ 2^64 − 1`, `max_price_per_unit < 2^128`), so the transaction passes all pre-OS validation. The attack requires no special privilege, no leaked key, and no coordination.

---

### Recommendation

Bound the **result** of `compute_max_possible_fee` to fit within the range-check domain, or replace `assert_nn_le` with a comparison that handles values up to the full felt range. Concretely:

1. **Tighten input bounds**: In `pack_resource_bounds`, add `assert_nn_le(resource_bounds.max_price_per_unit, MAX_PRICE_BOUND)` where `MAX_PRICE_BOUND` is chosen so that `max_amount × MAX_PRICE_BOUND × 3 < 2^128`. For example, `MAX_PRICE_BOUND = 2^62` keeps the total below 2^128.

2. **Or use a Uint256 comparison**: Compute the fee as a `Uint256` and compare using `uint256_le`, which handles values up to 2^256.

3. **Or add a post-computation range check**: After `compute_max_possible_fee`, assert `assert_nn(max_fee)` (i.e., `max_fee < 2^128`) and revert the transaction gracefully if it fails, rather than aborting the OS.

---

### Proof of Concept

Submit a V3 transaction with the following resource bounds:

```
l1_gas:      max_amount = 3,  max_price_per_unit = 2^128 − 1
l2_gas:      max_amount = 0,  max_price_per_unit = 0
l1_data_gas: max_amount = 0,  max_price_per_unit = 0
tip = 0
```

Both `3 ≤ 2^64 − 1` and `2^128 − 1 < 2^128` pass all per-field validations in `pack_resource_bounds` and `hash_fee_fields`. [2](#0-1) 

`compute_max_possible_fee` returns `3 × (2^128 − 1) = 3 × 2^128 − 3 > 2^129`. [5](#0-4) 

In `charge_fee`, `assert_nn_le(calldata.amount.low, 3×2^128−3)` is called. For any `calldata.amount.low < 2^128`:

```
(3×2^128 − 3) − calldata.amount.low ≥ 3×2^128 − 3 − (2^128 − 1) = 2×2^128 − 2 > 2^128
```

`assert_nn` fails. The OS aborts. The block is unprovable. The network halts. [6](#0-5)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L360-362)
```text
    // Charge fee.
    charge_fee(block_context=block_context, tx_execution_context=updated_tx_execution_context);

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
