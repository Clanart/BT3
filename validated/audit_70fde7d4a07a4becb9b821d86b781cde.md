### Title
Unbounded `max_price_per_unit` Causes `assert_nn_le` Overflow in `charge_fee`, Enabling Proof Failure / Network Halt — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

The `pack_resource_bounds` function validates `max_price_per_unit` only with `assert_nn` (non-negative, i.e., bounded to `[0, 2^128-1]`), placing no upper bound that would keep `compute_max_possible_fee`'s result within `[0, 2^128-1]`. Because `charge_fee` then validates the actual fee with `assert_nn_le(calldata.amount.low, max_fee)` — a check that requires `max_fee` itself to be in `[0, 2^128-1]` — any transaction whose resource bounds push `max_fee` above `2^128-1` makes the Cairo constraint unsatisfiable. If the sequencer includes such a transaction, no valid STARK proof can be produced for that block, halting the network.

---

### Finding Description

**Step 1 — Missing upper bound in `pack_resource_bounds`** [1](#0-0) 

```cairo
func pack_resource_bounds{range_check_ptr}(resource_bounds: ResourceBounds) -> felt {
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);   // max_amount ≤ 2^64-1
    assert_nn(resource_bounds.max_price_per_unit);            // only: ≥ 0, i.e. ≤ 2^128-1
    ...
}
```

`assert_nn` uses the 128-bit range-check builtin, so it only guarantees `max_price_per_unit ∈ [0, 2^128-1]`. There is **no upper bound** tighter than `2^128-1`.

**Step 2 — `compute_max_possible_fee` can exceed `2^128-1`** [2](#0-1) 

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit
     + l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip)
     + l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit;
```

With `max_amount = 2^64-1` and `max_price_per_unit = 2^128-1` for each resource, the sum reaches approximately `3 × 2^192`, which is a valid felt (below the Stark prime `P ≈ 2^251`) but **far above `2^128-1`**.

**Step 3 — `charge_fee` uses `assert_nn_le` which requires `max_fee ≤ 2^128-1`** [3](#0-2) 

```cairo
local low_actual_fee;
%{ LoadActualFee %}
...
assert_nn_le(calldata.amount.low, max_fee);
```

`assert_nn_le(a, b)` is implemented as two range-check assertions:
- `assert_nn(a)` → `a ∈ [0, 2^128)`
- `assert_nn(b - a)` → `b - a ∈ [0, 2^128)`

When `max_fee ≈ 3 × 2^192`, for any valid 128-bit `actual_fee`:

```
max_fee - actual_fee ≈ 3 × 2^192  >>  2^128 - 1
```

The second range-check **fails unconditionally**. The Cairo constraint is unsatisfiable; no valid proof can be generated for the block containing this transaction.

---

### Impact Explanation

If a transaction with `max_fee > 2^128-1` is included in a block, the OS Cairo program raises an unsatisfiable constraint during `charge_fee`. The STARK prover cannot produce a valid proof for that block. The network cannot confirm the block, constituting a **total network halt** — matching the allowed impact "High: Network not being able to confirm new transactions."

---

### Likelihood Explanation

The attacker is an unprivileged transaction sender. They craft a V3 transaction with all three resource bounds set to `max_amount = 2^64-1`, `max_price_per_unit = 2^128-1`. The transaction hash computation and nonce check both succeed normally; the OS only fails later inside `charge_fee`. Whether the sequencer's off-chain simulation catches this depends on whether the simulation faithfully mirrors the OS's `assert_nn_le` semantics for oversized `max_fee`. Because the OS itself imposes no explicit upper-bound guard before `charge_fee`, a sequencer simulation that does not independently cap `max_fee` at `2^128-1` will accept the transaction and include it in a block, triggering the halt.

---

### Recommendation

Add an explicit upper-bound assertion in `pack_resource_bounds` (or in `compute_max_possible_fee`) to ensure the resulting `max_fee` cannot exceed `2^128-1`:

```cairo
// In pack_resource_bounds or compute_max_possible_fee:
assert_nn_le(resource_bounds.max_price_per_unit, MAX_PRICE_PER_UNIT_BOUND);
```

where `MAX_PRICE_PER_UNIT_BOUND` is chosen so that `3 × max_amount × MAX_PRICE_PER_UNIT_BOUND < 2^128`. Alternatively, replace `assert_nn_le` in `charge_fee` with a comparison that correctly handles felt values larger than `2^128-1`.

---

### Proof of Concept

1. Attacker submits a V3 invoke transaction with:
   - L1 gas: `max_amount = 2^64-1`, `max_price_per_unit = 2^128-1`
   - L2 gas: `max_amount = 2^64-1`, `max_price_per_unit = 2^128-1`, `tip = 2^64-1`
   - L1 data gas: `max_amount = 2^64-1`, `max_price_per_unit = 2^128-1`

2. `pack_resource_bounds` passes for each bound (`assert_nn_le(max_amount, 2^64-1)` ✓, `assert_nn(max_price_per_unit)` ✓).

3. `compute_max_possible_fee` returns:
   ```
   (2^64-1)(2^128-1) + (2^64-1)(2^128-1 + 2^64-1) + (2^64-1)(2^128-1)
   ≈ 3 × 2^192  (valid felt, < P)
   ```

4. Sequencer's simulation accepts the transaction (no OS-level guard rejects it at this stage).

5. Sequencer includes the transaction in a block and invokes the OS prover.

6. OS reaches `charge_fee` → `assert_nn_le(actual_fee, 3×2^192)`:
   - For any `actual_fee ∈ [0, 2^128-1]`: `3×2^192 - actual_fee > 2^128-1` → range-check fails.
   - If sequencer sets `actual_fee = 3×2^192` to pass `assert_nn_le`: `Uint256(low=3×2^192, high=0)` is an invalid 256-bit value; the ERC-20 transfer reverts; `non_reverting_select_execute_entry_point_func` asserts `is_reverted = 0` → fails.

7. In either case the Cairo program is unsatisfiable. No valid STARK proof can be produced. The block cannot be confirmed. **Network halt.** [1](#0-0) [4](#0-3) [3](#0-2)

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
