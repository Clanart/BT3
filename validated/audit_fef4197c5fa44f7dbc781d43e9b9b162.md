### Title
Unbounded `max_price_per_unit` Causes `assert_nn_le` Range-Check Failure in `charge_fee`, Invalidating OS Proof - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` multiplies user-supplied `max_amount` (bounded to `[0, 2^64)`) by `max_price_per_unit` (only checked to be non-negative via `assert_nn`, so it can be up to `2^128 - 1`). The product can reach ~`2^192`. This result is then passed as the upper bound to `assert_nn_le(actual_fee, max_fee)` in `charge_fee`. Cairo's `assert_nn_le` uses range checks that only accept values in `[0, 2^128)`. When `max_fee > 2^128`, the range check fails unconditionally, making the OS proof invalid for any block containing such a transaction.

---

### Finding Description

In `transaction_hash.cairo`, `pack_resource_bounds` enforces:

```cairo
assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);  // max_amount ≤ 2^64 - 1
assert_nn(resource_bounds.max_price_per_unit);           // max_price_per_unit ≥ 0 (i.e., in [0, 2^128))
```

No upper bound is placed on `max_price_per_unit` beyond the implicit `2^128` from `assert_nn`.

`compute_max_possible_fee` then computes:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit
     + l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip)
     + l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit;
```

With `max_amount` up to `2^64 - 1` and `max_price_per_unit` up to `2^128 - 1`, each product can reach `(2^64 - 1) × (2^128 - 1) ≈ 2^192`. The sum of three such products can reach `~3 × 2^192`, well above `2^128` but below the STARK field prime (`~2^251`), so no field-level wrap-around occurs — the felt value is simply a large number.

This large `max_fee` is then passed to:

```cairo
assert_nn_le(calldata.amount.low, max_fee);
```

Cairo's `assert_nn_le(a, b)` internally checks that `b - a` fits in a range-check cell, i.e., `b - a ∈ [0, 2^128)`. When `max_fee > 2^128` and `actual_fee` is any small value (including 0), `max_fee - actual_fee > 2^128`, and the range check fails. There is no value of `actual_fee` the sequencer can supply via `LoadActualFee` that avoids this failure — even `actual_fee = 0` does not help because `max_fee` itself exceeds the range-check bound.

---

### Impact Explanation

When a V3 transaction with `max_price_per_unit > 2^64` is included in a block, the OS Cairo program fails at `assert_nn_le` during `charge_fee`. The STARK proof for that block cannot be generated. The sequencer must discard the block and re-sequence without the offending transaction. If the sequencer's off-chain mempool validation does not enforce `max_fee ≤ 2^128` (i.e., `max_price_per_unit ≤ 2^64` per resource type), an attacker can repeatedly submit such transactions, causing repeated proof failures and preventing the network from confirming new transactions — a total network shutdown.

**Matched impact**: High — Network not being able to confirm new transactions.

---

### Likelihood Explanation

Any unprivileged user can submit a V3 transaction. The only on-chain enforcement on `max_price_per_unit` is `assert_nn` (non-negativity), which permits values up to `2^128 - 1`. A value of `2^65` passes all transaction-hash validation checks but causes `max_fee ≈ 2^129 > 2^128`. If the sequencer's off-chain simulation path does not independently enforce `max_price_per_unit ≤ 2^64`, the transaction enters the block and breaks the proof. The attacker needs no special privilege, no leaked key, and no trusted role — only the ability to submit a standard V3 transaction.

---

### Recommendation

Add an explicit upper-bound check on `max_price_per_unit` in `pack_resource_bounds` (or in `compute_max_possible_fee`) to ensure `max_fee` never exceeds `2^128 - 1`:

```cairo
// In pack_resource_bounds or compute_max_possible_fee:
assert_nn_le(resource_bounds.max_price_per_unit, 2 ** 64 - 1);
```

This mirrors the existing bound on `max_amount` and guarantees that `max_amount × max_price_per_unit ≤ (2^64 - 1)^2 < 2^128`, keeping `max_fee` within the range-check domain for all three resource types combined.

---

### Proof of Concept

1. Craft a V3 invoke transaction with:
   - `l1_gas_bounds.max_amount = 2^64 - 1` (passes `assert_nn_le(max_amount, 2^64 - 1)`)
   - `l1_gas_bounds.max_price_per_unit = 2^65` (passes `assert_nn` since `2^65 < 2^128`)
   - All other resource bounds set to 0.

2. Submit the transaction. It passes mempool signature and hash validation because `pack_resource_bounds` only checks `assert_nn(max_price_per_unit)`.

3. Sequencer includes the transaction in a block and runs the OS.

4. OS reaches `charge_fee` → `compute_max_possible_fee` returns `(2^64 - 1) × 2^65 ≈ 2^129`.

5. OS executes `assert_nn_le(actual_fee, 2^129)`. Internally this checks `2^129 - actual_fee ∈ [0, 2^128)`. For any `actual_fee ≥ 0`, `2^129 - actual_fee ≥ 2^128`, so the range check fails.

6. The OS proof is invalid. The block cannot be finalized. The sequencer must re-run without the transaction, and the attacker can repeat indefinitely.

**Relevant code locations:**

- `max_price_per_unit` bound: [1](#0-0) 
- `compute_max_possible_fee` unchecked product: [2](#0-1) 
- `assert_nn_le` failure site: [3](#0-2)

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
