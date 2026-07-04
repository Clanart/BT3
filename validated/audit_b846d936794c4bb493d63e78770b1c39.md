### Title
Unchecked Field Arithmetic Overflow in `compute_max_possible_fee` Allows Fee Bypass — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` multiplies user-supplied `max_amount` and `max_price_per_unit` resource-bound fields using raw Cairo field arithmetic, with no overflow guard. Because Cairo arithmetic is modular over the Stark prime P (~2^251), a crafted transaction can make the sum of products wrap to exactly 0 mod P. `charge_fee` then skips the ERC-20 transfer entirely, letting the attacker execute transactions for free.

---

### Finding Description

`compute_max_possible_fee` computes the fee ceiling as:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit
     + l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip)
     + l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit;
``` [1](#0-0) 

The only upstream constraints on these fields come from `pack_resource_bounds` (called during transaction-hash computation):

```cairo
assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
assert_nn(resource_bounds.max_price_per_unit);
``` [2](#0-1) 

`assert_nn` constrains `max_price_per_unit` to `[0, (P-1)/2]` (≈ 2^250). `max_amount` is bounded to `[0, 2^64-1]`. Their product can therefore reach ~2^314, wrapping around P multiple times. The **sum** of three such products can be made congruent to 0 mod P.

`charge_fee` then short-circuits on a zero result:

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);
if (max_fee == 0) {
    return ();
}
``` [3](#0-2) 

No fee transfer is executed, and the sequencer receives nothing.

---

### Impact Explanation

**Critical — Direct loss of funds.**

The sequencer's fee revenue is the ERC-20 transfer executed inside `charge_fee`. If `max_fee` evaluates to 0, that transfer is skipped unconditionally. An attacker can execute invoke, declare, or deploy-account transactions with an arbitrarily large L2 gas budget while paying zero fees. At scale this drains sequencer revenue and can lead to a network halt (sequencers stop processing unprofitable blocks).

---

### Likelihood Explanation

Any unprivileged transaction sender can craft a valid V3 transaction with the overflow values. The transaction hash commits to the resource bounds (via `hash_fee_fields`), so the proof is still valid — the OS simply computes the wrong fee ceiling. No special role or key is required. The attacker only needs to solve the linear congruence shown in the PoC, which has an explicit closed-form solution.

---

### Recommendation

Before performing the multiplication in `compute_max_possible_fee`, enforce an explicit upper bound on `max_price_per_unit` (e.g., `assert_nn_le(max_price_per_unit, 2**128 - 1)`) so that no product of `max_amount * max_price_per_unit` can exceed the field prime. Alternatively, verify that the computed sum is strictly greater than zero and within a safe range before using it as a fee ceiling.

---

### Proof of Concept

Let P be the Stark field prime. Choose:

| Field | Value |
|---|---|
| L2 gas `max_amount` | N (e.g., 10^6 — provides execution gas) |
| L2 gas `max_price_per_unit` | 1 |
| L1 gas `max_amount` | 2 |
| L1 gas `max_price_per_unit` | (P − N) / 2 |
| L1 data gas `max_amount` | 0 |
| tip | 0 |

**Constraint check:**
- `max_amount = 2 ≤ 2^64 − 1` ✓
- `(P − N)/2 ≤ (P − 1)/2` for any N ≥ 1 ✓ (passes `assert_nn`)

**Fee computation:**
```
max_fee = 2 * (P−N)/2  +  N * 1  +  0
        = (P − N)      +  N
        = P
        ≡ 0  (mod P)
```

`compute_max_possible_fee` returns 0. `charge_fee` returns immediately without transferring any tokens to the sequencer. The transaction executes with N units of L2 gas at zero cost. [4](#0-3) [5](#0-4) [6](#0-5)

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
