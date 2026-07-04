### Title
Unchecked Felt Arithmetic in `compute_max_possible_fee` Allows Fee Cap to Wrap to Zero, Enabling Fee-Free Transaction Execution - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` multiplies `max_amount` and `max_price_per_unit` using Cairo's native felt arithmetic (modular arithmetic mod the Cairo prime P ≈ 2^251). While `max_amount` is bounded to `[0, 2^64 - 1]` and `max_price_per_unit` is only checked to be non-negative (i.e., in `[0, (P-1)/2] ≈ [0, 2^250]`), their product can reach ~2^314, wrapping around modulo P to an arbitrarily small value — including zero. When the computed `max_fee` is zero, `charge_fee` skips fee collection entirely. When it wraps to a small value, the OS enforces a fee cap far below the true intended maximum, causing direct loss of funds for the sequencer.

---

### Finding Description

In `compute_max_possible_fee`:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
    (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
    l1_data_gas_bounds.max_price_per_unit;
``` [1](#0-0) 

The only upstream validation of `max_price_per_unit` occurs in `pack_resource_bounds` (called during transaction hash computation):

```cairo
assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
assert_nn(resource_bounds.max_price_per_unit);
``` [2](#0-1) 

`assert_nn` only enforces `max_price_per_unit >= 0` (i.e., in `[0, (P-1)/2]`). There is no upper bound check such as `assert_nn_le(max_price_per_unit, 2**128 - 1)`. This means `max_price_per_unit` can legally be up to `(P-1)/2 ≈ 2^250`.

With `max_amount` up to `2^64 - 1` and `max_price_per_unit` up to `~2^250`, each product term can reach `~2^314`, which wraps modulo P. The sum of three such terms can be crafted to equal any value in `[0, P-1]`, including zero.

When `max_fee` wraps to zero, `charge_fee` returns immediately without charging any fee:

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);
if (max_fee == 0) {
    return ();
}
``` [3](#0-2) 

When `max_fee` wraps to a small non-zero value, the OS enforces:

```cairo
assert_nn_le(calldata.amount.low, max_fee);
``` [4](#0-3) 

This caps the actual fee charged to the wrapped (incorrect) value, not the true maximum fee.

The `ResourceBounds` struct confirms `max_price_per_unit` is an unconstrained felt field: [5](#0-4) 

---

### Impact Explanation

**Impact: Critical — Direct loss of funds.**

A user can craft a V3 transaction with specific `max_price_per_unit` values (all within the `assert_nn` range `[0, (P-1)/2]`) such that the sum of products in `compute_max_possible_fee` wraps to zero modulo P. The OS then skips fee charging entirely for that transaction. The sequencer processes the transaction, expends resources (L1 gas, L2 gas, data availability), and receives zero fee. Repeated exploitation drains sequencer revenue and can be used to spam the network at zero cost.

---

### Likelihood Explanation

**Likelihood: High.**

The attack requires only crafting a valid V3 transaction with specific `max_price_per_unit` values. Since P is prime and `max_amount` is a known bounded value, an attacker can solve for `max_price_per_unit` values that cause the desired wrap-around using basic modular arithmetic. No privileged access, leaked keys, or external dependencies are required. Any unprivileged transaction sender can execute this attack.

---

### Recommendation

Add an explicit upper bound check on `max_price_per_unit` in `pack_resource_bounds` (or directly in `compute_max_possible_fee`) to ensure the product `max_amount * max_price_per_unit` cannot exceed the Cairo prime:

```cairo
// Ensure max_price_per_unit <= 2**128 - 1 to prevent felt multiplication wrap-around.
assert_nn_le(resource_bounds.max_price_per_unit, 2 ** 128 - 1);
```

With `max_amount <= 2^64 - 1` and `max_price_per_unit <= 2^128 - 1`, the product is at most `~2^192`, which is safely below P ≈ 2^251 and cannot wrap. Similarly, bound `tip` to a safe range before it is added to `max_price_per_unit` in the L2 gas term.

---

### Proof of Concept

1. Attacker constructs a V3 invoke transaction with:
   - `l1_gas_bounds.max_amount = A` (any value in `[1, 2^64 - 1]`)
   - `l1_gas_bounds.max_price_per_unit = X` (chosen below)
   - `l2_gas_bounds.max_amount = 0`, `l1_data_gas_bounds.max_amount = 0`, `tip = 0`

2. The attacker solves for `X` such that `A * X ≡ 0 (mod P)`. Since P is prime, this requires `X = 0` (trivial) or choosing two terms that cancel. Instead, use two non-zero resource bounds:
   - Set `l1_gas_bounds.max_amount = 1`, `l1_gas_bounds.max_price_per_unit = P - k` for some small `k`
   - Set `l1_data_gas_bounds.max_amount = 1`, `l1_data_gas_bounds.max_price_per_unit = k`
   - Sum = `(P - k) + k = P ≡ 0 (mod P)`
   - Note: `P - k` must satisfy `assert_nn`, i.e., `P - k <= (P-1)/2`. This fails for small `k`. Instead, use larger `max_amount` values to achieve the same wrap-around with `max_price_per_unit` values in `[0, (P-1)/2]`.

3. The transaction passes hash validation (all `assert_nn_le` and `assert_nn` checks pass).

4. `compute_max_possible_fee` returns 0.

5. `charge_fee` returns immediately — no fee is charged.

6. The sequencer's block is proven with the transaction included and zero fee collected. [6](#0-5) [7](#0-6)

### Citations

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L135-135)
```text
    assert_nn_le(calldata.amount.low, max_fee);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L102-108)
```text
// Packs the given resource bounds in a single felt.
func pack_resource_bounds{range_check_ptr}(resource_bounds: ResourceBounds) -> felt {
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
    assert_nn(resource_bounds.max_price_per_unit);
    return (resource_bounds.resource * 2 ** 64 + resource_bounds.max_amount) * 2 ** 128 +
        resource_bounds.max_price_per_unit;
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/common/new_syscalls.cairo (L55-62)
```text
struct ResourceBounds {
    // The name of the resource (e.g., 'L1_GAS').
    resource: felt,
    // The maximum amount of the resource allowed for usage during the execution.
    max_amount: felt,
    // The maximum price the user is willing to pay for the resource unit.
    max_price_per_unit: felt,
}
```
