### Title
Unchecked Field Arithmetic Overflow in `compute_max_possible_fee` Allows Fee-Free Transaction Execution — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` performs unchecked multiplication of user-controlled `max_amount` and `max_price_per_unit` fields in Cairo's finite field (modulo the field prime P ≈ 2²⁵¹). Because `max_price_per_unit` has no upper-bound constraint, a user can craft resource bounds whose products sum to exactly 0 mod P. `charge_fee` then sees `max_fee == 0` and returns immediately without charging any fee, allowing the transaction to execute for free.

---

### Finding Description

`compute_max_possible_fee` computes:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit
     + l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip)
     + l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit;
``` [1](#0-0) 

All arithmetic in Cairo is implicitly modulo the field prime P. The only bounds enforced on the resource bounds fields are:

- `max_amount ≤ 2⁶⁴ − 1` (enforced in `pack_resource_bounds` via `assert_nn_le`)
- `max_price_per_unit ≥ 0` (enforced via `assert_nn` only — **no upper bound**)
- `tip ≤ 2⁶⁴ − 1` (enforced in `hash_fee_fields`) [2](#0-1) 

Because `assert_nn` only checks that a felt is in `[0, (P−1)/2]`, `max_price_per_unit` can be as large as `(P−1)/2 ≈ 2²⁵⁰`. With `max_amount` up to `2⁶⁴ − 1`, each product term can reach `≈ 2³¹⁴`, overflowing P many times. The function implicitly assumes the sum does not wrap around — an assumption that is violated by attacker-controlled inputs.

`charge_fee` then uses the result as a gate:

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);
if (max_fee == 0) {
    return ();
}
``` [3](#0-2) 

If the overflowed sum equals 0 mod P, fee charging is skipped entirely. The transaction executes, state changes are committed, and the OS proof is valid — all without any fee transfer to the sequencer.

---

### Impact Explanation

**Direct loss of funds (Critical).** The sequencer receives zero fee for executing the transaction. Because the OS proof is valid (the Cairo constraints are satisfied), the L1 verifier accepts the state transition. The sequencer's fee revenue is permanently lost for every such transaction included in a block. At scale, a coordinated attacker can drain sequencer revenue across many blocks.

---

### Likelihood Explanation

The attacker is an unprivileged V3 transaction sender. No special role or key is required. The crafted values pass all existing Cairo constraint checks (`assert_nn_le` on `max_amount`, `assert_nn` on `max_price_per_unit`, `assert_nn_le` on `tip`). The concrete PoC below shows exact field values that satisfy every constraint and produce `max_fee = 0`. The only external dependency is that the sequencer includes the transaction — which is plausible if the sequencer's own mempool check uses the same overflowing computation and also computes `max_fee = 0`, treating it as a zero-fee transaction.

---

### Recommendation

Add explicit upper-bound range checks on `max_price_per_unit` before performing the multiplication in `compute_max_possible_fee`. The product `max_amount * max_price_per_unit` must be representable as a standard integer (not a field element) to be meaningful as a fee cap. Concretely, enforce `max_price_per_unit ≤ 2⁶⁴ − 1` (matching the bound already applied to `max_amount`), which keeps each product within `2¹²⁸ − 1` and the three-term sum within `3 * (2¹²⁸ − 1)`, well below P. Alternatively, perform the fee cap comparison using multi-limb (Uint256) arithmetic rather than raw felt arithmetic.

---

### Proof of Concept

Let P = `0x800000000000011000000000000000000000000000000000000000000000001` (the Cairo field prime).

Set the following V3 transaction resource bounds:

| Field | Value |
|---|---|
| `l1_gas_bounds.max_amount` | `1` |
| `l1_gas_bounds.max_price_per_unit` | `(P−1)/2` |
| `l2_gas_bounds.max_amount` | `1` |
| `l2_gas_bounds.max_price_per_unit` | `(P−1)/2` |
| `tip` | `0` |
| `l1_data_gas_bounds.max_amount` | `1` |
| `l1_data_gas_bounds.max_price_per_unit` | `1` |

**Constraint satisfaction:**
- All `max_amount` values are `1 ≤ 2⁶⁴ − 1` ✓
- `(P−1)/2` satisfies `assert_nn` (it is the largest non-negative felt) ✓
- `tip = 0 ≤ 2⁶⁴ − 1` ✓

**Arithmetic (mod P):**

```
term1 = 1 * (P−1)/2           = (P−1)/2
term2 = 1 * ((P−1)/2 + 0)     = (P−1)/2
term3 = 1 * 1                  = 1

sum = (P−1)/2 + (P−1)/2 + 1 = P − 1 + 1 = P ≡ 0 (mod P)
```

`compute_max_possible_fee` returns `0`. `charge_fee` hits the `if (max_fee == 0) { return (); }` branch and exits without executing the ERC-20 transfer. The transaction runs to completion with zero fee charged. [4](#0-3) [5](#0-4)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L117-135)
```text
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
