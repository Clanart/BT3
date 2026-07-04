### Title
Fee Computation Field Overflow in `compute_max_possible_fee` Enables Block Proof Failure / Fee Bypass — (File: `execution/transaction_impls.cairo`)

---

### Summary

The `compute_max_possible_fee` function in the StarkNet OS performs arithmetic in the Cairo prime field without bounding `max_price_per_unit` to a safe range. An unprivileged transaction sender can craft resource bounds such that the field-modular product overflows, causing `compute_max_possible_fee` to return either `0` (fee bypass) or a value `> PRIME/2` (assertion failure in `assert_nn_le`). The latter causes the OS Cairo program to abort when proving a block, preventing block finalization and halting the network.

---

### Finding Description

**Root cause — unbounded `max_price_per_unit`**

In `pack_resource_bounds` the only constraint placed on `max_price_per_unit` is:

```cairo
assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
assert_nn(resource_bounds.max_price_per_unit);          // only: value < PRIME/2
``` [1](#0-0) 

`assert_nn` permits `max_price_per_unit` up to `PRIME/2 − 1 ≈ 2^250`. No tighter upper bound is enforced.

**Overflow in `compute_max_possible_fee`**

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
    (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
    l1_data_gas_bounds.max_price_per_unit;
``` [2](#0-1) 

All arithmetic is in the Cairo field (mod PRIME). With `max_amount ≤ 2^64 − 1` and `max_price_per_unit < PRIME/2`, the product `max_amount × max_price_per_unit` can reach `≈ 2^314`, which wraps modulo PRIME. The resulting `max_fee` can be **any** value in `[0, PRIME − 1]`, including:

- **`max_fee = 0`** — fee charging is silently skipped.
- **`max_fee ∈ (PRIME/2, PRIME)`** — the subsequent `assert_nn_le` call aborts the OS.

**Two exploitation paths in `charge_fee`**

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);

if (max_fee == 0) {
    return ();                          // Path A: fee bypass
}
...
assert_nn_le(calldata.amount.low, max_fee);   // Path B: abort if max_fee > PRIME/2
``` [3](#0-2) 

**Path B — block proof failure (network halt)**

`assert_nn_le(a, b)` internally calls `assert_le(a, b)`, which range-checks `b − a ∈ [0, PRIME/2)`. When `max_fee > PRIME/2` and `actual_fee` is small, `max_fee − actual_fee > PRIME/2`, failing the range check and aborting the entire OS execution. Because the OS proves a whole block, one such transaction makes the entire block unprovable.

**Path A — fee bypass**

When `max_fee ≡ 0 (mod PRIME)`, the early-return skips the ERC-20 transfer entirely. The sequencer's off-chain fee estimator uses regular integer arithmetic and computes

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
