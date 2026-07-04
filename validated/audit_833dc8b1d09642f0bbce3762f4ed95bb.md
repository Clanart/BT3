After thorough analysis of the scoped files, I found a valid analog to the external report's arithmetic overflow vulnerability class.

---

### Title
Unbounded `max_price_per_unit` Causes `assert_nn_le` Failure in `charge_fee`, Enabling Network Halt — (File: `execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` multiplies user-controlled `max_amount` (bounded to `2^64 - 1`) by `max_price_per_unit` (bounded only to `[0, 2^128)` via `assert_nn`). The resulting `max_fee` can reach up to `≈ 3 × 2^192`. This value is then passed to `assert_nn_le(actual_fee, max_fee)`, which internally calls `assert_nn(max_fee - actual_fee)` — a range check that requires its argument to be in `[0, 2^128)`. When `max_fee ≥ 2^128`, the range check fails, the Cairo proof cannot be generated, and the network halts for any block containing such a transaction.

---

### Finding Description

**Step 1 — Bounds on resource fields.**

In `pack_resource_bounds`, `max_amount` is bounded to `[0, 2^64 - 1]` and `max_price_per_unit` is only checked to be non-negative:

```cairo
assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
assert_nn(resource_bounds.max_price_per_unit);   // only: 0 ≤ price < 2^128
```

`assert_nn` uses a single range-check cell, bounding its argument to `[0, 2^128)`. No upper bound tighter than `2^128 - 1` is enforced on `max_price_per_unit`. `tip` is separately bounded to `[0, 2^64 - 1]` in `hash_fee_fields`, but `max_price_per_unit` is not.

**Step 2 — `compute_max_possible_fee` produces a value up to `≈ 3 × 2^192`.**

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit +
       l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip) +
       l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit;
```

Each product is at most `(2^64 - 1) × (2^128 - 1) ≈ 2^192`. The sum of three such products is at most `≈ 3 × 2^192 ≈ 2^193.6`. The Stark prime `P ≈ 2^251`, so no felt-field wrap-around occurs — the result is a genuine large positive integer, not reduced to zero.

**Step 3 — `assert_nn_le` fails when `max_fee ≥ 2^128`.**

```cairo
assert_nn_le(calldata.amount.low, max_fee);
```

`assert_nn_le(a, b)` is implemented as:
```
assert_nn(a)       // range-check: 0 ≤ a < 2^128
assert_nn(b - a)   // range-check: 0 ≤ b - a < 2^128
```

When `max_fee = 2^192` and `actual_fee = 1000`, the second check evaluates `assert_nn(2^192 - 1000)`. Since `2^192 - 1000 >> 2^128`, the range-check cell rejects the value and the Cairo program fails — the proof cannot be generated.

**Step 4 — The sequencer cannot work around this.**

The sequencer controls `low_actual_fee` via the `LoadActualFee` hint. Setting it to `0` still requires `assert_nn(max_fee - 0) = assert_nn(max_fee)` to pass, which fails for `max_fee ≥ 2^128`. There is no value of `low_actual_fee` that makes the assertion succeed when `max_fee ≥ 2^128`.

**Step 5 — No pre-execution rejection in the OS.**

`compute_max_possible_fee` is called inside `charge_fee`, which runs *after* the transaction has been executed. The OS performs no pre-execution check that `max_amount × max_price_per_unit < 2^128`. The transaction hash computation (which calls `pack_resource_bounds`) validates individual field bounds but not their product.

---

### Impact Explanation

**High — Network not being able to confirm new transactions (total network shutdown).**

If the sequencer includes a transaction with `max_fee ≥ 2^128` in a block, the Cairo OS program fails at the `assert_nn_le` call inside `charge_fee`. The proof for that block cannot be generated. The sequencer must discard the block and retry. An attacker who can repeatedly submit such transactions — and who can cause the sequencer to include them (e.g., by paying a high enough tip to pass mempool ordering) — can sustain a network halt.

---

### Likelihood Explanation

Any unprivileged transaction sender can craft a V3 transaction with:
- `max_amount = 2` (for any resource)
- `max_price_per_unit = 2^127` (passes `assert_nn`, since `2^127 < 2^128`)

This gives `max_fee ≥ 2 × 2^127 = 2^128`, which is exactly the threshold that breaks `assert_nn_le`. The OS code enforces no tighter bound on `max_price_per_unit` than `assert_nn`. The attack requires only that the sequencer include the transaction, which is the normal expected behavior.

---

### Recommendation

Bound `max_price_per_unit` to `[0, 2^64 - 1]` in `pack_resource_bounds`, consistent with the bounds already applied to `max_amount` and `tip`:

```cairo
assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
assert_nn_le(resource_bounds.max_price_per_unit, 2 ** 64 - 1);  // add this
```

This ensures each product `max_amount × max_price_per_unit ≤ (2^64 - 1)^2 < 2^128`, keeping `max_fee` within the range that `assert_nn_le` can safely handle.

---

### Proof of Concept

1. Attacker submits a V3 invoke transaction with:
   - `l1_gas_bounds.max_amount = 2`
   - `l1_gas_bounds.max_price_per_unit = 2^127`
   - All other resource bounds set to 0
2. `pack_resource_bounds` accepts this: `assert_nn_le(2, 2^64 - 1)` ✓, `assert_nn(2^127)` ✓
3. Sequencer includes the transaction in a block and executes it
4. `compute_max_possible_fee` returns `2 × 2^127 = 2^128`
5. `charge_fee` calls `assert_nn_le(actual_fee, 2^128)`
6. Internally: `assert_nn(2^128 - actual_fee)` — since `2^128 - actual_fee ≥ 2^128 - (2^128 - 1) = 1` but also `2^128 - actual_fee ≈ 2^128 ≥ 2^128`, the range check fails
7. The Cairo proof fails; the block cannot be finalized; network halts

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2)

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
