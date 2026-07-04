### Title
Missing Upper Bound on `max_price_per_unit` Enables Felt Arithmetic Overflow in Fee Computation — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo`)

---

### Summary

The `pack_resource_bounds` function validates `max_amount` with a tight upper bound (`<= 2^64 - 1`) but only checks `max_price_per_unit` for non-negativity (`assert_nn`), leaving it unbounded up to `P/2 ≈ 2^250`. Because `compute_max_possible_fee` performs raw felt arithmetic on these values, a crafted transaction can cause `max_fee` to be either `0` (fee bypass) or a felt value exceeding `2^128` (causing `assert_nn_le` to fail and the block to become unprovable).

---

### Finding Description

**Root cause — `pack_resource_bounds` in `transaction_hash.cairo`:** [1](#0-0) 

```cairo
func pack_resource_bounds{range_check_ptr}(resource_bounds: ResourceBounds) -> felt {
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);   // ✓ bounded
    assert_nn(resource_bounds.max_price_per_unit);            // ✗ only >= 0, no upper bound
    ...
}
```

`max_amount` is correctly capped at `2^64 - 1`. `max_price_per_unit` is only required to be non-negative, so any value in `[0, P/2]` (where `P ≈ 2^251`) passes the check.

**Overflow propagates into fee computation — `compute_max_possible_fee` in `transaction_impls.cairo`:** [2](#0-1) 

All arithmetic is modular (felt, mod P). With `max_amount = 1` and `max_price_per_unit = P/2`, the product is `P/2` — a felt value far exceeding `2^128`.

**Assertion failure in `charge_fee`:** [3](#0-2) 

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);
if (max_fee == 0) { return (); }
...
assert_nn_le(calldata.amount.low, max_fee);
```

`assert_nn_le(a, b)` internally checks that `b - a` lies in `[0, 2^128)`. If `max_fee = P/2 ≈ 2^250`, then `max_fee - actual_fee` is `≈ 2^250` for any reasonable `actual_fee`, which is **not** in `[0, 2^128)`. The range-check constraint is violated, the Cairo proof is invalid, and the block cannot be finalized.

**Two concrete exploit variants:**

| Variant | Crafted values | `max_fee` result | Effect |
|---|---|---|---|
| Network halt | `max_amount=1`, `max_price_per_unit=P/2` | `P/2 > 2^128` | `assert_nn_le` fails → block unprovable |
| Fee bypass | Three bounds with `max_price_per_unit` summing to `P` (e.g., each `≈ P/3 < P/2`) | `0` | Fee skipped entirely |

---

### Impact Explanation

**High — Network not being able to confirm new transactions (total network shutdown).**

If a block containing a transaction with `max_price_per_unit > 2^128` is submitted to the OS prover, the `assert_nn_le` constraint in `charge_fee` is unsatisfiable for any value of `actual_fee` the sequencer could supply. The block proof cannot be generated. Because the sequencer has no OS-level guard preventing inclusion of such a transaction, a single crafted transaction can render an entire block unprovable, halting the network.

---

### Likelihood Explanation

**Low.** The sequencer's gateway layer would typically reject transactions with unreasonably large `max_price_per_unit` values before they reach the OS. However, the OS itself — the authoritative protocol layer — imposes no such bound. Any gap or misconfiguration in the sequencer's off-chain validation leaves the OS exposed. The attack requires only a single malformed transaction to be included in a block.

---

### Recommendation

Add an explicit upper-bound check on `max_price_per_unit` in `pack_resource_bounds`, consistent with the bound already applied to `max_amount`:

```cairo
func pack_resource_bounds{range_check_ptr}(resource_bounds: ResourceBounds) -> felt {
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
    assert_nn_le(resource_bounds.max_price_per_unit, 2 ** 128 - 1);  // ADD THIS
    return (resource_bounds.resource * 2 ** 64 + resource_bounds.max_amount) * 2 ** 128 +
        resource_bounds.max_price_per_unit;
}
```

This ensures that all products in `compute_max_possible_fee` remain within the safe felt range and that `assert_nn_le(actual_fee, max_fee)` is always satisfiable.

---

### Proof of Concept

1. Attacker constructs a v3 invoke transaction with:
   - `l1_gas_bounds.max_amount = 1`, `l1_gas_bounds.max_price_per_unit = (P-1)/2`
   - `l2_gas_bounds.max_amount = 0`
   - `l1_data_gas_bounds.max_amount = 0`
   - `tip = 0`

2. `assert_nn((P-1)/2)` passes — value is exactly at the `P/2` boundary.

3. `compute_max_possible_fee` returns `(P-1)/2 ≈ 2^250`.

4. `charge_fee` reaches `assert_nn_le(actual_fee, (P-1)/2)`.

5. For any `actual_fee` (including `0`), `(P-1)/2 - actual_fee ≈ 2^250 >> 2^128` — the range-check constraint is violated.

6. The Cairo proof for the block is invalid. The block cannot be finalized. The network halts on this block.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L103-107)
```text
func pack_resource_bounds{range_check_ptr}(resource_bounds: ResourceBounds) -> felt {
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
    assert_nn(resource_bounds.max_price_per_unit);
    return (resource_bounds.resource * 2 ** 64 + resource_bounds.max_amount) * 2 ** 128 +
        resource_bounds.max_price_per_unit;
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L122-135)
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
