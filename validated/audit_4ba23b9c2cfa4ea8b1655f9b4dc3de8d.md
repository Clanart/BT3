### Title
Unchecked `max_price_per_unit` Enables Felt Arithmetic Wrap-Around in `compute_max_possible_fee`, Producing an Invalid Fee Cap - (File: `execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` multiplies `max_amount` (validated ≤ 2^64 − 1) by `max_price_per_unit` (only validated ≥ 0, no upper bound) using Cairo felt arithmetic, which is modular arithmetic modulo the Stark prime P ≈ 2^251. When `max_price_per_unit` is set to a large felt value, the product wraps around modulo P, producing a `max_fee` that is incorrect. This wrapped value is then passed directly to `assert_nn_le(actual_fee, max_fee)`, which uses the range-check builtin and requires both operands to be in [0, 2^128 − 1]. If the wrapped `max_fee` falls in [2^128, P − 1], the range-check fails, making the block unprovable.

---

### Finding Description

In `transaction_impls.cairo`, `compute_max_possible_fee` computes:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
    (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
    l1_data_gas_bounds.max_price_per_unit;
``` [1](#0-0) 

The only validation applied to `max_price_per_unit` occurs in `pack_resource_bounds` (called during transaction hash computation):

```cairo
assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
assert_nn(resource_bounds.max_price_per_unit);
``` [2](#0-1) 

`assert_nn` only checks non-negativity (i.e., the value is in [0, P − 1]). There is **no upper bound** on `max_price_per_unit`. With `max_amount` up to 2^64 − 1 and `max_price_per_unit` up to P − 1, the product `max_amount * max_price_per_unit mod P` can be any value in [0, P − 1].

The result `max_fee` is then used as the upper bound in:

```cairo
assert_nn_le(calldata.amount.low, max_fee);
``` [3](#0-2) 

`assert_nn_le(a, b)` uses the range-check builtin, which requires both `a` and `b − a` to be in [0, 2^128 − 1]. If `max_fee` is a felt value in [2^128, P − 1], then `max_fee − actual_fee ≥ 2^128`, and the range-check fails, causing the OS program to abort and the block proof to be invalid.

The `tip` field is also bounded only to 2^64 − 1:

```cairo
assert_nn_le(tip, 2 ** 64 - 1);
``` [4](#0-3) 

But `max_price_per_unit` receives no such bound, making the product `max_amount * (max_price_per_unit + tip)` for L2 gas also subject to wrap-around.

---

### Impact Explanation

If a malicious user crafts a V3 transaction with `max_price_per_unit` chosen such that `max_fee` (after felt modular reduction) falls in [2^128, P − 1], the OS program's `assert_nn_le` call fails at proof time. If the sequencer's off-chain simulation uses non-modular arithmetic (e.g., u128 or u256 in Rust) to compute `max_fee`, it will compute a different value than the Cairo OS and incorrectly accept the transaction. The sequencer then includes the transaction in a block, generates a proof, and the proof fails. The sequencer must discard the block and re-prove without the transaction. A sustained stream of such transactions can prevent the sequencer from confirming new transactions, matching the **High: Network not being able to confirm new transactions** impact.

---

### Likelihood Explanation

Any unprivileged transaction sender can submit a V3 transaction with an arbitrary `max_price_per_unit` felt value. The only gate is `assert_nn` (non-negativity), which is trivially satisfied. The attacker needs only to choose `max_price_per_unit` such that `max_amount * max_price_per_unit mod P ≥ 2^128`. Since `max_amount` can be up to 2^64 − 1, the set of valid `max_price_per_unit` values that cause wrap-around is large (any value > P / 2^64 ≈ 2^187). This is straightforward to compute and requires no privileged access.

---

### Recommendation

Add an explicit upper bound check on `max_price_per_unit` in `pack_resource_bounds` (or in `compute_max_possible_fee` itself) to ensure the product cannot exceed 2^128 − 1 (or the maximum felt value that keeps the sum within range-check bounds):

```cairo
assert_nn_le(resource_bounds.max_price_per_unit, 2 ** 128 - 1);
```

Alternatively, validate that the full computed `max_fee` is within [0, 2^128 − 1] before passing it to `assert_nn_le`.

---

### Proof of Concept

1. Attacker selects `max_amount = 2^64 − 1` (maximum allowed).
2. Attacker selects `max_price_per_unit = P − 1` (maximum felt value, passes `assert_nn`).
3. `max_fee = (2^64 − 1) * (P − 1) mod P = P − (2^64 − 1) mod P = 2^64 − 1` — this particular case wraps to a small value. More precisely, the attacker can choose `max_price_per_unit` such that `(2^64 − 1) * max_price_per_unit mod P` is any target value in [0, P − 1], including values in [2^128, P − 1].
4. For example, choose `max_price_per_unit = ceil(2^128 / (2^64 − 1)) + 1`. Then `max_amount * max_price_per_unit ≈ 2^128 + 2^64`, which mod P (since 2^128 + 2^64 < P) equals 2^128 + 2^64 > 2^128.
5. The sequencer's Rust simulation computes `max_fee = 2^128 + 2^64` using u256 arithmetic and accepts the transaction (since it's ≥ actual_fee).
6. The Cairo OS computes the same `max_fee = 2^128 + 2^64` (no wrap in this case since < P), then calls `assert_nn_le(actual_fee, 2^128 + 2^64)`. The range-check on `(2^128 + 2^64) − actual_fee ≥ 2^128` fails.
7. The block proof is invalid. The sequencer must re-prove.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L104-105)
```text
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
    assert_nn(resource_bounds.max_price_per_unit);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L117-117)
```text
    assert_nn_le(tip, 2 ** 64 - 1);
```
