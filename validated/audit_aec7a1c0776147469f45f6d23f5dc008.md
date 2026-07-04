### Title
Unbounded `max_price_per_unit` in `compute_max_possible_fee` Causes OS Panic via `assert_nn_le` Range-Check Failure — (File: `execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` computes a felt-arithmetic sum of products of `max_amount` (bounded to [0, 2^64−1]) and `max_price_per_unit` (bounded only to [0, 2^128−1] by `assert_nn`). The product of these two values can reach ~2^192, far exceeding 2^128. The result is then passed directly to `assert_nn_le` inside `charge_fee`, which requires both arguments to be in [0, 2^128). When `max_fee > 2^128`, the range-check builtin fails and the OS cannot produce a valid proof for the block — a network halt.

---

### Finding Description

**Root cause — missing upper bound on `max_price_per_unit`:**

In `pack_resource_bounds` (`transaction_hash/transaction_hash.cairo`, line 103–107):

```cairo
func pack_resource_bounds{range_check_ptr}(resource_bounds: ResourceBounds) -> felt {
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);   // max_amount ≤ 2^64 − 1
    assert_nn(resource_bounds.max_price_per_unit);            // only ≥ 0, i.e. ≤ 2^128 − 1
    ...
}
```

`assert_nn` constrains `max_price_per_unit` to [0, 2^128−1] — no upper bound tighter than 2^128−1 is enforced. [1](#0-0) 

**Propagation — unchecked product in `compute_max_possible_fee`:**

```cairo
func compute_max_possible_fee(tx_info: TxInfo*) -> felt {
    ...
    return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit
         + l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip)
         + l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit;
}
```

Each product can be as large as `(2^64 − 1) × (2^128 − 1) ≈ 2^192`. The sum of three such products can reach ~3 × 2^192, which is far above 2^128 but still below the field modulus P ≈ 2^251 (so no field-level wrap-around occurs — the value is a legitimately large felt). [2](#0-1) 

**Crash site — `assert_nn_le` in `charge_fee`:**

```cairo
assert_nn_le(calldata.amount.low, max_fee);
```

`assert_nn_le(a, b)` internally calls `assert_nn(b − a)`, which uses the range-check builtin to verify `b − a ∈ [0, 2^128)`. When `max_fee > 2^128`, the value `max_fee − actual_fee` exceeds 2^128, the range-check constraint is unsatisfiable, and the OS cannot generate a valid STARK proof for the block. [3](#0-2) 

There is no escape: even if the sequencer sets `low_actual_fee = 0`, the check `assert_nn_le(0, max_fee)` still requires `max_fee ∈ [0, 2^128)` and fails identically.

---

### Impact Explanation

When a V3 transaction with `max_fee > 2^128` is included in a block, the OS cannot produce a valid proof for that block. The sequencer is stuck: it cannot finalize the block, and no subsequent blocks can be proven until the offending transaction is removed. This constitutes a **High — network not being able to confirm new transactions (total network shutdown)**.

---

### Likelihood Explanation

An unprivileged transaction sender can craft a syntactically valid, correctly signed V3 transaction with:

- `l1_gas_bounds.max_amount = 2^64 − 1`
- `l1_gas_bounds.max_price_per_unit = 2^128 − 1`

This alone yields `max_fee ≥ (2^64 − 1) × (2^128 − 1) ≈ 2^192 ≫ 2^128`.

The transaction passes all hash and signature checks. The OS itself does not reject it during the pre-execution validation phase — the panic only occurs inside `charge_fee` at execution time. A sequencer that does not independently enforce `max_fee ≤ 2^128` (a constraint the OS never documents or enforces at ingestion) will include the transaction, triggering the halt.

---

### Recommendation

Add an explicit upper-bound check on `max_price_per_unit` in `pack_resource_bounds` so that the product `max_amount × max_price_per_unit` is guaranteed to fit in [0, 2^128):

```cairo
// max_amount ≤ 2^64 − 1, so to keep product ≤ 2^128 − 1:
assert_nn_le(resource_bounds.max_price_per_unit, 2 ** 64 - 1);
```

Alternatively, add a guard in `charge_fee` before calling `assert_nn_le`:

```cairo
assert_nn_le(max_fee, MAX_FEE_BOUND);  // e.g. MAX_FEE_BOUND = 2^128 − 1
assert_nn_le(calldata.amount.low, max_fee);
```

The tightest fix is at `pack_resource_bounds`, since it is the canonical validation point for resource bound fields. [1](#0-0) 

---

### Proof of Concept

1. Attacker constructs a V3 invoke transaction with:
   - `l1_gas_bounds = { resource: L1_GAS, max_amount: 2^64 − 1, max_price_per_unit: 2^128 − 1 }`
   - `l2_gas_bounds = { resource: L2_GAS, max_amount: 1, max_price_per_unit: 1 }`
   - `l1_data_gas_bounds = { resource: L1_DATA_GAS, max_amount: 1, max_price_per_unit: 1 }`
2. Signs it with a valid account key and submits to the mempool.
3. `compute_max_possible_fee` returns `(2^64 − 1) × (2^128 − 1) + 2 ≈ 2^192`.
4. `charge_fee` calls `assert_nn_le(low_actual_fee, 2^192)`.
5. `assert_nn_le` internally evaluates `assert_nn(2^192 − low_actual_fee)`.
6. The range-check builtin requires the value to be in [0, 2^128); `2^192 − low_actual_fee > 2^128` for any `low_actual_fee < 2^192 − 2^128`.
7. The constraint is unsatisfiable → the OS cannot produce a valid proof → block finalization halts. [4](#0-3) [1](#0-0)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L111-135)
```text
func charge_fee{
    range_check_ptr,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    outputs: OsCarriedOutputs*,
}(block_context: BlockContext*, tx_execution_context: ExecutionContext*) {
    alloc_locals;

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
