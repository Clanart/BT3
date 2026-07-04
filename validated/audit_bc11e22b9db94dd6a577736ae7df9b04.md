### Title
Unchecked Felt Multiplication in `compute_max_possible_fee` Allows Field-Prime Wrapping to Produce Distorted Fee Cap — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` multiplies user-controlled `max_amount` and `max_price_per_unit` fields as raw Cairo `felt` values without enforcing an upper bound on `max_price_per_unit`. Because Cairo arithmetic is modular over the field prime P ≈ 2²⁵¹, a crafted `max_price_per_unit` value causes the product to wrap around P, yielding a distorted (arbitrarily small) `max_fee`. The OS then enforces that the actual charged fee cannot exceed this wrapped value, allowing a transaction to execute for nearly zero cost.

---

### Finding Description

**Root cause — missing upper bound on `max_price_per_unit`:**

In `transaction_hash/transaction_hash.cairo`, `pack_resource_bounds` validates:

```cairo
assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);   // max_amount ∈ [0, 2^64-1]
assert_nn(resource_bounds.max_price_per_unit);            // only: max_price_per_unit ≥ 0
```

`assert_nn` in Cairo only checks that the value is in `[0, P/2)` (the "non-negative" half of the field). It does **not** bound `max_price_per_unit` to any economically meaningful range such as `[0, 2^128 - 1]`. [1](#0-0) 

**Vulnerable computation — unchecked product in `compute_max_possible_fee`:**

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
    (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
    l1_data_gas_bounds.max_price_per_unit;
```

With `max_amount ≤ 2^64 - 1` and `max_price_per_unit ≤ P/2 ≈ 2^250`, the product can reach `(2^64 - 1) × 2^250 ≈ 2^314`, which wraps around P multiple times. The result modulo P is an arbitrary value in `[0, P)`. [2](#0-1) 

**Fee enforcement uses the wrapped value:**

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);
if (max_fee == 0) {
    return ();
}
...
assert_nn_le(calldata.amount.low, max_fee);
```

If `max_fee` wraps to 0, fee charging is skipped entirely. If it wraps to a small non-zero value, the sequencer is constrained by the OS to charge at most that tiny amount. [3](#0-2) 

---

### Impact Explanation

**Direct loss of funds.** The OS program is the authoritative source of fee enforcement in the StarkNet proof. When `compute_max_possible_fee` returns a wrapped-to-small value, the Cairo constraint `assert_nn_le(calldata.amount.low, max_fee)` legally permits the sequencer to charge only that tiny amount. The on-chain verifier accepts the resulting proof as valid. The sequencer/protocol loses the fee revenue that should have been collected for executing the transaction. Because P is prime, for any non-zero `max_amount`, the attacker can compute `max_price_per_unit = k × max_amount⁻¹ mod P` (for any target residue `k`) to steer the product to any desired small value.

---

### Likelihood Explanation

An unprivileged V3 transaction sender controls `max_amount`, `max_price_per_unit`, and `tip` directly in the transaction fields. The only protocol-level validation on `max_price_per_unit` is `assert_nn` (non-negativity), which is enforced during hash computation and passes for any value in `[0, P/2)`. The attacker needs only to solve a modular arithmetic equation (trivial given P is prime) to choose a `max_price_per_unit` that causes wrapping. No privileged access, leaked keys, or external dependencies are required. The sequencer may reject such transactions off-chain, but the OS program itself provides no protocol-level guard, making this a reachable code path whenever a sequencer does not independently validate `max_price_per_unit` bounds before inclusion.

---

### Recommendation

In `pack_resource_bounds`, replace the loose `assert_nn` with a strict upper-bound check matching the protocol specification (e.g., `2^128 - 1`):

```cairo
// Before (insufficient):
assert_nn(resource_bounds.max_price_per_unit);

// After (safe):
assert_nn_le(resource_bounds.max_price_per_unit, 2 ** 128 - 1);
```

This ensures that `max_amount × max_price_per_unit ≤ (2^64 - 1) × (2^128 - 1) < 2^192 ≪ P`, making field-prime wrapping impossible in `compute_max_possible_fee`. [4](#0-3) 

---

### Proof of Concept

1. Attacker constructs a V3 invoke transaction with:
   - `max_amount = 1` (for L1 gas, passes `assert_nn_le(_, 2^64-1)`)
   - `max_price_per_unit = P - 1` (passes `assert_nn` since `P - 1 > P/2`... wait, `assert_nn` checks `[0, P/2)`)

   More precisely: choose `max_amount = 2`, `max_price_per_unit = (P + 1) / 2`. Then `max_amount × max_price_per_unit = P + 1 ≡ 1 (mod P)`. The value `(P+1)/2` must be `≤ P/2` to pass `assert_nn` — use `max_amount = 2` and `max_price_per_unit = (P+1)/2 - 1` to get product `≡ P - 1 ≡ -1 (mod P)`, which in unsigned representation is `P - 1` (large). Adjust: choose `max_price_per_unit` such that `2 × max_price_per_unit mod P = 1`, i.e., `max_price_per_unit = (P+1)/2`. Since P is odd, `(P+1)/2` is an integer. Check: `(P+1)/2 < P/2`? No, `(P+1)/2 > P/2`. So it fails `assert_nn`.

   Correct approach: `max_amount = 3`, target product = 1. Need `max_price_per_unit = 3⁻¹ mod P`. Since `3⁻¹ mod P < P/3 < P/2`, it passes `assert_nn`. Then `3 × 3⁻¹ mod P = 1`. `compute_max_possible_fee` returns 1 (for L1 gas component alone, assuming other components are 0).

2. The transaction hash is computed correctly (all hash-time checks pass).
3. The sequencer includes the transaction.
4. The OS executes `compute_max_possible_fee` → returns 1.
5. `assert_nn_le(calldata.amount.low, 1)` constrains the fee to at most 1 wei.
6. The transaction executes for 1 wei regardless of actual gas consumed.
7. The proof is valid; the on-chain verifier accepts it; the state transition is applied with a 1-wei fee deduction. [5](#0-4) [1](#0-0)

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
