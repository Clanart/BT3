### Title
Unchecked Arithmetic in `compute_max_possible_fee` Produces a Value Exceeding the Range-Check Limit, Permanently Breaking Fee Invariant for Any Block Containing Such a Transaction — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` multiplies `max_amount` (bounded to `[0, 2^64 - 1]`) by `max_price_per_unit` (bounded only to `[0, 2^128 - 1]` by `assert_nn`) without verifying the product fits within the Cairo range-check limit of `2^128`. The result is immediately passed to `assert_nn_le(actual_fee, max_fee)`, which requires both arguments to be in `[0, 2^128)`. When `max_fee > 2^128 - 1`, this assertion unconditionally fails for every possible `actual_fee`, making any block that contains such a transaction unprovable.

---

### Finding Description

**Root cause — `compute_max_possible_fee` (lines 87–102):**

```cairo
func compute_max_possible_fee(tx_info: TxInfo*) -> felt {
    ...
    return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
        (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
        l1_data_gas_bounds.max_price_per_unit;
}
```

The only upstream validation of the operands occurs in `pack_resource_bounds` (called during transaction-hash computation):

```cairo
func pack_resource_bounds{range_check_ptr}(resource_bounds: ResourceBounds) -> felt {
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);   // max_amount ≤ 2^64 - 1
    assert_nn(resource_bounds.max_price_per_unit);            // max_price_per_unit ∈ [0, 2^128)
    ...
}
```

`assert_nn` only guarantees `max_price_per_unit < 2^128`; it places **no upper bound** tighter than `2^128 - 1`. Therefore the product of a single term can reach:

```
(2^64 - 1) × (2^128 - 1) ≈ 2^192
```

which is far above `2^128 - 1`.

**Invariant check that breaks — `charge_fee` (line 135):**

```cairo
assert_nn_le(calldata.amount.low, max_fee);
```

`assert_nn_le(a, b)` expands to:
1. `assert_nn(a)` → requires `a ∈ [0, 2^128)`
2. `assert_nn(b - a)` → requires `b - a ∈ [0, 2^128)`

If `max_fee > 2^128 - 1`, then for any valid `actual_fee < 2^128`, the difference `max_fee - actual_fee > 2^128 - 1`, so step 2 always fails. There is no value of `actual_fee` the sequencer can supply to satisfy both constraints simultaneously.

**Execution path:**

1. Attacker submits a V3 `invoke` / `declare` / `deploy_account` transaction with `max_price_per_unit` set to a value such that `max_amount × max_price_per_unit > 2^128 - 1`.
2. `pack_resource_bounds` accepts it (only checks `max_price_per_unit ≥ 0`).
3. `compute_max_possible_fee` returns a felt `> 2^128 - 1`.
4. `assert_nn_le(actual_fee, max_fee)` fails unconditionally inside the OS proof.
5. The block containing this transaction cannot be proven; it is invalid.

---

### Impact Explanation

**High — Network not being able to confirm new transactions (total network shutdown).**

If the sequencer's off-chain pre-validation uses Python/Rust integer arithmetic (not Cairo range-check semantics), it will compute `actual_fee ≤ max_fee` as true (both are large integers) and include the transaction. The OS proof then fails at `assert_nn_le`, producing an unprovable block. The sequencer must discard the block and retry. An attacker who can repeatedly inject such transactions can sustain a denial-of-service against block production.

---

### Likelihood Explanation

**Medium.** The sequencer's transaction simulation layer typically validates fee bounds using native integer arithmetic, not Cairo range-check constraints. The discrepancy between "Python integer comparison" and "Cairo `assert_nn_le` with 2^128 range-check limit" is the exact gap the attacker exploits. Any unprivileged account holder can submit a V3 transaction; no special privilege is required.

---

### Recommendation

1. **Short term:** Add an explicit upper-bound check on `max_price_per_unit` in `pack_resource_bounds` (e.g., `assert_nn_le(resource_bounds.max_price_per_unit, 2 ** 64 - 1)`) so that the maximum product of any single term is `(2^64 - 1)^2 ≈ 2^128`, keeping the total sum within range-check bounds.
2. **Long term:** Add a post-computation range check inside `compute_max_possible_fee` asserting the result is in `[0, 2^128 - 1]`, or restructure fee computation to use `Uint256` arithmetic with explicit overflow detection.

---

### Proof of Concept

Craft a V3 invoke transaction with the following resource bounds for the L1-gas slot:

```
max_amount       = 2^64 - 1   (valid: ≤ 2^64 - 1, passes assert_nn_le)
max_price_per_unit = 2^65     (valid: < 2^128, passes assert_nn)
```

Step-by-step:

1. `pack_resource_bounds` accepts both values — no assertion fires.
2. `compute_max_possible_fee` computes:
   ```
   (2^64 - 1) × 2^65 = 2^129 - 2^65  ≈ 2^129
   ```
   This is the returned `max_fee` felt (no modular reduction occurs since `2^129 < P`).
3. Inside `charge_fee`, the sequencer loads `actual_fee = 1000` (a normal fee).
4. `assert_nn_le(1000, 2^129 - 2^65)` executes:
   - `assert_nn(1000)` → passes (`1000 < 2^128`).
   - `assert_nn((2^129 - 2^65) - 1000)` → **fails** (`2^129 - 2^65 - 1000 > 2^128 - 1`).
5. The OS proof is invalid; the block cannot be finalized. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L127-135)
```text
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
