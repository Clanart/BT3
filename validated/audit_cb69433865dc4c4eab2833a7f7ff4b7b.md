### Title
Unbounded `max_fee` Arithmetic in `compute_max_possible_fee` Causes Unprovable Blocks — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` multiplies user-supplied `max_amount` (bounded to `[0, 2^64-1]`) by `max_price_per_unit` (bounded only to `[0, 2^128-1]` by `assert_nn`), producing a felt result that can reach ~3 × 2^192. This value is then passed as the upper bound of `assert_nn_le(actual_fee, max_fee)` in `charge_fee`. Because `assert_nn_le` internally calls `assert_nn(max_fee - actual_fee)`, which requires the argument to fit in a range-check cell (`[0, 2^128-1]`), any transaction whose computed `max_fee` exceeds 2^128 is structurally unprovable. If the sequencer includes such a transaction in a block, the OS proof for that block cannot be generated, halting the network.

---

### Finding Description

**`pack_resource_bounds` in `transaction_hash.cairo`** enforces:

```cairo
assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);   // max_amount ≤ 2^64-1
assert_nn(resource_bounds.max_price_per_unit);            // max_price_per_unit ≤ 2^128-1
``` [1](#0-0) 

`assert_nn(x)` places `x` in a range-check cell, bounding it to `[0, 2^128-1]`. So `max_price_per_unit` is allowed up to `2^128 - 1`.

**`compute_max_possible_fee` in `transaction_impls.cairo`** then computes:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit
     + l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip)
     + l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit;
``` [2](#0-1) 

With `max_amount = 2^64-1` and `max_price_per_unit = 2^128-1`, a single term evaluates to `(2^64-1)*(2^128-1) ≈ 2^192`. The sum of three such terms can reach ~3 × 2^192, which is a valid felt (< PRIME ≈ 2^251) but far exceeds the range-check bound of 2^128.

**`charge_fee` then calls:**

```cairo
assert_nn_le(calldata.amount.low, max_fee);
``` [3](#0-2) 

`assert_nn_le(a, b)` is implemented as `assert_nn(a); assert_nn(b - a)`. When `max_fee > 2^128`, the value `max_fee - actual_fee` also exceeds 2^128 and cannot be placed in a range-check cell. The Cairo proof for this step is unsatisfiable — the block containing this transaction cannot be proven.

---

### Impact Explanation

**High — Network not being able to confirm new transactions (total network shutdown).**

If a block containing a transaction with `max_fee > 2^128` is sequenced, the StarkNet OS proof for that block fails at the `assert_nn_le` constraint in `charge_fee`. No valid STARK proof can be generated for the block. The network cannot finalize the block, halting transaction confirmation. Recovery requires the sequencer to re-sequence the block without the offending transaction, which may not be possible if the sequencer's off-chain validation does not mirror this OS-level constraint.

---

### Likelihood Explanation

**Medium.** An unprivileged user submitting a V3 transaction controls `max_amount` and `max_price_per_unit` directly. Setting `max_amount = 2^32` and `max_price_per_unit = 2^97` yields `max_fee ≈ 2^129 > 2^128` — a threshold easily crossed with economically plausible-looking values. The sequencer's off-chain mempool validation may not replicate the exact range-check constraint on `max_fee - actual_fee`, since the OS Cairo code is the authoritative execution layer. If the sequencer's Rust-side fee validation only checks `actual_fee ≤ max_fee` as a raw integer comparison (not as a range-check-bounded operation), it would accept the transaction while the OS proof would reject it.

---

### Recommendation

In `compute_max_possible_fee`, add an explicit upper-bound assertion on the returned value before it is used in `assert_nn_le`:

```cairo
let max_fee = l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit
            + l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip)
            + l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit;
assert_nn_le(max_fee, MAX_FEE_BOUND);  // e.g., 2^128 - 1
return max_fee;
```

Alternatively, tighten the bound on `max_price_per_unit` in `pack_resource_bounds` from `assert_nn` (≤ 2^128-1) to `assert_nn_le(resource_bounds.max_price_per_unit, MAX_PRICE_BOUND)` where `MAX_PRICE_BOUND` is chosen so that `max_amount * MAX_PRICE_BOUND` fits within 2^128. [4](#0-3) 

---

### Proof of Concept

1. Attacker constructs a V3 `invoke` transaction with:
   - `max_amount` (L1 gas) = `2^32` (= 4,294,967,296 — a plausible gas limit)
   - `max_price_per_unit` (L1 gas) = `2^97` (= ~158 trillion wei/gas — high but not obviously invalid to off-chain checks)
   - All other resource bounds set to 0.

2. `pack_resource_bounds` accepts this: `assert_nn_le(2^32, 2^64-1)` ✓ and `assert_nn(2^97)` ✓ (2^97 < 2^128).

3. `compute_max_possible_fee` returns `2^32 * 2^97 = 2^129 > 2^128`.

4. `charge_fee` calls `assert_nn_le(actual_fee, 2^129)`. Internally this calls `assert_nn(2^129 - actual_fee)`. Since `2^129 - actual_fee > 2^128`, the range-check constraint is unsatisfiable.

5. The OS proof for any block containing this transaction cannot be generated. The network halts on that block. [5](#0-4) [6](#0-5)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L120-135)
```text
    local tx_info: TxInfo* = tx_execution_context.execution_info.tx_info;
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
