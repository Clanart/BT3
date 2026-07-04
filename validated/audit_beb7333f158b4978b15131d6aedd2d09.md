### Title
Unbounded `max_fee` Arithmetic in `compute_max_possible_fee` Causes Unprovable Blocks — (File: `execution/transaction_impls.cairo`)

### Summary

`compute_max_possible_fee` multiplies `max_amount` (bounded to `[0, 2^64 - 1]`) by `max_price_per_unit` (bounded to `[0, 2^128 - 1]`) in plain felt arithmetic, producing a result up to `~3 × 2^192`. This value is then passed as the upper bound to `assert_nn_le`, which internally calls `assert_nn(max_fee - actual_fee)`. Because Cairo's range-check builtin only accepts values in `[0, 2^128 - 1]`, any `max_fee - actual_fee > 2^128 - 1` causes the range check to fail, making the block unprovable.

### Finding Description

In `charge_fee` (`transaction_impls.cairo`, lines 111–165), the OS:

1. Calls `compute_max_possible_fee` (lines 87–102) to derive `max_fee`:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
    (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
    l1_data_gas_bounds.max_price_per_unit;
```

2. Enforces the fee cap (line 135):

```cairo
assert_nn_le(calldata.amount.low, max_fee);
```

The individual field bounds are enforced during transaction-hash computation in `pack_resource_bounds` (lines 103–108):

```cairo
assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);   // max_amount ≤ 2^64 - 1
assert_nn(resource_bounds.max_price_per_unit);            // max_price_per_unit ≤ 2^128 - 1
```

and `tip` is bounded to `2^64 - 1` in `hash_fee_fields` (line 117):

```cairo
assert_nn_le(tip, 2 ** 64 - 1);
```

With these bounds, a single term `max_amount × max_price_per_unit` can reach `(2^64 - 1) × (2^128 - 1) ≈ 2^192`. The sum of three such terms reaches `≈ 3 × 2^192`, which is below the Stark prime (`P ≈ 2^251`) so no field-level wrap occurs — but it far exceeds `2^128 - 1`.

`assert_nn_le(a, b)` is implemented as `assert_nn(b - a)`, which writes `b - a` to the range-check cell. The range-check builtin rejects any value outside `[0, 2^128 - 1]`. When `max_fee ≈ 3 × 2^192` and `actual_fee` is a realistic value (e.g., `10^18` wei-equivalent), `max_fee - actual_fee ≈ 3 × 2^192 >> 2^128 - 1`, and the range check fails, aborting the proof.

### Impact Explanation

A block containing a transaction whose computed `max_fee` exceeds `2^128 - 1` cannot be proven by the OS. The sequencer must discard the block and rebuild it. If an attacker can repeatedly inject such transactions into blocks, the network cannot finalize new blocks — matching the **High: Network not being able to confirm new transactions** impact.

### Likelihood Explanation

An unprivileged user submits a V3 transaction with:
- `max_amount = 2^64 - 1` (maximum allowed by `pack_resource_bounds`)
- `max_price_per_unit = 2^128 - 1` (maximum allowed by `assert_nn`)

for all three resource types. The resulting `max_fee ≈ 3 × 2^192`. If the sequencer's off-chain mempool validation computes `max_fee` using 128-bit (truncating) arithmetic rather than arbitrary-precision arithmetic, it may compute a different (smaller) value and incorrectly accept the transaction. The OS then fails to prove the block. Likelihood is **medium**: it depends on whether the sequencer's validation independently catches the overflow before block inclusion.

### Recommendation

Add an explicit upper-bound check on `max_fee` before using it in `assert_nn_le`:

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);
// Ensure max_fee fits within the range-check builtin's domain.
assert_nn_le(max_fee, MAX_FEE_BOUND);  // e.g., MAX_FEE_BOUND = 2^128 - 1
assert_nn_le(calldata.amount.low, max_fee);
```

Alternatively, enforce a tighter upper bound on `max_price_per_unit` in `pack_resource_bounds` such that `max_amount × max_price_per_unit` is guaranteed to stay below `2^128 - 1`.

### Proof of Concept

1. Attacker constructs a V3 transaction with:
   - `l1_gas.max_amount = 2^64 - 1`, `l1_gas.max_price_per_unit = 2^128 - 1`
   - `l2_gas.max_amount = 2^64 - 1`, `l2_gas.max_price_per_unit = 2^128 - 1`
   - `l1_data_gas.max_amount = 2^64 - 1`, `l1_data_gas.max_price_per_unit = 2^128 - 1`
   - `tip = 0`

2. `pack_resource_bounds` passes: `assert_nn_le(2^64-1, 2^64-1)` ✓ and `assert_nn(2^128-1)` ✓.

3