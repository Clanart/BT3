### Title
Felt Arithmetic Overflow in `compute_max_possible_fee` Allows Fee-Free Transaction Execution - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` computes the maximum chargeable fee using unchecked felt arithmetic (mod P). Because `max_price_per_unit` is only constrained to `[0, (P-1)/2]` by `pack_resource_bounds`, a user can craft resource bounds whose product sum wraps around to exactly 0 mod P. When `charge_fee` sees `max_fee == 0`, it returns immediately without executing any ERC-20 transfer, allowing the transaction to execute with a non-trivial L2 gas budget while paying zero fees.

---

### Finding Description

`compute_max_possible_fee` in `transaction_impls.cairo` computes:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit
     + l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip)
     + l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit;
``` [1](#0-0) 

All arithmetic is felt arithmetic, i.e., modular arithmetic mod the Stark prime P = 2²⁵¹ + 17·2¹⁹² + 1.

The only range constraints enforced on the resource-bound fields come from `pack_resource_bounds`, called during transaction hash computation:

```cairo
assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
assert_nn(resource_bounds.max_price_per_unit);   // only checks >= 0, i.e., in [0, (P-1)/2]
``` [2](#0-1) 

`assert_nn` does **not** bound `max_price_per_unit` to any practical upper limit — it can be as large as `(P-1)/2 ≈ 2²⁵⁰`. `compute_max_possible_fee` itself applies no additional range checks.

`charge_fee` then gates all fee logic on a single zero-check:

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);
if (max_fee == 0) {
    return ();
}
``` [3](#0-2) 

If `max_fee` evaluates to 0 mod P, the function returns immediately — no ERC-20 transfer is executed, no fee is charged, and no lower-bound check is performed.

---

### Impact Explanation

A user can execute an invoke, declare, or deploy-account transaction with an arbitrarily large L2 gas budget while paying zero fees. The sequencer performs no ERC-20 debit, so it receives no compensation for the computational work. This constitutes a **direct loss of funds** for the sequencer/fee recipient on every such transaction.

Additionally, if this is exploited at scale, the sequencer processes unbounded work for free, which can degrade or halt block production — mapping to the **network not being able to confirm new transactions** impact.

---

### Likelihood Explanation

The Stark prime P is public. Computing `(P-1)/2` is trivial. The crafted resource bounds pass all on-chain validation checks (`assert_nn_le`, `assert_nn`, transaction hash verification). The sequencer's off-chain fee estimator is typically implemented in Python or Rust using arbitrary-precision integers; it would compute the product sum as a large positive integer (≈ P), not 0, and would therefore accept the transaction as fee-bearing. The OS then computes the same sum mod P and obtains 0, silently skipping the fee transfer. No privileged role is required — any transaction sender can craft this.

---

### Recommendation

Add explicit upper-bound range checks on `max_price_per_unit` inside `compute_max_possible_fee` (or enforce them in `pack_resource_bounds` with a tighter bound such as `2**128 - 1`) so that no product `max_amount * max_price_per_unit` can exceed P. Alternatively, assert `max_fee != 0` for all non-bootstrap V3 transactions before returning from `charge_fee`, mirroring the guard already present in the bootstrap declare path.

---

### Proof of Concept

Let P = 2²⁵¹ + 17·2¹⁹² + 1 (the Stark prime).

Craft a V3 transaction with:

| Resource | `max_amount` | `max_price_per_unit` |
|---|---|---|
| L1 gas | 2 | (P−1)/2 |
| L2 gas | N (e.g., 10⁶) | 0 |
| L1 data gas | 1 | 1 |

`tip = 0`

**Validation passes:**
- `assert_nn_le(2, 2⁶⁴−1)` ✓
- `assert_nn((P−1)/2)` ✓ — value is exactly at the upper bound of `[0,(P−1)/2]`
- `assert_nn_le(10⁶, 2⁶⁴−1)` ✓
- `assert_nn(0)` ✓
- `assert_nn_le(1, 2⁶⁴−1)` ✓, `assert_nn(1)` ✓

**`compute_max_possible_fee` result (mod P):**

```
2 · (P−1)/2  +  10⁶ · (0 + 0)  +  1 · 1
= (P−1)      +  0               +  1
= P
≡ 0  (mod P)
```

**`get_initial_user_gas_bound` result:**

```
l2_gas_bounds.max_amount = 10⁶
``` [4](#0-3) 

The transaction is granted 10⁶ units of L2 gas and executes normally. `charge_fee` computes `max_fee = 0` and returns at line 123–125 without transferring any tokens. The sequencer receives zero fee for a fully executed transaction.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L75-78)
```text
func get_initial_user_gas_bound(common_tx_fields: CommonTxFields*) -> felt {
    assert common_tx_fields.n_resource_bounds = 3;
    return common_tx_fields.resource_bounds[L2_GAS_INDEX].max_amount;
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L121-125)
```text
    let max_fee = compute_max_possible_fee(tx_info=tx_info);

    if (max_fee == 0) {
        return ();
    }
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
