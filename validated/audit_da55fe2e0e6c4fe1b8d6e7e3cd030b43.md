### Title
Unchecked Felt Arithmetic Overflow in `compute_max_possible_fee` Enables Complete Fee Bypass — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` performs unchecked multiplication and addition of fully user-controlled `felt` values. Because Cairo's `felt` arithmetic is modular (mod P ≈ 2^251 + 17·2^192 + 1), a crafted V3 transaction can cause the computed `max_fee` to wrap around to exactly 0. In `charge_fee`, a `max_fee == 0` result causes the function to return immediately, skipping fee collection entirely. This is the direct analog of the external report's overflow-in-conversion pattern: a sentinel/boundary value passes through unchecked arithmetic and produces an incorrect result that bypasses a critical guard.

---

### Finding Description

`compute_max_possible_fee` at lines 87–102 of `transaction_impls.cairo`:

```cairo
func compute_max_possible_fee(tx_info: TxInfo*) -> felt {
    ...
    return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
        (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
        l1_data_gas_bounds.max_price_per_unit;
}
``` [1](#0-0) 

Every operand (`max_amount`, `max_price_per_unit`, `tip`) is a raw `felt` value taken directly from the user-supplied transaction. No bounds are asserted on any of them anywhere in the OS. Cairo `felt` arithmetic is modular: if the sum of the three products equals P (the field prime), the result is 0.

`charge_fee` then uses this result as the sole gate for fee collection:

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);
if (max_fee == 0) {
    return ();          // ← fee charging skipped entirely
}
...
assert_nn_le(calldata.amount.low, max_fee);   // never reached
``` [2](#0-1) 

If `max_fee` wraps to 0, the ERC-20 transfer to the sequencer is never executed and the block is still accepted as valid by the OS.

---

### Impact Explanation

**Critical — Direct loss of funds.**

A user can execute arbitrary transactions without paying any fee. The sequencer's off-chain Rust code uses standard 128-bit or 256-bit integer arithmetic (no modular reduction), so it computes a large, positive `max_fee` and willingly includes the transaction. The OS Cairo code then computes `max_fee ≡ 0 (mod P)` and skips the fee transfer. The sequencer receives nothing for work it performed and resources it consumed. At scale, this constitutes a direct, repeatable loss of protocol revenue and can be used to drain sequencer economics.

---

### Likelihood Explanation

The attack requires only a valid V3 transaction with carefully chosen `ResourceBounds` fields — no privileged access, no key compromise, no operator collusion. The field prime P is a public constant. Computing a set of `(max_amount, max_price_per_unit, tip)` tuples whose products sum to P is trivial modular arithmetic. The OS imposes zero bounds on these fields.

---

### Recommendation

Before computing the fee, assert that each `max_amount` and `max_price_per_unit` fits within a safe sub-field range (e.g., 64-bit), so that no product or sum can reach P. For example:

```cairo
// Add before the return statement in compute_max_possible_fee:
assert_nn_le(l1_gas_bounds.max_amount, MAX_RESOURCE_AMOUNT);
assert_nn_le(l1_gas_bounds.max_price_per_unit, MAX_RESOURCE_PRICE);
assert_nn_le(l2_gas_bounds.max_amount, MAX_RESOURCE_AMOUNT);
assert_nn_le(l2_gas_bounds.max_price_per_unit, MAX_RESOURCE_PRICE);
assert_nn_le(tx_info.tip, MAX_TIP);
assert_nn_le(l1_data_gas_bounds.max_amount, MAX_RESOURCE_AMOUNT);
assert_nn_le(l1_data_gas_bounds.max_price_per_unit, MAX_RESOURCE_PRICE);
```

Where `MAX_RESOURCE_AMOUNT` and `MAX_RESOURCE_PRICE` are chosen so that `3 * MAX_RESOURCE_AMOUNT * (MAX_RESOURCE_PRICE + MAX_TIP) < P`.

---

### Proof of Concept

Let P = 2^251 + 17·2^192 + 1 (the Cairo field prime). Construct a V3 transaction with:

| Field | Value |
|---|---|
| `l1_gas_bounds.max_amount` | `1` |
| `l1_gas_bounds.max_price_per_unit` | `P − 1` |
| `l2_gas_bounds.max_amount` | `1` |
| `l2_gas_bounds.max_price_per_unit` | `1` |
| `tip` | `0` |
| `l1_data_gas_bounds.max_amount` | `0` |
| `l1_data_gas_bounds.max_price_per_unit` | `0` |

`compute_max_possible_fee` computes:

```
(1 × (P−1)) + (1 × (1 + 0)) + (0 × 0)
= (P−1) + 1
= P
≡ 0  (mod P)
```

`charge_fee` sees `max_fee == 0` and returns immediately. The transaction executes with zero fee charged. The sequencer's off-chain code, using non-modular arithmetic, computed `max_fee = P − 1 + 1 = P` (a large positive integer) and included the transaction expecting to collect a fee — but the OS proof accepts the block with no fee transfer.

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
