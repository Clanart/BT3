### Title
Unchecked Field Arithmetic Overflow in `compute_max_possible_fee` Allows Fee Bypass — (`File: execution/transaction_impls.cairo`)

### Summary

`compute_max_possible_fee` in `transaction_impls.cairo` performs raw field-element multiplication and addition with no bounds checks on the `ResourceBounds` inputs. Because Cairo arithmetic is modulo the Stark field prime P ≈ 2²⁵¹, a user-controlled combination of `max_amount`, `max_price_per_unit`, and `tip` values can be chosen so the sum wraps to exactly 0 (mod P). When `compute_max_possible_fee` returns 0, `charge_fee` exits immediately without transferring any fee tokens, allowing the transaction to execute for free.

---

### Finding Description

`compute_max_possible_fee` is defined as: [1](#0-0) 

```cairo
func compute_max_possible_fee(tx_info: TxInfo*) -> felt {
    ...
    return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit
         + l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip)
         + l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit;
}
```

All six operand fields (`max_amount`, `max_price_per_unit` for each of the three resource types, plus `tip`) are user-supplied field elements loaded from the transaction via hints. None of them are range-checked before this arithmetic. In Cairo, every `felt` lives in `[0, P-1]`; multiplication and addition silently reduce mod P. There is no `assert_nn_le` or `assert_le_felt` guarding these inputs before the return value is used.

`charge_fee` then does: [2](#0-1) 

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);

if (max_fee == 0) {
    return ();          // ← fee transfer is skipped entirely
}
...
assert_nn_le(calldata.amount.low, max_fee);
```

If `max_fee` wraps to 0, the early-return fires and no ERC-20 transfer is executed. The `assert_nn_le` guard is never reached.

---

### Impact Explanation

An attacker who can submit a signed transaction (any unprivileged user) can craft `ResourceBounds` values whose products sum to 0 mod P. The OS proof will be valid — the OS code itself accepts the transaction — and the L1 verifier will accept the resulting STARK proof. The transaction executes its full `__execute__` body while paying zero fees. Because the OS is the authoritative enforcement layer (not the sequencer's mempool), this bypass is provably correct from the L1 perspective.

Concrete impacts:
- **Direct loss of funds (Critical):** The sequencer/protocol receives no fee tokens for real computation. Repeated exploitation drains fee revenue.
- **Network shutdown (High):** Free execution enables unbounded spam. An attacker can flood the network with zero-cost transactions, preventing legitimate transactions from being confirmed.

---

### Likelihood Explanation

The attack requires only:
1. Knowledge of the Stark field prime P (public).
2. Ability to submit a signed V3 transaction with chosen `ResourceBounds` (any user).
3. A sequencer that includes the transaction (a colluding or unaware sequencer, or one that only checks mempool-level fee > 0 without re-deriving the OS-level `max_fee`).

No privileged access, leaked keys, or third-party compromise is required. The arithmetic to find a wrapping combination is trivial (e.g., set `l1_gas_bounds.max_price_per_unit = P − 1`, `l1_gas_bounds.max_amount = 1`, `l1_data_gas_bounds.max_amount = 1`, `l1_data_gas_bounds.max_price_per_unit = 1`, all L2 amounts = 0 → sum = (P−1) + 1 = P ≡ 0).

---

### Recommendation

1. **Add range checks on all `ResourceBounds` fields before arithmetic.** Each `max_amount` and `max_price_per_unit` should be asserted to fit within a safe bound (e.g., `assert_nn_le(max_amount, MAX_RESOURCE_AMOUNT)` and `assert_nn_le(max_price_per_unit, MAX_PRICE_PER_UNIT)`) so that no product or sum of products can reach P.

2. **Add a range check on `tip`** for the same reason, since it participates in the L2 gas term.

3. **Assert `max_fee > 0` separately** after the computation, so that a wrapped-to-zero result is caught as an error rather than silently bypassing fee collection.

---

### Proof of Concept

**Attacker-controlled transaction fields (V3 Invoke):**

| Field | Value |
|---|---|
| `l1_gas_bounds.max_amount` | `1` |
| `l1_gas_bounds.max_price_per_unit` | `P − 1` |
| `l2_gas_bounds.max_amount` | `0` |
| `l2_gas_bounds.max_price_per_unit` | `0` |
| `tip` | `0` |
| `l1_data_gas_bounds.max_amount` | `1` |
| `l1_data_gas_bounds.max_price_per_unit` | `1` |

**Arithmetic (mod P):**

```
max_fee = 1*(P−1) + 0*(0+0) + 1*1
        = (P−1) + 0 + 1
        = P
        ≡ 0  (mod P)
```

**OS execution path:**

1. `compute_max_possible_fee` returns `0`.
2. `charge_fee` hits `if (max_fee == 0) { return (); }` at line 123 and exits.
3. No ERC-20 `transfer` is executed.
4. `__execute__` runs normally.
5. The STARK proof is generated and accepted by the L1 verifier — the OS code itself produced this outcome. [3](#0-2) [4](#0-3)

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
