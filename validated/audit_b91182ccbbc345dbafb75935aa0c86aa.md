### Title
Unchecked Felt Overflow in `compute_max_possible_fee` Causes Unprovable Block via `assert_nn_le` Failure - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` computes the maximum chargeable fee as a raw felt arithmetic sum of products (`max_amount × max_price_per_unit`) without validating that the result fits within the `[0, 2^128)` range required by the subsequent `assert_nn_le` call in `charge_fee`. Because `max_amount` is validated up to `2^64 − 1` and `max_price_per_unit` up to `2^128 − 1`, their product can reach ~`2^192`, and the three-resource sum can reach ~`3 × 2^192`. When `charge_fee` calls `assert_nn_le(actual_fee, max_fee)` with such a `max_fee`, the range-check builtin fails unconditionally, making the block unprovable and halting the network.

---

### Finding Description

`compute_max_possible_fee` (lines 87–102 of `transaction_impls.cairo`) returns a felt-arithmetic expression:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit
     + l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip)
     + l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit;
``` [1](#0-0) 

The resource bounds are validated during transaction hash computation in `pack_resource_bounds`:

- `max_amount` ≤ `2^64 − 1` (via `assert_nn_le`)
- `max_price_per_unit` ∈ `[0, 2^128)` (via `assert_nn` — **no upper bound tighter than `2^128`**)
- `tip` ≤ `2^64 − 1` [2](#0-1) 

This means a single product `max_amount × max_price_per_unit` can reach `(2^64 − 1) × (2^128 − 1) ≈ 2^192`, and the three-resource sum can reach `≈ 3 × 2^192`. No range check is applied to the returned `max_fee` value.

`charge_fee` then uses this value directly in:

```cairo
assert_nn_le(calldata.amount.low, max_fee);
``` [3](#0-2) 

`assert_nn_le(a, b)` is implemented as `assert_nn(a)` followed by `assert_nn(b − a)`, where `assert_nn` uses the range-check builtin to verify the value is in `[0, 2^128)`. When `max_fee ≈ 3 × 2^192`:

- `actual_fee` (hint-provided, `< 2^128`) passes `assert_nn(actual_fee)`.
- `max_fee − actual_fee ≈ 3 × 2^192 ≫ 2^128` **fails** the range-check builtin unconditionally.

The sequencer cannot supply any `actual_fee` value that satisfies this check, because even `actual_fee = max_fee` would fail `assert_nn(actual_fee)` since `max_fee > 2^128`. The block becomes unprovable.

The early-return guard `if (max_fee == 0) { return (); }` does not help here — `max_fee` is far from zero. [4](#0-3) 

---

### Impact Explanation

**High — Network not being able to confirm new transactions (total network shutdown).**

If a block contains even one transaction with crafted resource bounds that produce `max_fee > 2^128`, the OS Cairo program cannot generate a valid proof for that block. The block is permanently stuck; no subsequent blocks can be committed on top of it. This constitutes a total network halt for the duration the poisoned block remains unresolved.

---

### Likelihood Explanation

**Medium.** The attack requires the sequencer to include the crafted transaction. A correctly implemented sequencer mempool would reject transactions where `max_fee` exceeds a safe threshold. However:

1. The OS Cairo code — the protocol's ground truth — imposes **no such bound**, creating a gap between off-chain validation and on-chain enforcement.
2. In a decentralized sequencer environment, different sequencer implementations may have inconsistent or absent mempool-level `max_fee` checks.
3. The crafted transaction is syntactically valid (correct hash, valid signature, valid nonce), so it passes all cryptographic checks and may pass naive mempool validators.
4. The resource bound values that trigger the bug (`max_amount = 2^64 − 1`, `max_price_per_unit = 2^128 − 1`) are within the ranges explicitly permitted by `pack_resource_bounds`. [5](#0-4) 

---

### Recommendation

Add an explicit range check on the result of `compute_max_possible_fee` before it is used in `assert_nn_le`. Specifically, assert that `max_fee` is within `[0, 2^128)` (or whatever upper bound the protocol intends), or restructure `charge_fee` to use a safe comparison that handles large felt values. Alternatively, tighten the per-resource-bound constraints in `pack_resource_bounds` so that the maximum possible sum of products is provably less than `2^128`.

---

### Proof of Concept

1. Craft a V3 invoke transaction with:
   - `l1_gas_bounds.max_amount = 2^64 − 1`
   - `l1_gas_bounds.max_price_per_unit = 2^128 − 1`
   - (other bounds can be zero)
2. Sign and submit the transaction. It passes `pack_resource_bounds` validation (both values are within their checked ranges).
3. The sequencer includes the transaction in a block.
4. The OS executes `compute_max_possible_fee`:
   ```
   max_fee = (2^64 − 1) × (2^128 − 1) ≈ 2^192
   ```
5. `charge_fee` reaches `assert_nn_le(actual_fee, max_fee)`.
6. For any `actual_fee < 2^128`: `max_fee − actual_fee ≈ 2^192 > 2^128` → range-check builtin fails.
7. The proof for the block cannot be generated. The network halts on this block. [6](#0-5) [7](#0-6)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L121-125)
```text
    let max_fee = compute_max_possible_fee(tx_info=tx_info);

    if (max_fee == 0) {
        return ();
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
