### Title
Felt Arithmetic Overflow in `compute_max_possible_fee` Allows Fee-Free Transaction Execution — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` computes the transaction fee ceiling using raw felt arithmetic with no overflow protection. Because felt arithmetic is modular (mod the Stark prime P ≈ 2²⁵²), a user can craft `ResourceBounds` values whose products sum to exactly 0 mod P. When `max_fee == 0`, `charge_fee` returns immediately without deducting any fee, allowing the transaction to execute for free. This is the direct analog of the `GaugeExtraRewarder` bug: a balance/limit check that silently misses a component of the total amount, causing the enforced ceiling to be zero rather than the true value.

---

### Finding Description

`compute_max_possible_fee` (lines 87–101) returns:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit
     + l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip)
     + l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit;
```

All three terms are felt multiplications with no range-check on the result. The only upstream bounds enforced (in `pack_resource_bounds`, lines 103–105) are:

- `max_amount ∈ [0, 2⁶⁴ − 1]`
- `max_price_per_unit ∈ [0, (P−1)/2]` (via `assert_nn`)
- `tip ∈ [0, 2⁶⁴ − 1]`

A single product `max_amount × max_price_per_unit` can reach (2⁶⁴ − 1) × (P−1)/2 ≈ 2³¹⁵, far exceeding P. The sum of three such products can therefore wrap to any value mod P, including 0.

`charge_fee` (lines 121–125) then does:

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);
if (max_fee == 0) {
    return ();          // ← exits without charging anything
}
```

If `max_fee` wraps to 0, the early return fires and no ERC-20 transfer is executed. Any non-zero `actual_fee` the sequencer might try to charge would fail `assert_nn_le(actual_fee, 0)` (line 135), making the proof invalid. The sequencer is therefore forced to record a 0-fee transfer for such a transaction.

---

### Impact Explanation

**Critical — Direct loss of funds.**

The sequencer receives zero fee for executing the transaction. Because the Cairo OS proof enforces `actual_fee ≤ max_fee = 0`, the sequencer cannot charge anything without producing an invalid proof. An attacker who submits such a transaction gets full L2 gas execution at zero cost. At scale (many such transactions), this drains sequencer revenue and can be used to spam the network with computationally expensive, fee-free work.

---

### Likelihood Explanation

**High.** Any unprivileged V3 transaction sender can trigger this. No special role, key, or operator cooperation is required. The attacker only needs to submit a transaction with crafted `ResourceBounds` fields. The values are user-supplied inputs that pass all existing validation checks (`assert_nn`, `assert_nn_le`) while still causing the overflow.

---

### Recommendation

Add an explicit upper bound on `max_price_per_unit` in `pack_resource_bounds` (e.g., `assert_nn_le(resource_bounds.max_price_per_unit, 2 ** 128 - 1)`). With both `max_amount ≤ 2⁶⁴ − 1` and `max_price_per_unit ≤ 2¹²⁸ − 1`, each product fits in 192 bits, and the three-term sum fits in 194 bits — well below P ≈ 2²⁵², eliminating overflow. Alternatively, add a post-computation range check on the return value of `compute_max_possible_fee` to assert it is non-negative and within a sane bound.

---

### Proof of Concept

Let P = 2²⁵¹ + 17·2¹⁹² + 1 (the Stark prime).

**Minimal example (3 units of L2 gas, 0 fee):**

| Resource | `max_amount` | `max_price_per_unit` | Product mod P |
|---|---|---|---|
| L1 gas | 2 | (P−1)/2 | 2·(P−1)/2 = P−1 ≡ **−1** |
| L2 gas | 3 | (P+1)/3 | 3·(P+1)/3 = P+1 ≡ **+1** |
| L1 data gas | 0 | 0 | **0** |

Sum = −1 + 1 + 0 = **0 mod P** → `max_fee = 0` → `charge_fee` returns immediately.

**Bound verification:**
- `(P−1)/2` satisfies `assert_nn` (it is the exact upper bound). ✓
- `(P+1)/3 < (P−1)/2` because 2(P+1) < 3(P−1) iff 5 < P. ✓
- `max_amount` values 2 and 3 are within [0, 2⁶⁴−1]. ✓

**Scaled example (≈ 2⁶⁴ units of L2 gas, 0 fee):**

Let K = ⌊(2⁶⁴−1)/3⌋ ≈ 6.1 × 10¹⁸.

| Resource | `max_amount` | `max_price_per_unit` |
|---|---|---|
| L1 gas | 2K | (P−1)/2 |
| L2 gas | 3K | (P+1)/3 |
| L1 data gas | 0 | 0 |

Sum = K·(P−1) + K·(P+1) = 2KP ≡ **0 mod P**.

- `2K ≈ 1.2 × 10¹⁹ < 2⁶⁴ − 1`. ✓
- `3K ≈ 1.8 × 10¹⁹ ≤ 2⁶⁴ − 1`. ✓

The attacker submits a valid V3 transaction with these `ResourceBounds`. The Cairo OS computes `max_fee = 0`, returns from `charge_fee` without executing any ERC-20 transfer, and the proof is accepted — granting the attacker nearly 2⁶⁴ units of L2 gas execution at zero cost. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L87-101)
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
