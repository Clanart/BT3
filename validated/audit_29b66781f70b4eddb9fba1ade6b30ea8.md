### Title
Unbounded `max_price_per_unit` Enables Felt-Arithmetic Overflow in `compute_max_possible_fee`, Allowing Complete Fee Bypass - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`pack_resource_bounds` enforces only a lower bound (`assert_nn`) on `max_price_per_unit`, leaving no upper bound. `compute_max_possible_fee` then multiplies these uncapped felt values together using raw Cairo felt arithmetic (modulo the field prime P ≈ 2²⁵¹). An unprivileged transaction sender can craft specific `max_price_per_unit` values that cause the sum of products to wrap to exactly 0 mod P, making `charge_fee` skip fee collection entirely.

---

### Finding Description

In `pack_resource_bounds` (called during transaction hash computation), the validation is:

```cairo
assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
assert_nn(resource_bounds.max_price_per_unit);   // only ≥ 0, no upper bound
```

`assert_nn` only constrains `max_price_per_unit` to `[0, P/2]` (≈ `[0, 2²⁵⁰]`). No upper bound is enforced.

`compute_max_possible_fee` then computes:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
    (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
    l1_data_gas_bounds.max_price_per_unit;
```

This is raw felt arithmetic — all operations are implicitly modulo P. With `max_price_per_unit` up to P/2 and `max_amount` up to 2⁶⁴−1, individual products can reach ≈ 2³¹⁴, wrapping modulo P to an arbitrary value.

`charge_fee` then does:

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);
if (max_fee == 0) {
    return ();   // ← fee collection skipped entirely
}
```

If the attacker makes the sum ≡ 0 (mod P), the OS silently skips the ERC-20 transfer to the sequencer.

**Concrete exploit values** (amounts = 1, tip = 0):

The attacker needs `price_l1 + price_l2 + price_l1_data ≡ 0 (mod P)`, i.e., the sum equals exactly P. A valid solution with all values in `[0, P/2]`:

- `l1_gas_bounds.max_price_per_unit = (P−1)/2`
- `l2_gas_bounds.max_price_per_unit = 1`
- `l1_data_gas_bounds.max_price_per_unit = (P−1)/2`

Sum = `(P−1)/2 + 1 + (P−1)/2 = P ≡ 0 (mod P)`. All three values satisfy `assert_nn` (each ≤ P/2). All amounts = 1 satisfy `assert_nn_le(amount, 2⁶⁴−1)`.

---

### Impact Explanation

When `compute_max_possible_fee` returns 0, `charge_fee` returns immediately without executing the ERC-20 transfer. The transaction is fully executed — validate + execute entry points run, state changes are committed — but zero fee is collected. This is a **direct loss of funds**: the sequencer/protocol is owed a fee for execution resources consumed but receives nothing. The OS proof remains valid (no assertion fails), so the block is accepted.

---

### Likelihood Explanation

Any unprivileged transaction sender (invoke, declare, deploy_account) controls `max_price_per_unit` directly. The transaction hash commits to these values via `hash_fee_fields` → `pack_resource_bounds`, so the sequencer cannot alter them after signing. The exploit values are computable offline with simple arithmetic. No special access, leaked keys, or privileged role is required.

---

### Recommendation

Add an explicit upper bound on `max_price_per_unit` in `pack_resource_bounds`, for example:

```cairo
assert_nn_le(resource_bounds.max_price_per_unit, 2 ** 128 - 1);
```

This ensures that `max_amount * max_price_per_unit ≤ (2⁶⁴−1) * (2¹²⁸−1) < 2¹⁹²`, which is well below P and cannot wrap. Alternatively, `compute_max_possible_fee` should use checked arithmetic (e.g., via `Uint256` multiplication) to detect overflow before comparing against the actual fee.

---

### Proof of Concept

**Root cause — no upper bound on `max_price_per_unit`:** [1](#0-0) 

**Unchecked felt multiplication in fee computation:** [2](#0-1) 

**Fee bypass branch triggered when result wraps to 0:** [3](#0-2) 

**Tip is bounded to `[0, 2⁶⁴−1]` but `max_price_per_unit` is not:** [4](#0-3)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L116-117)
```text
    assert data_to_hash[0] = tip;
    assert_nn_le(tip, 2 ** 64 - 1);
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
