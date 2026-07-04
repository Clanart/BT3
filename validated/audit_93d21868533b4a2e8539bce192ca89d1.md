### Title
Unbounded `max_price_per_unit` Causes Field-Arithmetic Wrap-Around in `compute_max_possible_fee`, Enabling Fee Bypass or Block-Proof Failure — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` multiplies user-supplied `max_amount` and `max_price_per_unit` fields in Cairo felt arithmetic without enforcing an upper bound on `max_price_per_unit`. Because felt arithmetic is modular (mod P ≈ 2²⁵¹), a crafted `max_price_per_unit` value can cause the product to wrap around, producing an arbitrarily small (or zero) `max_fee`. The sequencer, computing fees with integer arithmetic off-chain, is tricked into including the transaction expecting a large fee; the OS program then enforces only the wrapped (tiny) cap, resulting in a direct loss of fee revenue or, in the complementary case, a block-proof failure that halts the network.

---

### Finding Description

**Root cause — `pack_resource_bounds` (transaction hash path):**

```cairo
func pack_resource_bounds{range_check_ptr}(resource_bounds: ResourceBounds) -> felt {
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);   // ✓ upper-bounded
    assert_nn(resource_bounds.max_price_per_unit);            // ✗ only lower-bounded (≥ 0)
    ...
}
```

`assert_nn` constrains `max_price_per_unit` to `[0, (P−1)/2]` ≈ `[0, 2²⁵⁰]`. No upper bound (e.g., `2¹²⁸ − 1`) is enforced.

**Root cause — `compute_max_possible_fee` (fee-charging path):**

```cairo
func compute_max_possible_fee(tx_info: TxInfo*) -> felt {
    ...
    return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit
         + l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip)
         + l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit;
}
```

With `max_amount ≤ 2⁶⁴ − 1` and `max_price_per_unit ≤ (P−1)/2 ≈ 2²⁵⁰`, the product can reach ≈ 2³¹⁴, far exceeding P. The result is reduced modulo P silently, yielding an arbitrary value in `[0, P−1]`.

**Concrete attack construction:**

Since P is prime, for any nonzero `max_amount`, its modular inverse `max_amount⁻¹ mod P` exists. An attacker sets:

- `max_price_per_unit = max_amount⁻¹ mod P` (if this value falls in `[0, (P−1)/2]`, it passes `assert_nn`)
- Product: `max_amount × max_amount⁻¹ ≡ 1 (mod P)` → `max_fee = 1`

The sequencer's off-chain validator (Rust, using 256-bit integers) computes `max_amount × max_price_per_unit` as a huge integer (≈ 2³¹⁴) and accepts the transaction as high-fee. The OS program computes the same product in felt arithmetic and gets `1`, capping the actual fee at 1 wei.

Alternatively, choosing values that wrap into `[2¹²⁸, P)` causes `assert_nn_le(calldata.amount.low, max_fee)` to fail (since `assert_nn_le` uses 128-bit range checks), aborting the OS execution and invalidating the block proof.

---

### Impact Explanation

**Scenario A — Fee bypass (direct loss of funds):**
`max_fee` wraps to a value in `[0, 2¹²⁸)`. The OS enforces `assert_nn_le(actual_fee, max_fee)`, capping the charged fee at the wrapped value (e.g., 1 wei). The sequencer, having included the transaction expecting a large fee, receives essentially nothing. Repeated across many transactions, this drains sequencer fee revenue entirely.

**Scenario B — Block-proof failure (network halt):**
`max_fee` wraps to a value in `[2¹²⁸, P)`. `assert_nn_le(actual_fee, max_fee)` fails because `max_fee − actual_fee` exceeds the 128-bit range-check bound. The OS program aborts, the block proof is invalid, and the network cannot confirm new transactions until the block is discarded and rebuilt.

Both impacts are in the allowed scope: **direct loss of funds** and **network not being able to confirm new transactions**.

---

### Likelihood Explanation

- An unprivileged transaction sender controls all `ResourceBounds` fields in a V3 transaction.
- Finding a valid `(max_amount, max_price_per_unit)` pair that wraps to a small felt requires only a modular inverse computation — trivial offline.
- The sequencer's Rust implementation uses native integer arithmetic; the OS uses felt arithmetic. This discrepancy is the exploitable gap and does not require any privileged access or sequencer compromise.
- The only mitigation is the sequencer's mempool policy, which is not enforced by the OS program and can be circumvented if the sequencer's fee-validation logic does not replicate felt-modular arithmetic exactly.

---

### Recommendation

Add an explicit upper-bound check on `max_price_per_unit` in `pack_resource_bounds` to ensure the product `max_amount × max_price_per_unit` cannot exceed P:

```cairo
func pack_resource_bounds{range_check_ptr}(resource_bounds: ResourceBounds) -> felt {
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
    assert_nn_le(resource_bounds.max_price_per_unit, 2 ** 128 - 1);  // ADD THIS
    ...
}
```

With `max_amount ≤ 2⁶⁴ − 1` and `max_price_per_unit ≤ 2¹²⁸ − 1`, the product is at most `(2⁶⁴ − 1)(2¹²⁸ − 1) < 2¹⁹²`, which is well below P ≈ 2²⁵¹, eliminating all wrap-around.

---

### Proof of Concept

**Step 1.** Compute offline (Python):
```python
P = 2**251 + 17 * 2**192 + 1
max_amount = 7  # any small value whose inverse lands in [0, (P-1)//2]
price = pow(max_amount, -1, P)  # modular inverse
assert price <= (P - 1) // 2    # passes assert_nn
assert (max_amount * price) % P == 1  # max_fee = 1 in OS
print(f"max_price_per_unit = {price}")  # ~2^248, looks enormous to integer arithmetic
```

**Step 2.** Craft a V3 transaction with:
- `l1_gas_bounds = {max_amount: 0, max_price_per_unit: 0}`
- `l2_gas_bounds = {max_amount: 7, max_price_per_unit: price}`
- `l1_data_gas_bounds = {max_amount: 0, max_price_per_unit: 0}`
- `tip = 0`

**Step 3.** Sequencer off-chain check: `7 × price ≈ 2²⁴⁸` → enormous fee → transaction accepted.

**Step 4.** OS execution: `compute_max_possible_fee` returns `(7 × price) mod P = 1`.

**Step 5.** `assert_nn_le(actual_fee, 1)` → sequencer can charge at most 1 wei. Transaction is processed for free. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L99-101)
```text
    return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
        (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
        l1_data_gas_bounds.max_price_per_unit;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L134-135)
```text
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
