### Title
Unbounded `max_price_per_unit` Enables Felt-Arithmetic Wrap-Around in `compute_max_possible_fee`, Allowing Fee-Free Transaction Execution — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`pack_resource_bounds` enforces `assert_nn_le(max_amount, 2**64 - 1)` but only `assert_nn(max_price_per_unit)` — a non-negativity check that permits values up to `(P−1)/2 ≈ 2^250`. Because `compute_max_possible_fee` multiplies these fields in felt arithmetic (mod the STARK prime P), an attacker can craft resource bounds whose product-sum wraps to exactly 0 mod P. `charge_fee` then short-circuits on `max_fee == 0` and collects nothing, letting the transaction execute for free.

---

### Finding Description

**`pack_resource_bounds` — missing upper-bound on `max_price_per_unit`**

```cairo
// transaction_hash/transaction_hash.cairo  lines 103-108
func pack_resource_bounds{range_check_ptr}(resource_bounds: ResourceBounds) -> felt {
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);   // ✓ 64-bit cap
    assert_nn(resource_bounds.max_price_per_unit);            // ✗ only ≥ 0; no upper bound
    return (resource_bounds.resource * 2 ** 64 + resource_bounds.max_amount) * 2 ** 128 +
        resource_bounds.max_price_per_unit;
}
``` [1](#0-0) 

`assert_nn(x)` in Cairo only verifies `x ∈ [0, (P−1)/2]`. It does **not** bound `max_price_per_unit` to the protocol-intended 128-bit range. The maximum accepted value is therefore `(P−1)/2 ≈ 2^250`.

**`compute_max_possible_fee` — unchecked felt multiplication**

```cairo
// execution/transaction_impls.cairo  lines 99-101
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit
     + l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip)
     + l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit;
``` [2](#0-1) 

Each product can reach `(2^64 − 1) × (P−1)/2 ≈ 2^313`, which is `≈ 2^62` multiples of P. The three-term sum can therefore be made to equal exactly `k × P` for some integer k, reducing to 0 mod P.

**`charge_fee` — zero-fee early exit**

```cairo
// execution/transaction_impls.cairo  lines 123-125
if (max_fee == 0) {
    return ();
}
``` [3](#0-2) 

When `compute_max_possible_fee` returns 0, `charge_fee` returns immediately without executing the ERC-20 transfer, so the sequencer receives nothing.

---

### Impact Explanation

**Critical — Direct loss of funds.**

Any V3 transaction sender can execute invoke, declare, or deploy-account transactions without paying any fee. The sequencer's fee revenue is completely bypassed. Because the OS proof is generated over this logic, the zero-fee execution is accepted as valid by the verifier, making the loss permanent and protocol-level.

---

### Likelihood Explanation

**High.** The exploit requires only crafting specific `max_price_per_unit` values in a standard V3 transaction — no privileged access, no leaked keys, no external dependency. The arithmetic is deterministic and the required values are trivially computable off-chain.

---

### Recommendation

Add an explicit 128-bit upper-bound check on `max_price_per_unit` inside `pack_resource_bounds`, mirroring the existing check on `max_amount`:

```diff
 func pack_resource_bounds{range_check_ptr}(resource_bounds: ResourceBounds) -> felt {
     assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
-    assert_nn(resource_bounds.max_price_per_unit);
+    assert_nn_le(resource_bounds.max_price_per_unit, 2 ** 128 - 1);
     return (resource_bounds.resource * 2 ** 64 + resource_bounds.max_amount) * 2 ** 128 +
         resource_bounds.max_price_per_unit;
 }
```

With `max_price_per_unit ≤ 2^128 − 1` and `max_amount ≤ 2^64 − 1`, the maximum single product is `(2^64 − 1)(2^128 − 1) < 2^192 ≪ P`, and the three-term sum is at most `3 × 2^192 ≪ P`, so no wrap-around is possible.

---

### Proof of Concept

**Concrete crafted resource bounds (tip = 0):**

| Field | L1 gas | L2 gas | L1 data gas |
|---|---|---|---|
| `max_amount` | 1 | 1 | 1 |
| `max_price_per_unit` | `(P−1)/2` | `(P−1)/2` | `1` |

**Arithmetic check:**

```
sum = (P−1)/2 + (P−1)/2 + 1
    = P − 1 + 1
    = P
    ≡ 0  (mod P)
```

All three `max_price_per_unit` values satisfy `assert_nn` (they are ≤ `(P−1)/2`). All three `max_amount` values satisfy `assert_nn_le(..., 2^64 − 1)`. `tip = 0` satisfies `assert_nn_le(tip, 2^64 − 1)`.

`compute_max_possible_fee` returns `0`. `charge_fee` hits the `if (max_fee == 0) { return (); }` branch and exits without charging the user. The transaction executes for free.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L123-125)
```text
    if (max_fee == 0) {
        return ();
    }
```
