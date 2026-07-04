### Title
Unchecked Felt Arithmetic in `compute_max_possible_fee` Causes `assert_nn_le` Failure in `charge_fee` — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` computes the maximum chargeable fee as a raw felt multiplication of `max_amount` (bounded to `[0, 2^64 - 1]`) and `max_price_per_unit` (bounded to `[0, 2^128 - 1]`). The product can reach ~`3 × 2^192`, far exceeding `2^128`. The result is then passed directly to `assert_nn_le` in `charge_fee`, which internally calls `assert_nn(max_fee - actual_fee)` — a range-check builtin call that requires its argument to be in `[0, 2^128)`. When `max_fee > 2^128`, this range check fails, aborting the entire OS Cairo execution and making the block unprovable.

---

### Finding Description

In `pack_resource_bounds` (called during transaction hash computation), the OS validates:

```cairo
assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);   // max_amount ∈ [0, 2^64 - 1]
assert_nn(resource_bounds.max_price_per_unit);            // max_price_per_unit ∈ [0, 2^128 - 1]
```

`assert_nn` uses the range-check builtin, bounding `max_price_per_unit` to `[0, 2^128 - 1]`. No upper bound is placed on the *product*.

`compute_max_possible_fee` then computes:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit
     + l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip)
     + l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit;
```

Maximum possible value: `(2^64 - 1) × (2^128 - 1) × 3 ≈ 3 × 2^192`, which is a valid felt (below the Stark prime `~2^251`) but far above `2^128`.

In `charge_fee`, the result is used as the upper bound in:

```cairo
assert_nn_le(calldata.amount.low, max_fee);
```

`assert_nn_le(a, b)` expands to `assert_nn(b - a)`, which writes `b - a` to the range-check builtin. The range-check builtin requires its input to be in `[0, 2^128)`. With `max_fee ≈ 3 × 2^192` and `actual_fee` being a normal small value (e.g., `~10^18 ≈ 2^60`), the value `max_fee - actual_fee ≈ 3 × 2^192 >> 2^128` fails the range check, aborting the entire OS program.

---

### Impact Explanation

A Cairo program abort means the STARK proof cannot be generated for the block. The sequencer cannot finalize the block, and no new transactions can be confirmed until the block is rebuilt without the offending transaction. If the sequencer's mempool admission logic does not independently enforce an upper bound on `max_price_per_unit`, a single crafted transaction can repeatedly stall block production — matching the **High: Network not being able to confirm new transactions** impact class.

---

### Likelihood Explanation

Any unprivileged transaction sender can submit a V3 transaction with `max_price_per_unit = 2^128 - 1` and `max_amount = 2^64 - 1` for any resource type. The OS Cairo code itself does not reject such values — `pack_resource_bounds` only checks `assert_nn(max_price_per_unit)`, which passes for any value in `[0, 2^128 - 1]`. The vulnerability is triggered if the sequencer includes the transaction without an independent off-chain bound check on the product. There is no on-chain/OS-level guard preventing inclusion.

---

### Recommendation

Add an explicit upper-bound check on `max_price_per_unit` in `pack_resource_bounds` (or equivalently in `compute_max_possible_fee`) to ensure the product `max_amount × max_price_per_unit` fits within `[0, 2^128)`:

```cairo
// Ensure max_price_per_unit is bounded so that max_amount * max_price_per_unit < 2^128.
// Since max_amount <= 2^64 - 1, bounding max_price_per_unit to 2^64 - 1 is sufficient.
assert_nn_le(resource_bounds.max_price_per_unit, 2 ** 64 - 1);
```

Alternatively, `compute_max_possible_fee` should use safe 256-bit arithmetic and `charge_fee` should use a comparison that handles values above `2^128` correctly.

---

### Proof of Concept

**Attacker-controlled entry path:**

1. Attacker submits a V3 invoke transaction with:
   - `l1_gas_bounds.max_amount = 2^64 - 1`
   - `l1_gas_bounds.max_price_per_unit = 2^128 - 1`
   - (other resource bounds set to any valid values)

2. Sequencer includes the transaction in a block.

3. OS executes `execute_invoke_transaction` → `compute_invoke_transaction_hash` → `pack_resource_bounds`:
   - `assert_nn_le(max_amount, 2^64 - 1)` ✓ passes
   - `assert_nn(max_price_per_unit)` ✓ passes (value is `2^128 - 1 < 2^128`)

4. OS executes `charge_fee` → `compute_max_possible_fee`:
   - Returns `(2^64 - 1) × (2^128 - 1) + ... ≈ 2^192`

5. OS executes `assert_nn_le(actual_fee, max_fee)`:
   - Internally: `assert_nn(2^192 - actual_fee)` where `2^192 - actual_fee >> 2^128`
   - **Range-check builtin fails** → OS program aborts → block is unprovable → network stalls.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L99-102)
```text
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
