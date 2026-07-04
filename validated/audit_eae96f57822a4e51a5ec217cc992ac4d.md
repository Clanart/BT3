### Title
Unvalidated Upper Bound on `max_price_per_unit` Enables Fee Computation Overflow and Fee Evasion — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo` and `execution/transaction_impls.cairo`)

---

### Summary

The `ResourceBounds.max_price_per_unit` field is validated only for non-negativity via `assert_nn`, with no upper bound constraint. This allows values up to `(PRIME-1)/2 ≈ 2^250`. The fee computation in `compute_max_possible_fee` performs felt arithmetic modulo PRIME, which can be made to wrap to exactly 0 by a crafted transaction. When `compute_max_possible_fee` returns 0, `charge_fee` unconditionally skips fee deduction, allowing an unprivileged transaction sender to execute with a large L2 gas budget while paying zero fees.

---

### Finding Description

**Root cause — missing upper bound in `pack_resource_bounds`:**

In `transaction_hash/transaction_hash.cairo`, `pack_resource_bounds` validates:

```cairo
assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
assert_nn(resource_bounds.max_price_per_unit);
```

`assert_nn` only enforces `max_price_per_unit ∈ [0, (PRIME-1)/2]`. There is no upper bound such as `2^128 - 1`. Values up to `(PRIME-1)/2 ≈ 2^250` are accepted as valid. [1](#0-0) 

**Overflow in `compute_max_possible_fee`:**

In `execution/transaction_impls.cairo`, the maximum fee is computed in felt arithmetic (mod PRIME):

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
    (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
    l1_data_gas_bounds.max_price_per_unit;
``` [2](#0-1) 

Because `max_price_per_unit` can be as large as `(PRIME-1)/2`, the product `max_amount * max_price_per_unit` can exceed PRIME and wrap around. The sum of three such products can be made to equal exactly PRIME, causing the return value to be `0 mod PRIME`.

**Fee skipped when result is 0:**

In `charge_fee`:

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);
if (max_fee == 0) {
    return ();
}
``` [3](#0-2) 

When `max_fee == 0`, the ERC-20 transfer that deducts fees from the user's account is never executed.

**Initial gas is drawn from `l2_gas_bounds.max_amount`:**

```cairo
func get_initial_user_gas_bound(common_tx_fields: CommonTxFields*) -> felt {
    assert common_tx_fields.n_resource_bounds = 3;
    return common_tx_fields.resource_bounds[L2_GAS_INDEX].max_amount;
}
``` [4](#0-3) 

The attacker can set `l2_gas_bounds.max_amount` to `2^64 - 1` (the maximum allowed) while still making the total fee sum wrap to 0, obtaining a large execution gas budget at zero cost.

---

### Impact Explanation

An unprivileged transaction sender can execute transactions with up to `2^64 - 1` L2 gas units without paying any fee. This enables:

1. **Free execution of arbitrarily expensive transactions**, draining sequencer resources without compensation.
2. **Network spam at zero cost**, which can saturate block capacity and prevent legitimate transactions from being confirmed — matching the allowed impact: **High. Network not being able to confirm new transactions (total network shutdown).**

---

### Likelihood Explanation

The exploit requires only:
- Knowledge of the StarkNet field prime (public).
- Crafting specific resource bound values (simple arithmetic).
- Signing the transaction with those values (standard wallet operation).

No privileged access, leaked keys, or external dependencies are required. Any unprivileged transaction sender can execute this.

---

### Recommendation

Add an explicit upper bound check on `max_price_per_unit` in `pack_resource_bounds`, consistent with the 128-bit packing layout used in the formula:

```cairo
assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
assert_nn_le(resource_bounds.max_price_per_unit, 2 ** 128 - 1);  // Add this
``` [5](#0-4) 

This ensures that `max_amount * max_price_per_unit ≤ (2^64 - 1) * (2^128 - 1) < 2^192 < PRIME`, making overflow impossible for any single product, and the sum of three products bounded well below PRIME.

---

### Proof of Concept

Let `P = 2^251 + 17·2^192 + 1` (StarkNet PRIME). Set:

| Resource | `max_amount` | `max_price_per_unit` |
|---|---|---|
| L1_GAS | `2` | `(P-1)/2` |
| L2_GAS | `2^64 - 1` | `0` |
| L1_DATA_GAS | `1` | `1` |

`tip = 0`.

Fee sum:
```
= 2 * (P-1)/2  +  (2^64-1) * 0  +  1 * 1
= (P - 1)      +  0              +  1
= P
≡ 0  (mod P)
```

All values satisfy the existing constraints:
- `max_amount ≤ 2^64 - 1` ✓
- `assert_nn(max_price_per_unit)`: `(P-1)/2` is the maximum value allowed by `assert_nn` ✓

The attacker signs this transaction, submits it, and the OS:
1. Computes the transaction hash including these resource bounds (hash is valid).
2. Verifies the signature (valid, since the attacker signed it).
3. Executes `__validate__` and `__execute__` with `2^64 - 1` L2 gas.
4. Calls `compute_max_possible_fee` → returns `0`.
5. `charge_fee` returns immediately — **no fee is deducted**.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L75-78)
```text
func get_initial_user_gas_bound(common_tx_fields: CommonTxFields*) -> felt {
    assert common_tx_fields.n_resource_bounds = 3;
    return common_tx_fields.resource_bounds[L2_GAS_INDEX].max_amount;
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L99-101)
```text
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
