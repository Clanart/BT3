### Title
Field-Arithmetic Overflow in `compute_max_possible_fee` Allows Fee-Free Transaction Execution — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` computes the maximum chargeable fee as a sum of `max_amount * max_price_per_unit` products in Cairo felt arithmetic (modulo the field prime `P ≈ 2^251`). Because `max_price_per_unit` is only validated to be non-negative (`assert_nn`, bounding it to `[0, P/2)`), an attacker can supply values that cause the sum of products to wrap around to `0 mod P`. When `compute_max_possible_fee` returns `0`, `charge_fee` exits immediately without transferring any fee, allowing the transaction to execute for free.

---

### Finding Description

**Root cause — missing upper bound on `max_price_per_unit`:**

`pack_resource_bounds` is the only place where `max_price_per_unit` is validated during transaction hash computation:

```cairo
// transaction_hash/transaction_hash.cairo:103-108
func pack_resource_bounds{range_check_ptr}(resource_bounds: ResourceBounds) -> felt {
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);   // max_amount ∈ [0, 2^64)
    assert_nn(resource_bounds.max_price_per_unit);            // max_price_per_unit ∈ [0, P/2)
    ...
}
```

`assert_nn` only checks `x < P/2`, so `max_price_per_unit` can legally be any value up to `(P-1)/2 ≈ 2^250`. The StarkNet spec treats this field as a `u128`, but no upper bound of `2^128 - 1` is enforced.

**Overflow in `compute_max_possible_fee`:**

```cairo
// transaction_impls.cairo:99-101
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
    (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
    l1_data_gas_bounds.max_price_per_unit;
```

All arithmetic is modulo `P`. With `max_price_per_unit` near `P/2`, individual products can exceed `P` and wrap around. The sum of multiple wrapped products can equal exactly `P ≡ 0 (mod P)`.

**Consequence in `charge_fee`:**

```cairo
// transaction_impls.cairo:121-125
let max_fee = compute_max_possible_fee(tx_info=tx_info);
if (max_fee == 0) {
    return ();   // ← fee transfer skipped entirely
}
```

If `max_fee == 0`, the ERC-20 transfer to the sequencer is never executed.

---

### Impact Explanation

**Direct loss of funds (Critical).**

An attacker executes invoke, deploy-account, or declare transactions without paying any fee. The sequencer's fee revenue is zero for those transactions. Because the OS program is the authoritative on-chain verifier, a valid STARK proof can be generated for a block containing such transactions, making the fee evasion permanent and provably correct on L1.

---

### Likelihood Explanation

The attack requires only crafting a V3 transaction with specific `max_price_per_unit` values — no privileged access, no key compromise, no third-party dependency. The values pass all existing validation checks. The sequencer's off-chain fee estimator (typically implemented in Rust/Python using standard integer arithmetic, not modular arithmetic) would compute a large positive fee from the same inputs and include the transaction, while the on-chain Cairo code produces `0`. This discrepancy between off-chain estimation and on-chain execution is the realistic exploitation path.

---

### Recommendation

In `pack_resource_bounds`, add an upper-bound check on `max_price_per_unit` consistent with the `u128` type the StarkNet spec mandates:

```cairo
assert_nn_le(resource_bounds.max_price_per_unit, 2 ** 128 - 1);
```

This mirrors the existing `assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1)` pattern and ensures that `max_amount * max_price_per_unit ≤ (2^64 - 1)(2^128 - 1) < 2^192 ≪ P`, making field-arithmetic overflow impossible.

---

### Proof of Concept

**Crafted transaction fields (all pass existing validation):**

| Field | Value | Validation result |
|---|---|---|
| `l1_gas_bounds.max_amount` | `2` | `assert_nn_le(2, 2^64-1)` ✓ |
| `l1_gas_bounds.max_price_per_unit` | `(P-1)/2` | `assert_nn((P-1)/2)` ✓ (since `(P-1)/2 < P/2`) |
| `l2_gas_bounds.max_amount` | `1` | `assert_nn_le(1, 2^64-1)` ✓ |
| `l2_gas_bounds.max_price_per_unit` | `1` | `assert_nn(1)` ✓ |
| `tip` | `0` | `assert_nn_le(0, 2^64-1)` ✓ |
| `l1_data_gas_bounds.max_amount` | `0` | ✓ |
| `l1_data_gas_bounds.max_price_per_unit` | `0` | ✓ |

**On-chain arithmetic (mod P):**

```
term1 = 2 * (P-1)/2        = P - 1  ≡  -1  (mod P)
term2 = 1 * (1 + 0)        = 1      ≡   1  (mod P)
term3 = 0 * 0              = 0      ≡   0  (mod P)

compute_max_possible_fee   = -1 + 1 + 0  =  0  (mod P)
```

`charge_fee` receives `max_fee = 0` and returns immediately at line 123–125 of `transaction_impls.cairo` without executing the ERC-20 transfer. The transaction runs with zero fee paid. [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L120-125)
```text
    local tx_info: TxInfo* = tx_execution_context.execution_info.tx_info;
    let max_fee = compute_max_possible_fee(tx_info=tx_info);

    if (max_fee == 0) {
        return ();
    }
```
