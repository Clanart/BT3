### Title
Unchecked Arithmetic Overflow in `compute_max_possible_fee` Causes Unprovable Blocks - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` multiplies user-controlled `max_amount` (bounded to `[0, 2^64)`) by `max_price_per_unit` (bounded to `[0, 2^128)`) without verifying the product fits within `[0, 2^128)`. The result is then passed to `assert_nn_le(actual_fee, max_fee)`, which internally calls `assert_nn(max_fee - actual_fee)`. Since `assert_nn` uses the range-check builtin (which only accepts values in `[0, 2^128)`), any `max_fee ≥ 2^128` causes an irrecoverable Cairo assertion failure. If the sequencer includes such a transaction, the block cannot be proven, halting the network.

---

### Finding Description

`compute_max_possible_fee` computes:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit +
    l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip) +
    l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit;
``` [1](#0-0) 

The upstream `pack_resource_bounds` (called during transaction hash computation) enforces:
- `assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1)` → `max_amount ∈ [0, 2^64)`
- `assert_nn(resource_bounds.max_price_per_unit)` → `max_price_per_unit ∈ [0, 2^128)` [2](#0-1) 

The product of these two bounds can reach `(2^64 - 1) × (2^128 - 1) ≈ 2^192`, far exceeding `2^128`. With three resource types plus tip, `max_fee` can reach `~3 × 2^192`.

`charge_fee` then calls:

```cairo
assert_nn_le(calldata.amount.low, max_fee);
``` [3](#0-2) 

`assert_nn_le(a, b)` is implemented as `assert_nn(a); assert_nn(b - a)`. The `assert_nn` builtin requires its argument to be in `[0, 2^128)`. When `max_fee ≥ 2^128 + actual_fee`, the expression `max_fee - actual_fee ≥ 2^128` fails the range-check builtin unconditionally — no value of `actual_fee` can rescue it.

---

### Impact Explanation

**High — Network not being able to confirm new transactions (total network shutdown).**

If the sequencer includes a transaction with `max_fee ≥ 2^128` in a block, the OS Cairo program fails at `assert_nn_le` during `charge_fee`. A failed Cairo execution means no valid STARK proof can be generated for that block. The sequencer is stuck: it cannot prove the block and cannot roll back without discarding the entire block. Repeated injection of such transactions can permanently stall block production.

---

### Likelihood Explanation

Any unprivileged user submitting a V3 transaction can trigger this. The attacker only needs to set, for example:
- `max_amount = 2` (passes `assert_nn_le(max_amount, 2^64 - 1)`)
- `max_price_per_unit = 2^127` (passes `assert_nn(max_price_per_unit)` since `2^127 < 2^128`)
- Product = `2 × 2^127 = 2^128` → `max_fee ≥ 2^128` → `assert_nn_le` fails

The only mitigation is whether the sequencer's off-chain mempool validation independently enforces `max_fee < 2^128` before including the transaction. The OS Cairo code itself has no such guard, meaning any sequencer that does not add this extra check out-of-band is vulnerable.

---

### Recommendation

Add an explicit upper-bound check on `max_fee` inside `compute_max_possible_fee` or immediately after it in `charge_fee`:

```cairo
// After computing max_fee:
assert_nn_le(max_fee, MAX_FEE_BOUND - 1);  // e.g., MAX_FEE_BOUND = 2**128
```

Alternatively, constrain `max_price_per_unit` to `[0, 2^64)` in `pack_resource_bounds` (matching `max_amount`), so the product is at most `(2^64)^2 = 2^128`, and the sum of three terms stays within `[0, 3 × 2^128)` — still requiring a sum-level check. The cleanest fix is to bound `max_price_per_unit` to `[0, 2^64 - 1]` via `assert_nn_le(resource_bounds.max_price_per_unit, 2 ** 64 - 1)`, ensuring the product never exceeds `2^128 - 1`.

---

### Proof of Concept

1. Attacker constructs a valid V3 `invoke` transaction with:
   - `resource_bounds[L1_GAS].max_amount = 2`
   - `resource_bounds[L1_GAS].max_price_per_unit = 2^127`
   - All other resource bounds set to 0.

2. `pack_resource_bounds` passes: `assert_nn_le(2, 2^64 - 1)` ✓ and `assert_nn(2^127)` ✓ (since `2^127 < 2^128`).

3. `compute_max_possible_fee` returns `2 × 2^127 = 2^128`.

4. In `charge_fee`, `assert_nn_le(actual_fee, 2^128)` executes `assert_nn(2^128 - actual_fee)`. Since `2^128 - actual_fee ≥ 2^128 - (2^128 - 1) = 1` but `2^128 - actual_fee` itself is `≥ 2^128` when `actual_fee = 0`, the range-check builtin rejects it (range-check requires value `< 2^128`).

5. The OS Cairo execution aborts. No valid STARK proof can be produced for the block. Network halts. [4](#0-3) [5](#0-4) [2](#0-1)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L120-135)
```text
    local tx_info: TxInfo* = tx_execution_context.execution_info.tx_info;
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
