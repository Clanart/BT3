### Title
Unchecked `max_fee` Range in `charge_fee` Causes OS Panic via `assert_nn_le` Width Mismatch — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` returns a `felt` that can reach ~`2^192`, but `charge_fee` passes it directly to `assert_nn_le(actual_fee, max_fee)`, which internally requires `max_fee − actual_fee ∈ [0, 2^128)`. When a user sets resource-bound fields to their allowed maxima, `max_fee` exceeds `2^128 − 1`, the range-check always fails, the OS panics, and the block proof cannot be produced.

---

### Finding Description

**Root cause — `compute_max_possible_fee` (lines 87–102):**

```cairo
func compute_max_possible_fee(tx_info: TxInfo*) -> felt {
    ...
    return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit
         + l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip)
         + l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit;
}
``` [1](#0-0) 

The only bounds enforced on the inputs come from `pack_resource_bounds` (called during transaction-hash computation):

```cairo
assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);   // max_amount ≤ 2^64 − 1
assert_nn(resource_bounds.max_price_per_unit);            // max_price_per_unit ∈ [0, 2^128)
``` [2](#0-1) 

`assert_nn` only guarantees the value is in `[0, 2^128)`. Therefore:

```
max_amount * max_price_per_unit ≤ (2^64 − 1) × (2^128 − 1) ≈ 2^192
```

The sum of three such products can reach `≈ 3 × 2^192`, which is well within the Stark prime (~`2^251`) so no field-element wrap occurs — but it is far above `2^128 − 1`.

**Failure site — `charge_fee` (lines 121–135):**

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);

if (max_fee == 0) { return (); }

local low_actual_fee;
%{ LoadActualFee %}
local calldata: TransferCallData = TransferCallData(
    recipient=...,
    amount=Uint256(low=low_actual_fee, high=0),   // actual_fee ∈ [0, 2^128)
);
assert_nn_le(calldata.amount.low, max_fee);        // ← PANICS when max_fee > 2^128 − 1
``` [3](#0-2) 

`assert_nn_le(a, b)` is implemented as:
1. `assert_nn(a)` → range-check `a ∈ [0, 2^128)`
2. `assert_nn(b − a)` → range-check `b − a ∈ [0, 2^128)`

When `max_fee ≈ 2^192` and `actual_fee ∈ [0, 2^128)`, the value `max_fee − actual_fee ≈ 2^192 − actual_fee` is far above `2^128 − 1`. The range-check builtin rejects it, the OS execution panics, and no valid proof can be generated for the block.

The sequencer cannot work around this: `actual_fee` is stored as `Uint256(low=actual_fee, high=0)`, so it is structurally bounded to `[0, 2^128)`. There is no value of `actual_fee` that makes `assert_nn_le` pass when `max_fee > 2^128 − 1`.

This is the direct analog of the external report's precision-loss pattern: a fixed-width comparison (`assert_nn_le` operating on `[0, 2^128)` differences) is applied to a value (`max_fee`) computed in a wider domain (`[0, ~2^192]`), producing an irrecoverable arithmetic failure instead of a correct accounting result.

---

### Impact Explanation

Every block that contains such a transaction fails to produce a valid STARK proof. The sequencer cannot finalize the block, and no new transactions can be confirmed until the offending transaction is identified and excluded. If the sequencer's mempool or block-building logic does not independently enforce `max_fee ≤ 2^128 − 1`, a single crafted transaction can repeatedly stall block production — matching the **High: Network not being able to confirm new transactions (total network shutdown)** impact category.

---

### Likelihood Explanation

Any unprivileged transaction sender can trigger this by setting:

```
l1_gas_bounds.max_price_per_unit = 2^128 − 1
l1_gas_bounds.max_amount        = 2^64  − 1
```

Both values pass every OS-level validation check (`assert_nn_le` for `max_amount`, `assert_nn` for `max_price_per_unit`, and the transaction-hash integrity check). The OS itself contains no guard that rejects `max_fee > 2^128 − 1` before reaching `charge_fee`. The attack requires no privileged access, no leaked key, and no external dependency.

---

### Recommendation

Add an explicit upper-bound check on `max_fee` immediately after it is computed in `charge_fee`, before the `assert_nn_le` call:

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);
// Guard: assert_nn_le requires max_fee ∈ [0, 2^128 − 1].
assert_nn_le(max_fee, 2 ** 128 - 1);   // reject oversized max_fee early
if (max_fee == 0) { return (); }
...
assert_nn_le(calldata.amount.low, max_fee);
```

Alternatively, enforce a tighter upper bound on `max_price_per_unit` inside `pack_resource_bounds` (e.g., `assert_nn_le(max_price_per_unit, MAX_PRICE_PER_UNIT)` where `MAX_PRICE_PER_UNIT` is chosen so that the total `max_fee` stays within `[0, 2^128 − 1]`).

---

### Proof of Concept

1. Craft a V3 invoke transaction with:
   - `l1_gas_bounds.max_amount = 2^64 − 1`
   - `l1_gas_bounds.max_price_per_unit = 2^128 − 1`
   - Other resource bounds set to any valid values.

2. The transaction passes `hash_fee_fields` / `pack_resource_bounds` validation (both bounds are within their checked ranges).

3. Inside `charge_fee`:
   ```
   max_fee = (2^64 − 1) × (2^128 − 1) + ... ≈ 2^192
   assert_nn_le(actual_fee, 2^192)
     → assert_nn(2^192 − actual_fee)
     → range_check(2^192 − actual_fee)   // 2^192 − actual_fee >> 2^128 − 1 → PANIC
   ```

4. The OS execution aborts; the block proof cannot be generated; the network halts until the transaction is manually excluded. [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L121-135)
```text
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
