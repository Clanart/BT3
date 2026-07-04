### Title
Unbounded `max_price_per_unit` Causes `assert_nn_le` Failure in `charge_fee`, Enabling Network Halt — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

The `max_price_per_unit` field of `ResourceBounds` is only validated to be non-negative (`assert_nn`, bounding it to `[0, 2^128)`), with no upper cap that accounts for multiplication by `max_amount` (up to `2^64 - 1`). Their product can reach ~`2^192`. `compute_max_possible_fee` returns this value as `max_fee`, and the subsequent `assert_nn_le(actual_fee, max_fee)` in `charge_fee` fails because Cairo's range-check-based `assert_nn_le` requires `max_fee - actual_fee < 2^128`. The sequencer's off-chain execution uses regular arithmetic and passes the check, but the Cairo OS proof step fails — an unprivileged user can exploit this discrepancy to cause a block proof failure and halt the network.

---

### Finding Description

**Vulnerability class**: Fee/accounting bug — unbounded rate multiplier causing computed value to exceed the range expected by a downstream bounds check.

In `pack_resource_bounds` (called during transaction hash computation):

```cairo
func pack_resource_bounds{range_check_ptr}(resource_bounds: ResourceBounds) -> felt {
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);   // max_amount ≤ 2^64 - 1
    assert_nn(resource_bounds.max_price_per_unit);            // max_price_per_unit ∈ [0, 2^128)
    ...
}
```

`max_price_per_unit` is bounded only to `[0, 2^128 - 1]`. No cap is placed on the *product* `max_amount × max_price_per_unit`. [1](#0-0) 

In `compute_max_possible_fee`:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit +
       l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip) +
       l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit;
```

With `max_amount = 2^64 - 1` and `max_price_per_unit = 2^128 - 1`, each term reaches `(2^64 - 1)(2^128 - 1) ≈ 2^192`. The total `max_fee` can reach `~3 × 2^192`, well above `2^128`. [2](#0-1) 

In `charge_fee`, the OS then executes:

```cairo
assert_nn_le(calldata.amount.low, max_fee);
```

Cairo's `assert_nn_le(a, b)` is implemented as:
1. `assert_nn(a)` — checks `a ∈ [0, 2^128)`
2. `assert_nn(b - a)` — checks `b - a ∈ [0, 2^128)`

When `max_fee ≈ 2^192` and `actual_fee` is a normal value (e.g., `10^6`), `max_fee - actual_fee ≈ 2^192 >> 2^128`. Step 2 fails the range check — this is an **assertion failure**, not a graceful revert. An assertion failure in the Cairo OS causes the entire block proof to be invalid. [3](#0-2) 

---

### Impact Explanation

**High — Network not being able to confirm new transactions (total network shutdown).**

When the Cairo OS encounters an assertion failure (not a revert), the STARK proof for the entire block cannot be generated. No block containing such a transaction can be finalized. If the sequencer repeatedly attempts to include such transactions (or cannot detect the discrepancy before proof generation), the network halts.

---

### Likelihood Explanation

**Medium.** The sequencer's off-chain execution environment (Rust/Python) uses standard arithmetic: `actual_fee ≤ max_fee` trivially passes when `max_fee ≈ 2^192`. The sequencer has no reason to reject the transaction. Only the Cairo OS proof step uses range-check-based `assert_nn_le`, creating a discrepancy. An attacker needs only to submit a single V3 transaction with `max_amount = 2^64 - 1` and `max_price_per_unit = 2^128 - 1` for any resource type. No special privilege is required.

---

### Recommendation

In `pack_resource_bounds` (or in `compute_max_possible_fee`), add an explicit upper bound on `max_price_per_unit` that ensures the product `max_amount × max_price_per_unit` stays within `[0, 2^128)`. For example:

```cairo
assert_nn_le(resource_bounds.max_price_per_unit, 2 ** 64 - 1);
```

This mirrors the bound on `max_amount` and ensures `max_fee ≤ 3 × (2^64 - 1)^2 ≈ 3 × 2^128`, which is still large enough for practical use but keeps `assert_nn_le` in `charge_fee` from failing. [4](#0-3) 

---

### Proof of Concept

1. Attacker constructs a V3 `invoke` transaction with:
   - `l1_gas_bounds.max_amount = 2^64 - 1`
   - `l1_gas_bounds.max_price_per_unit = 2^128 - 1`
   - (similarly for L2 and L1_DATA gas bounds)

2. The transaction hash is computed successfully — `pack_resource_bounds` passes because `assert_nn_le(max_amount, 2^64 - 1)` and `assert_nn(max_price_per_unit)` both hold.

3. The sequencer's off-chain execution runs the transaction. It computes `max_fee ≈ 3 × 2^192` and checks `actual_fee ≤ max_fee` using regular arithmetic — this passes. The sequencer includes the transaction in a block.

4. The Cairo OS processes the block. `compute_max_possible_fee` returns `max_fee ≈ 3 × 2^192`.

5. `charge_fee` calls `assert_nn_le(actual_fee, max_fee)`. Internally, `assert_nn(max_fee - actual_fee)` is called. Since `max_fee - actual_fee ≈ 3 × 2^192 > 2^128`, the range check fails.

6. The Cairo OS aborts with an assertion failure. The block proof cannot be generated. The network cannot finalize the block. [5](#0-4) [6](#0-5) [1](#0-0)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L86-102)
```text
// Returns the maximum possible fee that can be charged for the transaction.
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
