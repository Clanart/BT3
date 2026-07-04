### Title
Missing Upper Bound on `max_price_per_unit` Causes Fee Arithmetic Overflow Leading to Unprovable Block — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo`)

---

### Summary

`pack_resource_bounds` enforces an upper bound on `max_amount` (`<= 2^64 - 1`) but only checks that `max_price_per_unit` is non-negative (`assert_nn`), with no upper bound. The StarkNet spec (SNIP-8) defines `max_price_per_unit` as a `u128`. When a transaction carries `max_price_per_unit > 2^128 - 1`, the unchecked felt arithmetic in `compute_max_possible_fee` can produce a `max_fee` value that exceeds `2^128 - 1`. The subsequent `assert_nn_le(actual_fee, max_fee)` call in `charge_fee` then fails the range_check builtin, making the block unprovable and halting the network.

---

### Finding Description

In `pack_resource_bounds`:

```cairo
func pack_resource_bounds{range_check_ptr}(resource_bounds: ResourceBounds) -> felt {
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);  // bounded ✓
    assert_nn(resource_bounds.max_price_per_unit);           // only >= 0, NO upper bound ✗
    return (resource_bounds.resource * 2 ** 64 + resource_bounds.max_amount) * 2 ** 128 +
        resource_bounds.max_price_per_unit;
}
```

`max_amount` is correctly bounded to `[0, 2^64 - 1]`. `max_price_per_unit` is only checked to be non-negative — any felt value up to `P - 1 ≈ 2^251` is accepted.

This value flows directly into `compute_max_possible_fee` via `tx_info.resource_bounds_start`:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
    (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
    l1_data_gas_bounds.max_price_per_unit;
```

All arithmetic is unchecked felt arithmetic (modulo P). With `max_price_per_unit = 2^200` and `max_amount = 1`, the product `1 * 2^200 = 2^200` is a felt value far exceeding `2^128 - 1`. The resulting `max_fee` is then passed to:

```cairo
assert_nn_le(calldata.amount.low, max_fee);
```

`assert_nn_le(a, b)` is implemented as `assert_nn(b - a)`, which uses the range_check builtin. The range_check builtin can only verify values in `[0, 2^128)`. If `max_fee > 2^128 - 1`, then `max_fee - actual_fee` (as a felt) also exceeds `2^128 - 1`, causing the range_check to fail. The block becomes unprovable.

---

### Impact Explanation

**High — Network not being able to confirm new transactions (total network shutdown).**

If a block contains a transaction with `max_price_per_unit > 2^128 - 1`, the OS Cairo program fails to produce a valid proof for that block. The block cannot be finalized on L1. Since the OS is the authoritative protocol-level validator and does not enforce the `u128` upper bound on `max_price_per_unit`, any sequencer that relies on the OS for fee field validation (rather than duplicating the check off-chain) will include such a transaction, triggering an unprovable block and halting the network.

---

### Likelihood Explanation

An unprivileged transaction sender can freely set `max_price_per_unit` to any felt value when constructing a v3 transaction. The OS's only check is `assert_nn` (non-negative). The sequencer's off-chain mempool validation is not part of the OS protocol and may not independently enforce the `u128` bound if it defers to the OS for fee field correctness. A single crafted transaction is sufficient to trigger the bug.

---

### Recommendation

In `pack_resource_bounds`, add an upper bound check on `max_price_per_unit` matching the spec-defined `u128` range:

```cairo
assert_nn_le(resource_bounds.max_price_per_unit, 2 ** 128 - 1);
```

This mirrors the existing pattern for `max_amount` and prevents felt arithmetic overflow in `compute_max_possible_fee`.

---

### Proof of Concept

1. Attacker constructs a v3 invoke transaction with:
   - `max_amount = 1` (for any resource, e.g., L1 gas)
   - `max_price_per_unit = 2^200` (a valid felt, passes `assert_nn`)
   - `tip = 0`

2. `pack_resource_bounds` accepts this: `assert_nn(2^200)` passes.

3. Sequencer includes the transaction in a block and calls the OS prover.

4. `compute_max_possible_fee` computes `1 * 2^200 = 2^200` (felt arithmetic, no wrap for this value since `2^200 < P`). `max_fee = 2^200 > 2^128 - 1`.

5. `charge_fee` calls `assert_nn_le(actual_fee, 2^200)`. Internally this calls `assert_nn(2^200 - actual_fee)`. Even with `actual_fee = 0`, `assert_nn(2^200)` requires the range_check builtin to verify `2^200 < 2^128`, which fails.

6. The OS cannot complete the proof. The block is unprovable. The network halts. [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L99-102)
```text
    return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
        (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
        l1_data_gas_bounds.max_price_per_unit;
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L134-135)
```text
    // Verify that the charged amount is not larger than the transaction's max_fee field.
    assert_nn_le(calldata.amount.low, max_fee);
```
