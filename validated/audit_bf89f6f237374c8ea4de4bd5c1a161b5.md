### Title
Unchecked Felt Arithmetic in `compute_max_possible_fee` Enables Complete Fee Bypass — (File: `execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` performs unchecked multiplication and addition of `max_amount`, `max_price_per_unit`, and `tip` values in the Cairo field. Because `max_price_per_unit` is only validated to be non-negative (i.e., ≤ `(P-1)/2`) but has no upper bound, an unprivileged transaction sender can craft resource-bound values that cause the sum to wrap to **zero modulo the field prime**. When `max_fee == 0`, `charge_fee` returns immediately without executing any ERC-20 transfer, allowing the transaction to execute with no fee paid.

---

### Finding Description

`compute_max_possible_fee` in `transaction_impls.cairo` computes:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
    (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
    l1_data_gas_bounds.max_price_per_unit;
``` [1](#0-0) 

All arithmetic is modulo the Cairo field prime `P = 2^251 + 17·2^192 + 1`. The only validation applied to `max_price_per_unit` before this computation is `assert_nn` in `pack_resource_bounds`:

```cairo
assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
assert_nn(resource_bounds.max_price_per_unit);
``` [2](#0-1) 

`assert_nn` only enforces `max_price_per_unit ∈ [0, (P-1)/2]`. No upper bound like `2^128` is enforced. This means `max_price_per_unit` can be as large as `(P-1)/2 ≈ 2^250`.

The result of `compute_max_possible_fee` is then used in `charge_fee`:

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);
if (max_fee == 0) {
    return ();
}
...
assert_nn_le(calldata.amount.low, max_fee);
``` [3](#0-2) 

If `max_fee` wraps to zero, the function returns immediately — no ERC-20 transfer is executed, and the user pays nothing.

---

### Impact Explanation

**Critical — Direct loss of funds.**

An attacker can execute arbitrary transactions (invoke, deploy-account, declare) without paying any fee. The fee token (ETH/STRK) that should be transferred to the sequencer is never transferred. The user's account balance is never debited. This is a direct, protocol-level loss of funds for the fee recipient on every such transaction.

---

### Likelihood Explanation

Any unprivileged V3 transaction sender can trigger this. The attacker only needs to set specific `max_price_per_unit` values in their transaction's resource bounds — values that are accepted by all existing validation checks. No privileged access, leaked keys, or external dependencies are required. The attack is deterministic and repeatable.

---

### Recommendation

Add an explicit upper-bound range check on `max_price_per_unit` in `pack_resource_bounds` (e.g., `assert_nn_le(resource_bounds.max_price_per_unit, 2 ** 128 - 1)`). This ensures that `max_amount * max_price_per_unit ≤ (2^64 - 1) * (2^128 - 1) < 2^192`, which cannot wrap modulo `P ≈ 2^251`. Similarly, validate that the full sum in `compute_max_possible_fee` cannot overflow by bounding all inputs before the arithmetic.

---

### Proof of Concept

**Setup:** Craft a V3 transaction with:
- `tip = 1`
- `l1_gas_bounds`: `max_amount = 1`, `max_price_per_unit = (P-1)/2`
- `l2_gas_bounds`: `max_amount = 1`, `max_price_per_unit = (P-1)/2`
- `l1_data_gas_bounds`: `max_amount = 0`, `max_price_per_unit = 0`

**Validation bypass:**
- `assert_nn_le(max_amount, 2^64 - 1)` → `1 ≤ 2^64 - 1` ✓
- `assert_nn(max_price_per_unit)` → `(P-1)/2 ≥ 0` ✓ (exactly at the boundary)
- `assert_nn_le(tip, 2^64 - 1)` → `1 ≤ 2^64 - 1` ✓

**Arithmetic in `compute_max_possible_fee`:**

```
1 * (P-1)/2  +  1 * ((P-1)/2 + 1)  +  0
= (P-1)/2 + (P-1)/2 + 1
= P - 1 + 1
= P
≡ 0  (mod P)
```

**Result:** `max_fee = 0`. `charge_fee` hits the `if (max_fee == 0) { return (); }` branch and exits without charging any fee. The transaction executes for free. [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L103-108)
```text
func pack_resource_bounds{range_check_ptr}(resource_bounds: ResourceBounds) -> felt {
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
    assert_nn(resource_bounds.max_price_per_unit);
    return (resource_bounds.resource * 2 ** 64 + resource_bounds.max_amount) * 2 ** 128 +
        resource_bounds.max_price_per_unit;
}
```
