### Title
Unbounded `max_price_per_unit` in `pack_resource_bounds` Enables Felt-Arithmetic Overflow in `compute_max_possible_fee`, Allowing Complete Fee Bypass — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

The `pack_resource_bounds` function in `transaction_hash.cairo` enforces that `max_amount ≤ 2^64 - 1` but only checks `assert_nn(max_price_per_unit)` — meaning `max_price_per_unit` is bounded only to `[0, (P-1)/2]` (≈ 2^250), not to the protocol-intended `[0, 2^128 - 1]`. When `compute_max_possible_fee` multiplies these values together in felt arithmetic, the products can overflow the field prime `P`, and the sum of three such products can be crafted to equal exactly `0 (mod P)`. Because `charge_fee` unconditionally skips fee collection when `max_fee == 0`, an attacker can obtain fully free execution of any v3 transaction.

---

### Finding Description

**Root cause — missing upper bound on `max_price_per_unit`:**

In `transaction_hash/transaction_hash.cairo`, `pack_resource_bounds` validates resource bounds during transaction hash computation:

```cairo
func pack_resource_bounds{range_check_ptr}(resource_bounds: ResourceBounds) -> felt {
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);   // ✓ bounded to u64
    assert_nn(resource_bounds.max_price_per_unit);            // ✗ only non-negative, NOT ≤ 2^128-1
    ...
}
``` [1](#0-0) 

`assert_nn` in Cairo only constrains a felt to `[0, (P-1)/2]`. The protocol-intended type for `max_price_per_unit` is `u128` (≤ 2^128 - 1), but the OS never enforces this upper bound. `tip` is correctly bounded to `[0, 2^64 - 1]`: [2](#0-1) 

**Overflow in `compute_max_possible_fee`:**

`compute_max_possible_fee` computes the fee ceiling using plain felt arithmetic:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit
     + l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip)
     + l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit;
``` [3](#0-2) 

With `max_amount ≤ 2^64 - 1` and `max_price_per_unit ≤ (P-1)/2 ≈ 2^250`, each product can reach `(2^64 - 1) × (P-1)/2 ≈ 2^314`, which is `≈ 2^63 × P`. The sum of three such terms can therefore wrap around `P` up to `≈ 3 × 2^63` times. Because `P` is prime, the attacker has full control over the residue: by choosing `max_price_per_unit` values appropriately, the sum can be made to equal exactly `0 (mod P)`.

**Fee bypass via the zero-check gate:**

`charge_fee` unconditionally returns without charging any fee when `max_fee == 0`:

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);
if (max_fee == 0) {
    return ();
}
``` [4](#0-3) 

This path is reached for every v3 account transaction type: invoke, deploy-account, and declare.

---

### Impact Explanation

**Impact: Critical — Direct loss of funds.**

An unprivileged transaction sender can obtain complete execution of any v3 transaction (invoke, deploy-account, declare) without paying any fee. The sequencer's off-chain fee validation may compute a large, legitimate-looking `max_fee` (if it uses integer arithmetic or a different code path), include the transaction in a block, and then discover that the OS-proven `max_fee` is `0`, resulting in zero fee revenue. Over many such transactions the sequencer suffers unbounded direct loss of fee income, which constitutes direct loss of funds at the protocol level.

---

### Likelihood Explanation

**Likelihood: High.**

- The attacker is an ordinary transaction sender — no privileged role required.
- The crafted `max_price_per_unit` values pass all OS-enforced checks (`assert_nn`), so the transaction is provably valid.
- The arithmetic to find overflow-to-zero values is straightforward: choose any `max_amount ≠ 0` for each resource, then solve the linear congruence `Σ (amount_i × price_i) ≡ 0 (mod P)` for the `price_i` values, subject to each `price_i ∈ [0, (P-1)/2]`. Solutions always exist.
- The attack is repeatable with fresh nonces.

---

### Recommendation

In `pack_resource_bounds`, add an upper-bound range check on `max_price_per_unit` to enforce the protocol-intended `u128` type:

```cairo
assert_nn_le(resource_bounds.max_price_per_unit, 2 ** 128 - 1);
``` [5](#0-4) 

This mirrors the existing pattern for `max_amount` and `tip`, and ensures that the maximum product per resource term is `(2^64 - 1) × (2^128 - 1) < 2^192 < P`, making overflow in `compute_max_possible_fee` impossible.

---

### Proof of Concept

**Setup:** Craft a v3 invoke transaction where the three resource-bound products sum to `0 (mod P)`.

**Step 1 — Choose amounts:**
```
l1_amount = 1,  l2_amount = 1,  l1_data_amount = 1
tip = 0
```

**Step 2 — Solve for prices:**
We need `p1 + p2 + p3 ≡ 0 (mod P)` with each `p_i ∈ [0, (P-1)/2]`.

Choose:
```
p1 = (P - 1) / 3        (integer, since P ≡ 1 mod 3)
p2 = (P - 1) / 3
p3 = P - 2*(P-1)/3 - ... (adjust to make sum ≡ 0 mod P)
```

Concretely, any triple `(p1, p2, p3)` satisfying `p1 + p2 + p3 = P` works (each `p_i < P/2` is achievable). For example:
```
p1 = P/3 + 1,  p2 = P/3,  p3 = P - p1 - p2   (all ≤ P/2)
```

**Step 3 — Submit transaction:**
The transaction passes `pack_resource_bounds` (each `p_i` passes `assert_nn`), the transaction hash is valid, and the OS executes the transaction.

**Step 4 — OS computes:**
```
max_fee = 1 * p1 + 1 * p2 + 1 * p3 = P ≡ 0 (mod P)
```

**Step 5 — Fee bypass:**
`charge_fee` hits `if (max_fee == 0) { return (); }` and exits without transferring any tokens. The transaction executes for free. [6](#0-5) [1](#0-0)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L117-117)
```text
    assert_nn_le(tip, 2 ** 64 - 1);
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L121-125)
```text
    let max_fee = compute_max_possible_fee(tx_info=tx_info);

    if (max_fee == 0) {
        return ();
    }
```
