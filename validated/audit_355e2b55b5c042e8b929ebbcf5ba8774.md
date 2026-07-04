### Title
Missing Upper Bound on `max_price_per_unit` Enables Fee Bypass via Field Arithmetic Overflow - (File: `transaction_hash/transaction_hash.cairo`)

---

### Summary

`pack_resource_bounds` validates that `max_price_per_unit` is non-negative but omits the required upper-bound check (`<= 2^128 - 1`). An unprivileged transaction sender can supply an oversized `max_price_per_unit` that causes `compute_max_possible_fee` to overflow the field prime and return `0`, triggering an early return in `charge_fee` and executing the transaction with zero fees paid.

---

### Finding Description

In `pack_resource_bounds` the two fields are validated asymmetrically:

```cairo
// transaction_hash/transaction_hash.cairo  lines 103-107
func pack_resource_bounds{range_check_ptr}(resource_bounds: ResourceBounds) -> felt {
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);   // ✓ properly bounded
    assert_nn(resource_bounds.max_price_per_unit);            // ✗ only non-negative, no upper bound
    return (resource_bounds.resource * 2 ** 64 + resource_bounds.max_amount) * 2 ** 128 +
        resource_bounds.max_price_per_unit;
}
``` [1](#0-0) 

`assert_nn(x)` in Cairo only guarantees `x ∈ [0, (P−1)/2]` where P ≈ 2^251. The StarkNet protocol specifies `max_price_per_unit` as a **u128** (max 2^128 − 1), but the OS enforces no such ceiling, allowing values up to ≈ 2^250.

`compute_max_possible_fee` then multiplies these unbounded values directly:

```cairo
// execution/transaction_impls.cairo  lines 95-101
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
    (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
    l1_data_gas_bounds.max_price_per_unit;
``` [2](#0-1) 

Because `max_amount ≤ 2^64 − 1` and `max_price_per_unit ≤ (P−1)/2 ≈ 2^250`, the product can reach ≈ 2^314, wrapping around P many times. A sender can choose values so the entire sum ≡ 0 (mod P).

When `max_fee = 0`, `charge_fee` exits immediately without transferring any tokens:

```cairo
// execution/transaction_impls.cairo  lines 121-125
let max_fee = compute_max_possible_fee(tx_info=tx_info);
if (max_fee == 0) {
    return ();
}
``` [3](#0-2) 

The downstream guard `assert_nn_le(calldata.amount.low, max_fee)` is never reached, so no fee is charged and the proof remains valid. [4](#0-3) 

---

### Impact Explanation

Any transaction sender can execute arbitrary transactions with zero fee payment. The OS produces a valid proof for such a block, meaning the fee bypass is protocol-enforced and cannot be corrected after the fact. This constitutes **direct loss of funds**: the protocol permanently loses fee revenue that should have been collected, and the attack is repeatable at will.

---

### Likelihood Explanation

The attack requires only arithmetic over the field — no privileged access, no leaked keys, no external dependencies. The attacker controls all `ResourceBounds` fields in their own transaction. The concrete example below shows the computation is trivial.

---

### Recommendation

Add the missing upper-bound check in `pack_resource_bounds`:

```diff
  func pack_resource_bounds{range_check_ptr}(resource_bounds: ResourceBounds) -> felt {
      assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
-     assert_nn(resource_bounds.max_price_per_unit);
+     assert_nn_le(resource_bounds.max_price_per_unit, 2 ** 128 - 1);
      return (resource_bounds.resource * 2 ** 64 + resource_bounds.max_amount) * 2 ** 128 +
          resource_bounds.max_price_per_unit;
  }
```

This mirrors the existing pattern used for `max_amount` and matches the u128 type mandated by the StarkNet specification.

---

### Proof of Concept

Let P = StarkNet field prime ≈ 2^251 + 17·2^192 + 1.

Choose resource bounds:

| Field | Value |
|---|---|
| `l1_gas_bounds.max_amount` | `2` |
| `l1_gas_bounds.max_price_per_unit` | `(P − 1) / 2` |
| `l2_gas_bounds.max_amount` | `1` |
| `l2_gas_bounds.max_price_per_unit` | `1` |
| `l1_data_gas_bounds.max_amount` | `0` |
| `tip` | `0` |

**Validation step** (`pack_resource_bounds`):
- `assert_nn_le(2, 2^64 − 1)` ✓
- `assert_nn((P−1)/2)` ✓ — exactly the maximum allowed by `assert_nn`

**Fee computation** (`compute_max_possible_fee`):
```
max_fee = 2 · (P−1)/2  +  1 · 1  +  0
        = (P − 1)      +  1
        = P
        ≡ 0  (mod P)
```

**Result**: `charge_fee` sees `max_fee = 0` and returns immediately. The transaction executes with zero fees paid. The OS proof is valid.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L95-101)
```text
    tempvar l1_gas_bounds: ResourceBounds = resource_bounds[L1_GAS_INDEX];
    tempvar l2_gas_bounds: ResourceBounds = resource_bounds[L2_GAS_INDEX];
    tempvar l1_data_gas_bounds = resource_bounds[L1_DATA_GAS_INDEX];

    return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
        (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
        l1_data_gas_bounds.max_price_per_unit;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L121-125)
```text
    let max_fee = compute_max_possible_fee(tx_info=tx_info);

    if (max_fee == 0) {
        return ();
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L134-135)
```text
    // Verify that the charged amount is not larger than the transaction's max_fee field.
    assert_nn_le(calldata.amount.low, max_fee);
```
