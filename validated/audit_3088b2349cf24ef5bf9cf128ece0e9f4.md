### Title
Hardcoded `high=0` in Uint256 Fee Amount Causes OS Hard Assertion Failure When `max_fee > 2^128` — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

In `charge_fee`, the actual fee transfer amount is constructed as `Uint256(low=low_actual_fee, high=0)` — the `high` limb is hardcoded to zero. The guard that follows, `assert_nn_le(calldata.amount.low, max_fee)`, uses Cairo's range-check-backed `assert_nn_le`, which internally verifies that `max_fee - low_actual_fee` fits in `[0, 2^128)`. Because `compute_max_possible_fee` returns a raw `felt` that can legitimately reach `~3 × 2^192` with protocol-valid resource bounds, the subtraction `max_fee - low_actual_fee` will exceed `2^128` for any reasonable actual fee, causing a hard assertion failure that aborts the entire OS execution and makes the block unprovable.

---

### Finding Description

**`compute_max_possible_fee` can return values far above `2^128`** [1](#0-0) 

The function returns a raw `felt` sum of three products. Each product is `max_amount × max_price_per_unit`. The bounds enforced during transaction-hash computation are: [2](#0-1) 

- `max_amount ≤ 2^64 − 1` (enforced by `assert_nn_le`)
- `max_price_per_unit ∈ [0, 2^128)` (enforced only by `assert_nn`, which is a single range-check — it does **not** bound the value to anything smaller than `2^128 − 1`)

Maximum single-resource product: `(2^64 − 1) × (2^128 − 1) ≈ 2^192`.  
Maximum total `max_fee` across three resources: `≈ 3 × 2^192`, well within the field prime (`≈ 2^251`) so no field overflow occurs — the value is simply a legitimately large felt.

**The hardcoded `high=0` and the broken guard** [3](#0-2) 

`assert_nn_le(a, b)` is defined in Cairo's standard library as `assert_nn(b − a)`, which uses the range-check builtin to verify `b − a ∈ [0, 2^128)`. When `max_fee ≈ 2^192` and `low_actual_fee ≤ 2^128 − 1`:

```
max_fee − low_actual_fee ≈ 2^192 − (2^128 − 1)  >>  2^128
```

The range check fails. Because `charge_fee` uses a **hard assertion** (not a soft revert), the failure propagates as an OS-level abort — the entire block execution fails and no valid STARK proof can be generated for it.

---

### Impact Explanation

A Cairo program assertion failure inside the OS means the prover cannot produce a valid proof for the block. The sequencer cannot finalize the block on L1. If the sequencer does not independently detect and exclude such a transaction before committing to a block, the block is permanently stuck and the network cannot advance — matching the **High: network not being able to confirm new transactions** impact class.

---

### Likelihood Explanation

Any unprivileged v3 transaction sender can set `max_price_per_unit` to a value close to `2^128 − 1` for any of the three resource types. The transaction passes all upstream checks (signature, nonce, hash, `pack_resource_bounds` bounds). The OS has no guard before `assert_nn_le` that would gracefully revert the transaction instead of aborting the block. The sequencer's off-chain simulation runs the same OS code, so it would also fail — but only if the simulation faithfully executes `charge_fee`. If the sequencer's mempool admission logic does not simulate fee charging to this depth, the transaction can slip through into a committed block.

---

### Recommendation

Replace the scalar `assert_nn_le` guard with a proper 256-bit comparison, or add an explicit upper-bound check on `max_fee` before constructing the `Uint256` transfer amount:

```cairo
// Before constructing the Uint256 amount, assert max_fee fits in 128 bits.
assert_nn_le(max_fee, 2 ** 128 - 1);
// Then the existing check is safe.
assert_nn_le(calldata.amount.low, max_fee);
```

Alternatively, represent `max_fee` as a `Uint256` throughout and perform a full 256-bit `uint256_le` comparison.

---

### Proof of Concept

1. Craft a v3 `invoke` transaction with:
   - `l1_gas_bounds.max_amount = 2^64 − 1`
   - `l1_gas_bounds.max_price_per_unit = 2^128 − 1`
   - (other resource bounds can be zero)
2. Submit the transaction; it passes signature verification, nonce check, and `pack_resource_bounds` validation (both bounds are within their respective allowed ranges).
3. The sequencer includes it in a block and runs the OS.
4. `compute_max_possible_fee` returns `(2^64 − 1) × (2^128 − 1) ≈ 2^192`.
5. The sequencer hint `LoadActualFee` sets `low_actual_fee` to the real fee (e.g., `10^9`).
6. `assert_nn_le(10^9, 2^192)` evaluates `2^192 − 10^9 ≈ 2^192`, which is `>> 2^128`.
7. The range-check builtin rejects the value → OS assertion failure → block is unprovable → network halt. [4](#0-3) [5](#0-4)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L103-108)
```text
func pack_resource_bounds{range_check_ptr}(resource_bounds: ResourceBounds) -> felt {
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
    assert_nn(resource_bounds.max_price_per_unit);
    return (resource_bounds.resource * 2 ** 64 + resource_bounds.max_amount) * 2 ** 128 +
        resource_bounds.max_price_per_unit;
}
```
