### Title
Field Arithmetic Overflow in `compute_max_possible_fee` Allows Fee-Free Transaction Execution — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` performs unchecked felt-field multiplication of user-controlled `max_amount` and `max_price_per_unit` resource bound values. Because Cairo arithmetic is modulo the StarkNet prime `P`, a crafted transaction can make the function return exactly `0`, causing `charge_fee` to skip fee collection entirely. The result is that a transaction executes with zero fee charged — a direct loss of funds for the sequencer.

---

### Finding Description

`compute_max_possible_fee` computes the fee ceiling as a sum of products of user-supplied resource bound fields:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit
     + l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip)
     + l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit;
``` [1](#0-0) 

All arithmetic is done in the felt field modulo `P = 2^251 + 17·2^192 + 1`. No overflow guard is applied to the products or their sum.

The only upstream validation of resource bound fields occurs in `pack_resource_bounds` (called during hash computation):

```cairo
assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
assert_nn(resource_bounds.max_price_per_unit);
``` [2](#0-1) 

`assert_nn` only enforces `max_price_per_unit ∈ [0, (P−1)/2]`. It does **not** bound the product `max_amount × max_price_per_unit` below `P`. With `max_amount ≤ 2^64 − 1` and `max_price_per_unit ≤ (P−1)/2 ≈ 2^250`, the product can reach `≈ 2^314`, wrapping around `P` many times.

`charge_fee` then uses the result directly:

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);
if (max_fee == 0) {
    return ();
}
...
assert_nn_le(calldata.amount.low, max_fee);
``` [3](#0-2) 

If `max_fee` wraps to `0`, the function returns immediately and **no fee transfer is executed**. If it wraps to a small value `k`, the actual fee is constrained to `≤ k`, which can be made arbitrarily small.

---

### Impact Explanation

**Direct loss of funds (Critical).** The sequencer is entitled to collect fees for every executed transaction. When `compute_max_possible_fee` wraps to `0`, `charge_fee` exits early and the ERC-20 fee transfer to the sequencer is never performed. The user's transaction is executed and its state changes are committed, but the sequencer receives nothing. This is provably valid from the OS's perspective — the proof will verify — so the sequencer has no recourse.

---

### Likelihood Explanation

Any unprivileged V3 transaction sender can craft resource bounds that trigger the overflow. The values pass all existing validation checks (`assert_nn_le` on `max_amount`, `assert_nn` on `max_price_per_unit`). The sequencer's off-chain fee estimation may use 128-bit or 256-bit arithmetic and compute a large, non-zero max_fee, causing it to accept the transaction into the mempool. The OS then computes `max_fee = 0` in felt arithmetic and skips fee collection. No special privilege, leaked key, or external dependency is required.

---

### Recommendation

Add explicit upper-bound checks on `max_price_per_unit` in `compute_max_possible_fee` (or enforce them in `pack_resource_bounds` and propagate the constraint). Specifically, bound `max_price_per_unit` to a value small enough that `max_amount × max_price_per_unit < P` is guaranteed, e.g.:

```cairo
assert_nn_le(resource_bounds.max_price_per_unit, 2 ** 128 - 1);
```

With `max_amount ≤ 2^64 − 1` and `max_price_per_unit ≤ 2^128 − 1`, the product is at most `≈ 2^192 < P`, eliminating the overflow. Alternatively, use `uint256` arithmetic for the fee computation to make overflow impossible.

---

### Proof of Concept

Let `P = 2^251 + 17·2^192 + 1` (the StarkNet field prime).

Craft a V3 transaction with:

| Field | Value | Passes validation? |
|---|---|---|
| `l1_gas_bounds.max_amount` | `2` | ✓ `assert_nn_le(2, 2^64-1)` |
| `l1_gas_bounds.max_price_per_unit` | `(P−1)/2` | ✓ `assert_nn((P−1)/2)` |
| `l2_gas_bounds.max_amount` | `1` | ✓ |
| `l2_gas_bounds.max_price_per_unit` | `1` | ✓ |
| `tip` | `0` | ✓ `assert_nn_le(0, 2^64-1)` |
| `l1_data_gas_bounds.max_amount` | `0` | ✓ |
| `l1_data_gas_bounds.max_price_per_unit` | `0` | ✓ |

`compute_max_possible_fee` computes:

```
2 × (P−1)/2  +  1 × (1 + 0)  +  0 × 0
= (P − 1)    +  1
= P
≡ 0  (mod P)
```

`charge_fee` receives `max_fee = 0` and returns immediately at:

```cairo
if (max_fee == 0) {
    return ();
}
``` [4](#0-3) 

The transaction executes with zero fee charged. The OS proof is valid. The sequencer receives no compensation.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L99-101)
```text
    return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
        (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
        l1_data_gas_bounds.max_price_per_unit;
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L104-105)
```text
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
    assert_nn(resource_bounds.max_price_per_unit);
```
