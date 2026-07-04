### Title
Fee Computation Field Overflow Allows Fee-Free Transaction Execution — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` performs unchecked felt-field arithmetic on user-controlled resource bound values. A malicious transaction sender can craft `max_price_per_unit` values that cause the sum to wrap around to zero modulo the StarkNet prime, making `charge_fee` skip fee collection entirely and allowing the transaction to execute for free.

---

### Finding Description

`compute_max_possible_fee` computes the fee ceiling as:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit
     + l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip)
     + l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit;
``` [1](#0-0) 

The only bounds enforced on the inputs come from `pack_resource_bounds` (called during transaction hash computation):

- `max_amount ≤ 2^64 − 1`
- `max_price_per_unit ≥ 0` (i.e., `assert_nn` enforces `max_price_per_unit < P/2`)
- `tip ≤ 2^64 − 1` [2](#0-1) 

There is **no upper bound** on `max_price_per_unit` beyond the felt range (`< P/2 ≈ 2^250`). Because Cairo arithmetic is modular over the StarkNet prime `P ≈ 2^251`, the product `max_amount × max_price_per_unit` can silently wrap around. With three independent terms, the attacker has enough degrees of freedom to make the total sum ≡ 0 (mod P).

`charge_fee` then checks:

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);
if (max_fee == 0) {
    return ();
}
``` [3](#0-2) 

When `max_fee` wraps to 0, the function returns immediately — the ERC-20 transfer to the sequencer is never executed, and the user pays nothing.

---

### Impact Explanation

**Direct loss of funds / Network shutdown.**

- The sequencer receives zero fee for processing the transaction. Because the OS proof is what L1 verifies, an honest sequencer that uses the same Cairo OS code will produce a valid proof showing `max_fee = 0` and no fee transfer — L1 accepts it.
- An attacker can submit an unbounded volume of computationally expensive transactions at zero cost, exhausting sequencer resources and causing a total network halt (no new transactions can be confirmed).

Both "Direct loss of funds" and "Network not being able to confirm new transactions" are within the allowed impact scope.

---

### Likelihood Explanation

Any unprivileged V3 transaction sender can trigger this. The attacker only needs to choose specific `max_price_per_unit` values for the three resource types such that the felt-arithmetic sum is 0 mod P. This requires no special access, no leaked keys, and no trusted role. The values pass all existing bound checks (`assert_nn`, `assert_nn_le`).

**Concrete example** (using StarkNet prime P):

| Field | Value |
|---|---|
| `l1_gas.max_amount` | 2 |
| `l1_gas.max_price_per_unit` | `(P − 1) / 2` |
| `l2_gas.max_amount` | 1 |
| `l2_gas.max_price_per_unit` | 1 |
| `tip` | 0 |
| `l1_data_gas.max_amount` | 0 |
| `l1_data_gas.max_price_per_unit` | 0 |

Computation:
- `l1_gas term = 2 × (P−1)/2 = P−1 ≡ −1 (mod P)`
- `l2_gas term = 1 × (1 + 0) = 1`
- `l1_data_gas term = 0`
- **Total = −1 + 1 + 0 = 0 (mod P)**

All values satisfy `assert_nn_le(max_amount, 2^64 − 1)` and `assert_nn(max_price_per_unit)` (since `(P−1)/2 < P/2`). [4](#0-3) 

---

### Recommendation

Add an explicit upper bound on `max_price_per_unit` in `pack_resource_bounds` (or in `compute_max_possible_fee`) to prevent field overflow. For example, enforce `max_price_per_unit ≤ 2^128 − 1` (matching the 128-bit packing already used in the hash encoding), which keeps each product well below P:

```cairo
assert_nn_le(resource_bounds.max_price_per_unit, 2 ** 128 - 1);
```

With `max_amount ≤ 2^64 − 1` and `max_price_per_unit ≤ 2^128 − 1`, the maximum product per term is `(2^64 − 1)(2^128 − 1) < 2^192 ≪ P`, and the sum of three such terms is at most `3 × 2^192 ≪ P`, making overflow impossible. [2](#0-1) 

---

### Proof of Concept

1. Attacker constructs a V3 invoke transaction with:
   - `l1_gas.max_amount = 2`, `l1_gas.max_price_per_unit = (P−1)/2`
   - `l2_gas.max_amount = 1`, `l2_gas.max_price_per_unit = 1`, `tip = 0`
   - `l1_data_gas.max_amount = 0`, `l1_data_gas.max_price_per_unit = 0`
2. All values pass `pack_resource_bounds` checks (amounts ≤ 2^64−1, prices pass `assert_nn`).
3. Transaction hash is computed and verified normally.
4. OS executes the transaction; `compute_max_possible_fee` returns `(P−1) + 1 + 0 ≡ 0 (mod P)`.
5. `charge_fee` hits `if (max_fee == 0) { return (); }` and exits — no fee transfer occurs.
6. The OS proof is valid; L1 accepts the block containing a fee-free transaction.
7. Attacker repeats with arbitrary calldata to spam the network at zero cost. [5](#0-4) [6](#0-5)

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
