### Title
Missing Overflow Invariant on `compute_max_possible_fee` Allows Fee-Charging Bypass — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` performs unchecked felt-field arithmetic on attacker-controlled resource-bound fields. The result can silently wrap to `0` mod P. `charge_fee` treats a zero result as "no fee due" and returns without charging anything, allowing a transaction sender to execute arbitrary transactions for free.

---

### Finding Description

`compute_max_possible_fee` sums three products of attacker-supplied fields:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit
     + l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip)
     + l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit;
``` [1](#0-0) 

The only upstream bounds enforced on these fields are:

- `max_amount ≤ 2^64 − 1` (via `assert_nn_le` in `pack_resource_bounds`)
- `max_price_per_unit ≥ 0` (via `assert_nn` in `pack_resource_bounds`, meaning the value is in `[0, (P−1)/2]`)
- `tip ≤ 2^64 − 1` (via `assert_nn_le` in `hash_fee_fields`) [2](#0-1) 

Because `max_price_per_unit` can be as large as `(P−1)/2 ≈ 2^250`, the product `max_amount × max_price_per_unit` can easily exceed the field prime P and wrap around. No assertion is made after the computation to verify that the returned `max_fee` is a faithful (non-overflowed) upper bound on the actual fee.

`charge_fee` then uses this value with a critical branch:

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);

if (max_fee == 0) {
    return ();          // ← fee charging skipped entirely
}
...
assert_nn_le(calldata.amount.low, max_fee);
``` [3](#0-2) 

If the overflow causes `max_fee` to equal `0`, the function returns immediately without executing the ERC-20 transfer, so no fee is deducted from the sender's account.

---

### Impact Explanation

**Direct loss of funds (Critical).**

The sequencer/fee recipient receives zero fee for a transaction that consumed real L1/L2/DA gas. Because the OS Cairo program generates the STARK proof, and the proof is accepted by the L1 verifier, this is not a mempool-level bypass — it is a provably valid state transition that permanently omits fee payment. Repeated exploitation drains sequencer revenue and allows unlimited free execution of arbitrary contract logic.

---

### Likelihood Explanation

The exploit requires only a crafted v3 transaction with specific `max_price_per_unit` values — fully within the control of any unprivileged transaction sender. No privileged access, leaked keys, or external dependencies are needed. The field-arithmetic overflow is deterministic and reproducible.

---

### Recommendation

After computing `max_fee`, assert that the result is a valid non-overflowed sum. The simplest approach is to bound `max_price_per_unit` to a safe range (e.g., `≤ 2^128 − 1`) so that the product `max_amount × max_price_per_unit` cannot exceed P, and add an explicit post-computation invariant check:

```cairo
// After computing max_fee, assert it is non-zero when any resource bound is non-zero.
// Alternatively, bound max_price_per_unit to 2^128-1 in pack_resource_bounds.
assert_nn_le(resource_bounds.max_price_per_unit, 2 ** 128 - 1);
```

Additionally, add an invariant assertion in `charge_fee`:

```cairo
// Invariant: if any resource bound is non-zero, max_fee must be non-zero.
// (Detects field-overflow producing a spurious zero.)
assert_nn(max_fee - actual_fee);  // max_fee >= actual_fee must hold
```

---

### Proof of Concept

Choose the following resource bounds for a v3 transaction:

| Field | Value |
|---|---|
| `l1_gas.max_amount` | `2` |
| `l1_gas.max_price_per_unit` | `(P − 1) / 2` |
| `l2_gas.max_amount` | `1` |
| `l2_gas.max_price_per_unit` | `1` |
| `tip` | `0` |
| `l1_data_gas.max_amount` | `0` |

All constraints pass:
- `max_amount ≤ 2^64 − 1` ✓
- `max_price_per_unit ≥ 0` and `≤ (P−1)/2` ✓ (`assert_nn` passes)

Arithmetic in `compute_max_possible_fee`:

```
term1 = 2 × (P−1)/2 = P − 1 ≡ −1  (mod P)
term2 = 1 × (1 + 0)  = 1
term3 = 0 × anything = 0
sum   = −1 + 1 + 0   = 0  (mod P)
```

`compute_max_possible_fee` returns `0`. `charge_fee` hits the `if (max_fee == 0) { return (); }` branch and exits without charging any fee, despite the transaction consuming real resources. [4](#0-3) [5](#0-4) [2](#0-1)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L103-108)
```text
func pack_resource_bounds{range_check_ptr}(resource_bounds: ResourceBounds) -> felt {
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
    assert_nn(resource_bounds.max_price_per_unit);
    return (resource_bounds.resource * 2 ** 64 + resource_bounds.max_amount) * 2 ** 128 +
        resource_bounds.max_price_per_unit;
}
```
