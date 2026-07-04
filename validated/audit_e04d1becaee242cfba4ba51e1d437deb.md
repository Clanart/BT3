### Title
Unchecked Fee Arithmetic Overflow in `compute_max_possible_fee` Causes Unprovable Blocks — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` multiplies `max_amount` (bounded to `[0, 2^64)`) by `max_price_per_unit` (bounded only to `[0, 2^128)` via `assert_nn`) without enforcing that the product fits within `[0, 2^128)`. The result is then passed to `assert_nn_le` in `charge_fee`, which requires both its arguments to be in `[0, 2^128)` via the range-check builtin. When the computed `max_fee` exceeds `2^128`, the range-check constraint is unsatisfiable, making the block unprovable and halting the network.

---

### Finding Description

In `transaction_hash.cairo`, `pack_resource_bounds` validates:

- `max_amount ≤ 2^64 - 1` via `assert_nn_le`
- `max_price_per_unit ≥ 0` via `assert_nn` — **no upper bound is enforced** [1](#0-0) 

`assert_nn` in Cairo uses the range-check builtin, which constrains values to `[0, 2^128)`. So `max_price_per_unit` can legally be up to `2^128 - 1`.

In `transaction_impls.cairo`, `compute_max_possible_fee` then computes:

```
max_fee = max_amount_L1 * max_price_L1
        + max_amount_L2 * (max_price_L2 + tip)
        + max_amount_L1data * max_price_L1data
``` [2](#0-1) 

With `max_amount` up to `2^64 - 1` and `max_price_per_unit` up to `2^128 - 1`, a single term can reach `(2^64 - 1) × (2^128 - 1) ≈ 2^192`. The sum of three such terms can reach `≈ 2^193`, which is a valid felt value (below the Stark prime `P ≈ 2^251`) but far exceeds `2^128`.

`charge_fee` then calls:

```cairo
assert_nn_le(calldata.amount.low, max_fee);
``` [3](#0-2) 

`assert_nn_le(a, b)` is implemented as `assert_nn(a)` followed by `assert_nn(b - a)`. Both calls write to the range-check builtin, which requires values in `[0, 2^128)`. When `max_fee > 2^128`, the term `max_fee - actual_fee` (where `actual_fee` is a normal token amount) exceeds `2^128`, making the range-check constraint unsatisfiable. The OS proof for the block cannot be generated.

The inconsistency is structural: the transaction-hash path (`pack_resource_bounds`) accepts `max_price_per_unit` up to `2^128 - 1`, but the block-execution path (`charge_fee`) implicitly requires `max_fee ≤ 2^128 - 1`. No check bridges this gap.

---

### Impact Explanation

**High — Network not being able to confirm new transactions (total network shutdown).**

If a block containing a transaction with `max_price_per_unit` large enough to push `max_fee` above `2^128` is submitted to the prover, the OS Cairo program will fail to satisfy the range-check constraint in `charge_fee`. The resulting proof is invalid and cannot be accepted. The sequencer must discard the block and reprocess it, and if the root cause is not identified, repeated inclusion of such transactions can stall block production indefinitely.

---

### Likelihood Explanation

**Medium.**

The StarkNet transaction specification defines `max_price_per_unit` as a `u128` field, so values up to `2^128 - 1` are protocol-legal. The OS itself only enforces `assert_nn` (i.e., `≥ 0`), not an upper bound that would prevent the product from exceeding `2^128`. An unprivileged user can craft a V3 transaction with:

- `max_amount = 2^64 - 1` (maximum allowed)
- `max_price_per_unit = 2^65` (legal per `assert_nn`, since `2^65 < 2^128`)

This yields `max_fee ≈ 2^129 > 2^128`. If the sequencer's mempool does not independently enforce a tighter bound on `max_price_per_unit` (which the OS program does not mandate), the transaction passes hash validation and is included in a block, triggering the failure.

---

### Recommendation

1. In `compute_max_possible_fee`, add an explicit `assert_nn_le(max_fee, MAX_FEE_BOUND)` where `MAX_FEE_BOUND = 2^128 - 1` after computing the sum, or use `Uint256` arithmetic for the fee computation.
2. Alternatively, enforce a tighter upper bound on `max_price_per_unit` in `pack_resource_bounds` (e.g., `assert_nn_le(max_price_per_unit, MAX_PRICE_BOUND)`) such that `max_amount * max_price_per_unit` is guaranteed to fit in `[0, 2^128)`.

---

### Proof of Concept

**Attacker-controlled inputs (V3 invoke transaction):**

```
resource_bounds[L1_GAS] = {
    max_amount:          2^64 - 1,   // passes assert_nn_le(..., 2^64 - 1)
    max_price_per_unit:  2^65,       // passes assert_nn (2^65 < 2^128)
}
resource_bounds[L2_GAS] = { max_amount: 0, max_price_per_unit: 0 }
resource_bounds[L1_DATA_GAS] = { max_amount: 0, max_price_per_unit: 0 }
tip = 0
```

**Execution trace in the OS:**

1. `hash_fee_fields` → `pack_resource_bounds`: `assert_nn_le(2^64 - 1, 2^64 - 1)` ✓, `assert_nn(2^65)` ✓. Transaction hash computed successfully.
2. `compute_max_possible_fee`: returns `(2^64 - 1) × 2^65 ≈ 2^129`.
3. `charge_fee`: `assert_nn_le(actual_fee, 2^129)` → internally evaluates `assert_nn(2^129 - actual_fee)`. Since `2^129 - actual_fee > 2^128`, the range-check builtin constraint is violated.
4. The OS proof is unsatisfiable. The block cannot be finalized. [4](#0-3) [5](#0-4) [1](#0-0)

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
