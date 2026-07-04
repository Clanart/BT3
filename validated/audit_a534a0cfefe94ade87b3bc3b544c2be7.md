### Title
Unbounded `max_price_per_unit` Causes `compute_max_possible_fee` to Exceed Range-Check Bound, Breaking `charge_fee` Enforcement — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`pack_resource_bounds` enforces only a non-negativity check (`assert_nn`) on `max_price_per_unit`, allowing values up to `2^128 - 1`. When multiplied by `max_amount` (up to `2^64 - 1`), the product can reach `~2^192`. `compute_max_possible_fee` returns this unchecked sum as a plain felt. The subsequent `assert_nn_le(actual_fee, max_fee)` in `charge_fee` internally calls `assert_nn(max_fee - actual_fee)`, which requires its argument to be in `[0, 2^128)`. When `max_fee >= 2^128`, this range-check always fails regardless of what `actual_fee` the sequencer provides, aborting the entire OS execution and making it impossible to produce a valid block proof for any block containing such a transaction.

---

### Finding Description

**Root cause — missing upper bound on `max_price_per_unit`:**

In `pack_resource_bounds`, `max_amount` is tightly bounded:

```cairo
assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
assert_nn(resource_bounds.max_price_per_unit);          // only lower-bound: [0, 2^128)
``` [1](#0-0) 

`assert_nn` is a Cairo range-check primitive that only guarantees the value is non-negative (i.e., `< 2^128`). There is no upper bound tighter than `2^128 - 1` on `max_price_per_unit`.

**Overflow in `compute_max_possible_fee`:**

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
    (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
    l1_data_gas_bounds.max_price_per_unit;
``` [2](#0-1) 

With `max_amount = 2` and `max_price_per_unit = 2^127`, a single term evaluates to `2 × 2^127 = 2^128`. The result is a felt that is `>= 2^128`.

**Broken `assert_nn_le` in `charge_fee`:**

```cairo
assert_nn_le(calldata.amount.low, max_fee);
``` [3](#0-2) 

`assert_nn_le(a, b)` is implemented as `assert_nn(a); assert_nn(b - a)`. When `max_fee >= 2^128` and `actual_fee < max_fee`, the term `max_fee - actual_fee` is `>= 1`, but more critically, `assert_nn(max_fee - actual_fee)` requires the argument to be in `[0, 2^128)`. If `max_fee - actual_fee >= 2^128`, the range-check builtin rejects it. The only escape is `actual_fee = max_fee`, but `assert_nn(actual_fee)` then also fails when `actual_fee >= 2^128`. There is no valid `actual_fee` the sequencer hint `%{ LoadActualFee %}` can supply to satisfy the check. [4](#0-3) 

---

### Impact Explanation

**High — Network not being able to confirm new transactions (total network shutdown).**

`charge_fee` is called unconditionally after execution in `execute_invoke_function_transaction`, `execute_deploy_account_transaction`, and `execute_declare_transaction`. An assertion failure inside the OS Cairo program is not a per-transaction revert; it aborts the entire block proof generation. If the sequencer includes even one such crafted transaction in a block, the prover cannot produce a valid STARK proof for that block, halting the chain until the block is discarded and reprocessed.

---

### Likelihood Explanation

Any unprivileged V3 transaction sender can set `max_price_per_unit` to a value such that `max_amount × max_price_per_unit >= 2^128`. The threshold is trivially reachable: `max_amount = 2`, `max_price_per_unit = 2^127` suffices. The only mitigation is the sequencer's off-chain pre-validation, which is not enforced by the on-chain Cairo OS code. A mismatch between off-chain acceptance logic and on-chain Cairo bounds (both using `assert_nn` as the gate) makes it realistic that a crafted transaction passes mempool admission and reaches block inclusion.

---

### Recommendation

Add an explicit upper bound on `max_price_per_unit` in `pack_resource_bounds` that guarantees `max_amount × max_price_per_unit < 2^128` for any valid `max_amount`. For example:

```cairo
assert_nn_le(resource_bounds.max_price_per_unit, 2 ** 64 - 1);
```

This mirrors the bound already applied to `max_amount` and ensures the product of the two 64-bit values fits within 128 bits, keeping `compute_max_possible_fee` safely within the range-check domain used by `assert_nn_le` in `charge_fee`.

---

### Proof of Concept

1. Craft a V3 invoke transaction with the following resource bounds for any one gas type (e.g., L1 gas):
   - `max_amount = 2`
   - `max_price_per_unit = 2^127` (= `170141183460469231731687303715884105728`)
   - All other resource bounds set to `(max_amount=0, max_price_per_unit=0)`
   - `tip = 0`

2. `pack_resource_bounds` accepts `max_price_per_unit = 2^127` because `assert_nn(2^127)` passes (`2^127 < 2^128`).

3. `compute_max_possible_fee` returns `2 × 2^127 = 2^128`.

4. In `charge_fee`, `max_fee = 2^128`. The sequencer hint sets `low_actual_fee` to any value `v < 2^128`.
   - `assert_nn_le(v, 2^128)` → `assert_nn(v)` passes, `assert_nn(2^128 - v)` fails because `2^128 - v >= 1` and for `v = 0`, `assert_nn(2^128)` fails (out of range-check range).

5. The OS Cairo execution aborts. No valid STARK proof can be generated for the block containing this transaction.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L103-108)
```text
func pack_resource_bounds{range_check_ptr}(resource_bounds: ResourceBounds) -> felt {
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
    assert_nn(resource_bounds.max_price_per_unit);
    return (resource_bounds.resource * 2 ** 64 + resource_bounds.max_amount) * 2 ** 128 +
        resource_bounds.max_price_per_unit;
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L99-101)
```text
    return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
        (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
        l1_data_gas_bounds.max_price_per_unit;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L123-135)
```text
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
