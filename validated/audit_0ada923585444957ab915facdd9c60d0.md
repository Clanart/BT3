### Title
Unchecked Upper Bound on `max_price_per_unit` Enables Field Arithmetic Overflow in `compute_max_possible_fee`, Allowing Complete Fee Bypass — (File: `execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` in `transaction_impls.cairo` computes the maximum chargeable fee as a sum of products of `max_amount * max_price_per_unit` for each resource type, entirely in Cairo field arithmetic (mod P ≈ 2²⁵¹). Because `pack_resource_bounds` in `transaction_hash.cairo` enforces only a lower bound (`assert_nn`) on `max_price_per_unit` — with no upper bound — an unprivileged transaction sender can craft resource bounds where the field-arithmetic products wrap around modulo the prime, causing `compute_max_possible_fee` to return `0`. When this happens, `charge_fee` exits immediately without transferring any fee, allowing the transaction to execute for free.

---

### Finding Description

**Step 1 — Missing upper bound in `pack_resource_bounds`:** [1](#0-0) 

`assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1)` bounds `max_amount` to 64 bits. However, `assert_nn(resource_bounds.max_price_per_unit)` only checks non-negativity — **no upper bound is enforced**. `max_price_per_unit` can therefore be any felt in `[0, P)`.

**Step 2 — Unchecked multiplication in `compute_max_possible_fee`:** [2](#0-1) 

All arithmetic here is Cairo field arithmetic (mod P). With `max_price_per_unit` near the field prime, the product `max_amount * max_price_per_unit` wraps around modulo P. The sum of three such wrapped terms can equal exactly `0 mod P`.

**Step 3 — Fee bypass when `max_fee == 0`:** [3](#0-2) 

If `compute_max_possible_fee` returns `0`, `charge_fee` returns immediately. No ERC-20 transfer is executed; the sequencer receives nothing.

**Step 4 — Discrepancy between off-chain and on-chain fee computation:**

The sequencer's off-chain code (written in Rust/Python) uses standard integer arithmetic. For a transaction with `max_price_per_unit` near P, the off-chain code computes a large, seemingly valid `max_fee` and accepts the transaction. The OS, however, computes `max_fee = 0` in field arithmetic and skips the fee. The resulting proof is valid.

---

### Impact Explanation

An unprivileged user can craft a v3 transaction whose resource bounds cause `compute_max_possible_fee` to return `0`. The OS then executes the transaction without charging any fee. At scale, this enables:

- **Direct loss of funds**: The sequencer loses all fee revenue for affected transactions. The ERC-20 fee token transfer that should compensate the sequencer is never executed.
- **Network not being able to confirm new transactions**: Free execution enables unbounded spam. A single attacker can flood the sequencer with zero-cost transactions, exhausting block capacity and preventing legitimate transactions from being confirmed — a total network halt.

---

### Likelihood Explanation

The attack requires only arithmetic knowledge of the Cairo field prime (publicly known: `P = 2²⁵¹ + 17·2¹⁹² + 1`). Constructing the required resource bounds is trivial:

- Set `L1_GAS.max_amount = 1`, `L1_GAS.max_price_per_unit = P - X`
- Set `L2_GAS.max_amount = 1`, `L2_GAS.max_price_per_unit = X` (with `tip = 0`)
- Set `L1_DATA_GAS.max_amount = 0`

Result: `(P - X) + X + 0 = P ≡ 0 mod P`.

The transaction passes all hash-time validations (`assert_nn_le` on `max_amount`, `assert_nn` on `max_price_per_unit`), is accepted by the sequencer's off-chain code (which sees a large integer max_fee), and is processed by the OS with `max_fee = 0`.

---

### Recommendation

Add an explicit upper bound on `max_price_per_unit` in `pack_resource_bounds`:

```cairo
assert_nn_le(resource_bounds.max_price_per_unit, 2 ** 128 - 1);
```

This ensures that `max_amount * max_price_per_unit ≤ (2⁶⁴ - 1) * (2¹²⁸ - 1) < 2¹⁹²`, which is well below the field prime and cannot wrap around. The same bound should be applied to `tip` in `compute_max_possible_fee`.

---

### Proof of Concept

Let `P = 0x800000000000011000000000000000000000000000000000000000000000001` (Cairo field prime).

Choose `X = 1`:

| Resource | `max_amount` | `max_price_per_unit` |
|---|---|---|
| L1_GAS | 1 | P − 1 |
| L2_GAS | 1 | 1 |
| L1_DATA_GAS | 0 | 0 |

`compute_max_possible_fee` computes:
```
1 * (P - 1) + 1 * (1 + 0) + 0 * 0
= (P - 1) + 1
= P
≡ 0 (mod P)
```

`charge_fee` hits `if (max_fee == 0) { return (); }` and exits. The transaction is executed with zero fee paid. The sequencer provides `LoadActualFee = 0`; `assert_nn_le(0, 0)` passes; the proof is valid and accepted on-chain. [1](#0-0) [4](#0-3) [5](#0-4)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L121-135)
```text
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
