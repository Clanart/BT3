### Title
Fee Evasion via Felt Arithmetic Overflow in `compute_max_possible_fee` — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` computes the maximum chargeable fee using unchecked felt arithmetic. An unprivileged transaction sender can craft resource bounds whose products sum to exactly the field prime `P`, causing the result to be `0 (mod P)`. The `charge_fee` function then short-circuits and charges nothing, while the user retains a non-zero L2 gas budget for execution — executing transactions for free.

---

### Finding Description

`compute_max_possible_fee` (lines 87–101) computes:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
    (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
    l1_data_gas_bounds.max_price_per_unit;
``` [1](#0-0) 

This is pure felt arithmetic — no range checks are applied to the individual `max_amount` or `max_price_per_unit` fields before or after this computation. The OS never constrains these fields to fit within `[0, 2^128)` or any sub-field range.

In `charge_fee`, the very first guard is:

```cairo
if (max_fee == 0) {
    return ();
}
``` [2](#0-1) 

If `max_fee` evaluates to `0` (whether legitimately or via overflow), the function returns immediately — no ERC-20 transfer is executed, no fee is charged.

The initial gas budget for execution is determined independently:

```cairo
func get_initial_user_gas_bound(common_tx_fields: CommonTxFields*) -> felt {
    assert common_tx_fields.n_resource_bounds = 3;
    return common_tx_fields.resource_bounds[L2_GAS_INDEX].max_amount;
}
``` [3](#0-2) 

`l2_gas.max_amount` is returned directly — it is **not** required to be zero when `max_fee` is zero. These two quantities are computed independently, so a non-zero `l2_gas.max_amount` can coexist with a `max_fee` of `0`.

---

### Impact Explanation

An attacker executes arbitrary transactions on StarkNet without paying any protocol fee. Every such transaction represents a direct, unrecoverable loss of fee revenue that should have been transferred to the sequencer/stakers. At scale, this drains the economic incentive layer of the protocol and constitutes **direct loss of funds** (fee assets that should flow to the sequencer are never transferred).

---

### Likelihood Explanation

The attack is fully deterministic and requires no privileged access, no leaked keys, and no external dependency. Any user who can submit a signed transaction can exploit this. The crafted resource bounds are valid felt values; the OS performs no range validation on them. The attack is repeatable on every block.

---

### Recommendation

Add explicit range checks on each `max_amount` and `max_price_per_unit` field in `ResourceBounds` before computing `compute_max_possible_fee`. Constrain each value to `[0, 2^64)` or `[0, 2^128)` using `assert_nn_le`, ensuring that no product or sum can overflow the field prime. Alternatively, compute `max_fee` as a `Uint256` using safe 128-bit multiplication and addition, and compare against a `Uint256` representation of the actual fee.

---

### Proof of Concept

Let `P` be the StarkNet field prime (`2^251 + 17·2^192 + 1`). Set the following resource bounds in a V3 invoke transaction:

| Field | Value |
|---|---|
| `l1_gas_bounds.max_amount` | `1` |
| `l1_gas_bounds.max_price_per_unit` | `P − 1` |
| `l2_gas_bounds.max_amount` | `1` |
| `l2_gas_bounds.max_price_per_unit` | `1` |
| `tip` | `0` |
| `l1_data_gas_bounds.max_amount` | `0` |

**Computation inside `compute_max_possible_fee`:**

```
max_fee = 1·(P−1) + 1·(1+0) + 0
        = (P−1) + 1
        = P
        ≡ 0  (mod P)
```

**Result in `charge_fee`:**

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);  // returns 0
if (max_fee == 0) {
    return ();  // ← taken; no fee charged
}
```

**Gas budget:**

```cairo
let initial_user_gas_bound = get_initial_user_gas_bound(...);
// returns resource_bounds[L2_GAS_INDEX].max_amount = 1
```

The transaction executes with gas budget `1`, and `charge_fee` returns without transferring any tokens. The sequencer receives `0` fee. The attack is repeatable for any desired execution by scaling `l2_gas_bounds.max_amount` while keeping the overflow condition satisfied (e.g., `l2_gas_bounds.max_amount = N`, `l2_gas_bounds.max_price_per_unit = 1`, `l1_gas_bounds.max_amount = 1`, `l1_gas_bounds.max_price_per_unit = P − N`). [4](#0-3) [5](#0-4)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L75-78)
```text
func get_initial_user_gas_bound(common_tx_fields: CommonTxFields*) -> felt {
    assert common_tx_fields.n_resource_bounds = 3;
    return common_tx_fields.resource_bounds[L2_GAS_INDEX].max_amount;
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L87-101)
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
