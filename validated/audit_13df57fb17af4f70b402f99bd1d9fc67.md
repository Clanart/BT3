### Title
Unbounded `compute_max_possible_fee` Arithmetic Breaks `assert_nn_le` Fee Validation, Rendering Blocks Unprovable — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` performs unchecked felt multiplication of resource-bound fields that are individually bounded but whose product can far exceed 2^128. The result is passed directly to `assert_nn_le`, a Cairo range-check primitive that requires both operands to fit in 128 bits. When `max_fee > 2^128`, the range check always fails for any valid `low_actual_fee`, making the block unprovable and halting the network.

---

### Finding Description

**Root cause — `pack_resource_bounds` under-constrains `max_price_per_unit`:**

In `transaction_hash.cairo`, `pack_resource_bounds` validates:

```cairo
assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);   // max_amount ∈ [0, 2^64)
assert_nn(resource_bounds.max_price_per_unit);             // max_price_per_unit ∈ [0, 2^128)
```

`assert_nn` places the value in the range-check builtin, bounding it to `[0, 2^128 - 1]`. `max_amount` is bounded to `[0, 2^64 - 1]`. The product of these two values can therefore reach `(2^64 - 1) × (2^128 - 1) ≈ 2^192`.

**Propagation — `compute_max_possible_fee` sums three such products:**

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit
     + l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip)
     + l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit;
```

All arithmetic is unchecked felt arithmetic. The returned `max_fee` can be up to `~3 × 2^192`, which is well below the field prime (~2^251) so no field overflow occurs, but it is orders of magnitude larger than 2^128.

**Failure point — `assert_nn_le` in `charge_fee`:**

```cairo
assert_nn_le(calldata.amount.low, max_fee);
```

Cairo's `assert_nn_le(a, b)` expands to:
1. `assert_nn(a)` → range-check that `a ∈ [0, 2^128)`
2. `assert_le(a, b)` → range-check that `b - a ∈ [0, 2^128)`

`low_actual_fee` is loaded from the hint `%{ LoadActualFee %}` and is constrained to `[0, 2^128)` by step 1. When `max_fee > 2^128`, the subtraction `max_fee - low_actual_fee` exceeds 2^128 for every valid `low_actual_fee`, causing step 2's range check to fail unconditionally. The OS cannot produce a valid proof for the block.

**The `high` field is hardcoded to zero:**

```cairo
amount=Uint256(low=low_actual_fee, high=0),
```

This means the actual fee transferred is always in `[0, 2^128)`, but the ceiling `max_fee` against which it is checked can be `~2^192`. The mismatch between the domain of `low_actual_fee` and the domain of `max_fee` is the structural flaw.

---

### Impact Explanation

**High — Network not being able to confirm new transactions (total network shutdown).**

If a block contains a V3 transaction whose resource bounds produce `max_fee > 2^128`, the OS Cairo program fails at the `assert_nn_le` range check inside `charge_fee`. Because the OS is the prover's execution environment, a failed assertion means the block cannot be proven. No subsequent block can be built on top of an unproven block, halting the network's ability to confirm new transactions.

---

### Likelihood Explanation

A user submitting a V3 transaction controls `max_amount` and `max_price_per_unit` for each resource type. Setting `max_amount = 2` and `max_price_per_unit = 2^128 - 1` for any single resource type yields:

```
max_fee ≥ 2 × (2^128 - 1) = 2^129 - 2 > 2^128
```

This is a trivially constructable transaction. The OS itself imposes no upper bound on `max_fee`; the only gate is the sequencer's off-chain mempool validation. A sequencer implementation that does not independently enforce `max_fee ≤ 2^128` before including a transaction will produce an unprovable block. Because the OS is the authoritative specification of validity, the absence of this check at the protocol level is the root cause.

---

### Recommendation

Add an explicit upper-bound check on `max_fee` immediately after it is computed, before it is used in `assert_nn_le`:

```cairo
// In compute_max_possible_fee or at the call site in charge_fee:
assert_nn_le(max_fee, 2 ** 128 - 1);
```

Alternatively, tighten `pack_resource_bounds` to bound `max_price_per_unit` to `[0, 2^64 - 1]` (matching the SNIP-8 specification), which would keep each product within `[0, 2^128)` and the three-product sum within `[0, 3 × 2^128)` — still too large, so the explicit cap on `max_fee` is the safer fix.

---

### Proof of Concept

**Step 1.** Craft a V3 invoke transaction with:
- `l1_gas_bounds.max_amount = 2`
- `l1_gas_bounds.max_price_per_unit = 2^128 - 1`
- All other resource bounds set to 0.

**Step 2.** `pack_resource_bounds` validates `max_amount ≤ 2^64 - 1` ✓ and `max_price_per_unit ≥ 0` ✓. Transaction hash computation succeeds.

**Step 3.** `compute_max_possible_fee` returns:
```
max_fee = 2 × (2^128 - 1) = 2^129 - 2
```

**Step 4.** The sequencer includes the transaction. The OS reaches `charge_fee`.

**Step 5.** `assert_nn_le(low_actual_fee, max_fee)` is called. For any `low_actual_fee ∈ [0, 2^128)`:
```
max_fee - low_actual_fee ≥ 2^129 - 2 - (2^128 - 1) = 2^128 - 1
```
When `low_actual_fee < 2^128 - 1`, the difference exceeds `2^128 - 1`, failing the range check. When `low_actual_fee = 0`, `max_fee - low_actual_fee = 2^129 - 2 > 2^128`, failing unconditionally.

**Step 6.** The OS aborts. The block is unprovable. The network cannot confirm new transactions.

---

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
