### Title
Unbounded `max_price_per_unit` in `pack_resource_bounds` Causes `assert_nn_le` Abort in `charge_fee`, Enabling Network Halt — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo`)

---

### Summary

In `pack_resource_bounds`, `max_price_per_unit` is only validated to be non-negative (`assert_nn`), with no upper-bound check. When `compute_max_possible_fee` multiplies this unbounded user-supplied value by `max_amount`, the resulting `max_fee` can exceed `2^128`. The subsequent `assert_nn_le(calldata.amount.low, max_fee)` in `charge_fee` then unconditionally aborts the Cairo OS program, because the range-check builtin cannot verify values ≥ `2^128`. Any block containing such a transaction cannot be proven, halting the network.

---

### Finding Description

**Root cause — missing upper-bound on `max_price_per_unit`:**

In `pack_resource_bounds`, `max_amount` is correctly bounded to `[0, 2^64 − 1]`, but `max_price_per_unit` is only checked with `assert_nn`, which only enforces `>= 0` (i.e., the range-check builtin bound of `[0, 2^128)`). No upper-bound is enforced. [1](#0-0) 

**Overflow in `compute_max_possible_fee`:**

`compute_max_possible_fee` directly multiplies the user-supplied `max_amount` (up to `2^64 − 1`) by `max_price_per_unit` (up to `2^128 − 1`) with no overflow guard. The maximum product of a single term is `(2^64 − 1) × (2^128 − 1) ≈ 2^192`. Summing three such terms yields a `max_fee` value of up to `~3 × 2^192`, far exceeding `2^128`. [2](#0-1) 

**Hard abort in `charge_fee`:**

`charge_fee` calls `assert_nn_le(calldata.amount.low, max_fee)`. This expands to `assert_nn(max_fee − calldata.amount.low)`, which places the value into the range-check builtin. The range-check builtin enforces `[0, 2^128)`. When `max_fee ≈ 2^192`, the expression `max_fee − calldata.amount.low ≈ 2^192` is outside `[0, 2^128)` for any valid `calldata.amount.low`, causing an unconditional Cairo program abort — not a transaction revert. [3](#0-2) 

**`charge_fee` is always reached for V3 invoke transactions:**

`charge_fee` is called unconditionally at the end of `execute_invoke_function_transaction`, regardless of whether the transaction reverted. [4](#0-3) 

---

### Impact Explanation

When the Cairo OS program aborts, the STARK proof for the block cannot be generated. Any block that includes a transaction with an oversized `max_price_per_unit` becomes unprovable. Since the sequencer must include transactions to advance the chain, and the OS abort is deterministic (not a soft revert), this constitutes a **total network halt**: the network cannot confirm new transactions.

**Impact: High — Network not being able to confirm new transactions (total network shutdown).**

---

### Likelihood Explanation

The attack requires only submitting a standard V3 invoke transaction with `max_price_per_unit` set to any value ≥ `ceil(2^128 / max_amount)` (e.g., `max_price_per_unit = 2^128 − 1`, `max_amount = 1`). This passes all on-chain mempool and hash-computation checks (`pack_resource_bounds` only calls `assert_nn`). No privileged access, leaked keys, or special role is required — any unprivileged transaction sender can trigger this. The attack is cheap (one transaction) and fully deterministic.

---

### Recommendation

Add an explicit upper-bound check on `max_price_per_unit` inside `pack_resource_bounds`, consistent with the bound already applied to `max_amount`:

```cairo
func pack_resource_bounds{range_check_ptr}(resource_bounds: ResourceBounds) -> felt {
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
-   assert_nn(resource_bounds.max_price_per_unit);
+   assert_nn_le(resource_bounds.max_price_per_unit, 2 ** 128 - 1);  // already implicit
    // Better: bound to a value that prevents max_fee overflow, e.g. 2 ** 64 - 1
+   assert_nn_le(resource_bounds.max_price_per_unit, 2 ** 64 - 1);
    ...
}
```

Alternatively, add an explicit overflow guard in `compute_max_possible_fee` by verifying the result fits within `[0, 2^128)` before it is used in `assert_nn_le`. [1](#0-0) 

---

### Proof of Concept

1. Attacker constructs a V3 invoke transaction with:
   - `l1_gas_bounds.max_amount = 1`
   - `l1_gas_bounds.max_price_per_unit = 2^128 − 1`
   - Other resource bounds set to zero.

2. During transaction hash computation, `pack_resource_bounds` is called:
   - `assert_nn_le(1, 2^64 − 1)` → passes
   - `assert_nn(2^128 − 1)` → passes (value is in `[0, 2^128)`)
   - Transaction hash is computed and accepted.

3. Sequencer includes the transaction in a block and runs the OS.

4. `compute_max_possible_fee` computes:
   ```
   max_fee = 1 * (2^128 − 1) + 0 + 0 = 2^128 − 1
   ```
   (Even at exactly `2^128 − 1`, `assert_nn_le(0, 2^128 − 1)` requires `assert_nn(2^128 − 1 − 0)`, which places `2^128 − 1` into the range-check builtin. The range-check builtin checks `[0, 2^128)`, so `2^128 − 1` is the boundary. To guarantee abort, use `max_amount = 2` and `max_price_per_unit = 2^127`, giving `max_fee = 2^128`, which causes `assert_nn(2^128)` to fail unconditionally.)

5. `charge_fee` is called. `max_fee = 2^128 > 0`, so execution continues past the zero-check.

6. `assert_nn_le(calldata.amount.low, 2^128)` → `assert_nn(2^128 − calldata.amount.low)`. For any `calldata.amount.low < 2^128`, the value `2^128 − calldata.amount.low` is in `[1, 2^128]`, and `2^128` itself fails the range-check constraint `[0, 2^128)`.

7. The Cairo OS program aborts. The block proof cannot be generated. The network halts.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L360-362)
```text
    // Charge fee.
    charge_fee(block_context=block_context, tx_execution_context=updated_tx_execution_context);

```
