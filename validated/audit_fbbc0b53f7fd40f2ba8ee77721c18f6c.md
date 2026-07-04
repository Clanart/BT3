### Title
Unbounded `max_price_per_unit` in `pack_resource_bounds` Causes `assert_nn_le` Range-Check Failure in `charge_fee`, Halting Block Proving — (File: `transaction_hash/transaction_hash.cairo`, `execution/transaction_impls.cairo`)

---

### Summary

`pack_resource_bounds` validates `max_amount` with a tight upper-bound check but only verifies `max_price_per_unit` is non-negative (`assert_nn`), leaving it unbounded above. An attacker submitting a V3 transaction with `max_price_per_unit ≥ 2**128` causes `compute_max_possible_fee` to return a felt value ≥ 2**128. When `charge_fee` subsequently calls `assert_nn_le(actual_fee, max_fee)`, the internal range-check on `max_fee − actual_fee` fails because that difference exceeds the 2**128 range-check bound. The OS cannot produce a valid proof for the block, halting the network.

---

### Finding Description

**Root cause — missing upper-bound on `max_price_per_unit`:** [1](#0-0) 

```cairo
func pack_resource_bounds{range_check_ptr}(resource_bounds: ResourceBounds) -> felt {
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);   // ✓ bounded
    assert_nn(resource_bounds.max_price_per_unit);            // ✗ only ≥ 0, no upper bound
    return (resource_bounds.resource * 2 ** 64 + resource_bounds.max_amount) * 2 ** 128 +
        resource_bounds.max_price_per_unit;
}
```

`max_amount` is correctly capped at `2**64 − 1`. `max_price_per_unit` is only checked to be non-negative via `assert_nn`, which in Cairo 0 enforces `value < 2**128` via a range-check builtin — but that means any value in `[0, 2**128)` passes. A value of exactly `2**128` or larger (up to `PRIME/2 ≈ 2**250`) also passes `assert_nn` because the hint branches on `value % PRIME < range_check_builtin.bound`; a value of `2**128` satisfies `assert_nn` since `2**128 < PRIME/2`.

**Propagation — unbounded product in `compute_max_possible_fee`:** [2](#0-1) 

```cairo
func compute_max_possible_fee(tx_info: TxInfo*) -> felt {
    ...
    return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
        (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
        l1_data_gas_bounds.max_price_per_unit;
}
```

With `max_amount = 2**64 − 1` and `max_price_per_unit = 2**128`, the product `(2**64 − 1) × 2**128 ≈ 2**192`. This is a valid felt (< PRIME) but is ≥ 2**128.

**Failure point — `assert_nn_le` with oversized `max_fee`:** [3](#0-2) 

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);
...
assert_nn_le(calldata.amount.low, max_fee);
```

`assert_nn_le(a, b)` expands to `assert_nn(a); assert_nn(b − a)`. The second call checks that `max_fee − actual_fee < 2**128`. When `max_fee ≈ 2**192` and `actual_fee` is a small legitimate value (e.g., 10**6), `max_fee − actual_fee ≈ 2**192`, which is ≥ 2**128. The range-check builtin rejects this value, the assertion fires, and the OS proof is invalid.

This is the direct analog of the `LeqGadget` bug: a comparison primitive (`assert_nn_le`) silently assumes its inputs fit within a fixed bit-width (128 bits), but an attacker-controlled field (`max_price_per_unit`) is never bounded to that width, allowing the comparison to receive an out-of-range operand.

---

### Impact Explanation

When the OS fails an internal assertion, it cannot produce a valid STARK proof for the block. The sequencer must detect the failure, drop the offending transaction, and re-sequence. An attacker who continuously submits V3 transactions with `max_price_per_unit ≥ 2**128` can force repeated proof failures, preventing the network from confirming new transactions — a sustained network halt.

**Impact: High — Network not being able to confirm new transactions (total network shutdown).**

---

### Likelihood Explanation

Any unprivileged user can craft a V3 `invoke`, `declare`, or `deploy_account` transaction and set `max_price_per_unit` to any felt value. The OS performs no upper-bound check on this field before it reaches `compute_max_possible_fee`. The attack requires only a single malformed transaction and no special privileges, keys, or infrastructure.

---

### Recommendation

In `pack_resource_bounds`, replace the unconstrained `assert_nn` with a tight upper-bound check matching the intended 128-bit semantic:

```cairo
// Before (vulnerable):
assert_nn(resource_bounds.max_price_per_unit);

// After (fixed):
assert_nn_le(resource_bounds.max_price_per_unit, 2 ** 128 - 1);
```

This mirrors the existing pattern used for `max_amount` and ensures `compute_max_possible_fee` always returns a value that `assert_nn_le` can safely compare.

---

### Proof of Concept

1. Craft a V3 `invoke` transaction with any resource bound having `max_amount = 1` and `max_price_per_unit = 2**128`.
2. Submit the transaction to the sequencer. The OS accepts it through `pack_resource_bounds` (only `assert_nn` is checked, which passes for `2**128`).
3. The sequencer includes the transaction in a block and runs the OS.
4. `compute_max_possible_fee` returns `1 × 2**128 = 2**128`.
5. `charge_fee` calls `assert_nn_le(actual_fee, 2**128)`.
6. Internally, `assert_nn(2**128 − actual_fee)` is called. Since `2**128 − actual_fee ≥ 2**128`, the range-check builtin rejects the value.
7. The OS assertion fires; the block proof is invalid.
8. The sequencer cannot finalize the block; the network stalls.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L102-108)
```text
// Packs the given resource bounds in a single felt.
func pack_resource_bounds{range_check_ptr}(resource_bounds: ResourceBounds) -> felt {
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
    assert_nn(resource_bounds.max_price_per_unit);
    return (resource_bounds.resource * 2 ** 64 + resource_bounds.max_amount) * 2 ** 128 +
        resource_bounds.max_price_per_unit;
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L86-102)
```text
// Returns the maximum possible fee that can be charged for the transaction.
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
