### Title
Unbounded `max_price_per_unit` Enables Felt-Overflow Fee Bypass in `compute_max_possible_fee` - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`pack_resource_bounds` enforces only a non-negativity check (`assert_nn`) on `max_price_per_unit`, leaving it unbounded up to `⌊P/2⌋ ≈ 2^250`. `compute_max_possible_fee` then multiplies this value by `max_amount` (bounded to `2^64 - 1`) using felt arithmetic (mod P). The product overflows the field prime, allowing an unprivileged transaction sender to craft resource bounds whose felt-arithmetic sum equals 0 mod P, collapsing `max_fee` to 0 and forcing the OS to accept a zero-fee transaction.

---

### Finding Description

`pack_resource_bounds` in `transaction_hash.cairo` validates resource bounds during transaction hash computation:

```cairo
func pack_resource_bounds{range_check_ptr}(resource_bounds: ResourceBounds) -> felt {
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);   // max_amount ∈ [0, 2^64-1]
    assert_nn(resource_bounds.max_price_per_unit);            // ONLY checks ≥ 0, i.e. ∈ [0, ⌊P/2⌋]
    ...
}
``` [1](#0-0) 

`max_amount` is tightly bounded to `[0, 2^64-1]`, but `max_price_per_unit` is only required to be non-negative — it can be any value up to `⌊P/2⌋ ≈ 2^250`.

`compute_max_possible_fee` then computes the fee ceiling using plain felt arithmetic:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit
     + l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip)
     + l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit;
``` [2](#0-1) 

Each term `max_amount * max_price_per_unit` can reach `(2^64-1) × (P/2) ≈ 2^314`, which is `≈ 2^62` multiples of P. The sum of three such terms can be made to equal exactly 0 mod P by choosing `max_price_per_unit` values accordingly (since P is prime, the attacker has full control over the residue of each term).

`charge_fee` then enforces:

```cairo
assert_nn_le(calldata.amount.low, max_fee);
``` [3](#0-2) 

If `max_fee = 0`, this assertion forces `actual_fee = 0`, and the ERC-20 transfer executes for zero tokens — the user pays nothing.

This is the direct analog of the external report: instead of an `int256` parameter where negative values trigger "withdraw all," here a `felt` parameter (`max_price_per_unit`) is insufficiently bounded, allowing modular overflow to collapse the fee ceiling to zero — the same class of accounting mistake caused by an overly permissive type/range.

---

### Impact Explanation

An unprivileged transaction sender can craft a V3 transaction whose resource bounds cause `compute_max_possible_fee` to return 0 mod P. The OS then enforces `actual_fee = 0`, executing the ERC-20 fee transfer for zero tokens. The user's account is not debited. This constitutes **direct loss of funds** — the protocol and sequencer receive no fee compensation for executing the transaction, and the fee token balance that should have been transferred to the sequencer is never moved.

---

### Likelihood Explanation

Any unprivileged user can construct such a transaction: the resource bounds are user-supplied fields committed to by the transaction hash. The attacker only needs to solve `A·B + C·(D+E) + F·G ≡ 0 (mod P)` for values within the enforced ranges — a straightforward linear equation over a prime field with many solutions. No special privilege, key, or operator cooperation is required. The only friction is whether the sequencer's off-chain mempool validation uses 256-bit arithmetic (missing the overflow) or felt arithmetic (detecting it); a discrepancy between the two is a realistic implementation gap.

---

### Recommendation

Replace the loose `assert_nn` on `max_price_per_unit` with a tight upper-bound check matching the protocol's intended precision (e.g., `2^128 - 1`):

```cairo
// In pack_resource_bounds:
assert_nn_le(resource_bounds.max_price_per_unit, 2 ** 128 - 1);
```

With `max_amount ≤ 2^64 - 1` and `max_price_per_unit ≤ 2^128 - 1`, each product fits within `2^192`, and the sum of three terms fits within `2^194`, well below P ≈ 2^252 — eliminating overflow entirely. This mirrors the external report's recommendation to use `uint256` instead of `int256`: constrain the type to the domain where only valid values are representable.

---

### Proof of Concept

Let P = StarkNet field prime = `2^251 + 17·2^192 + 1`.

Choose:
- `l1_gas_bounds.max_amount = 1`, `l1_gas_bounds.max_price_per_unit = P - X` (for some X in `[1, ⌊P/2⌋]`)
- `l2_gas_bounds.max_amount = 1`, `l2_gas_bounds.max_price_per_unit = X`, `tip = 0`
- `l1_data_gas_bounds.max_amount = 0`

Then:
```
max_fee = 1·(P - X) + 1·(X + 0) + 0 = P - X + X = P ≡ 0 (mod P)
```

`assert_nn(P - X)` passes as long as `P - X ≤ ⌊P/2⌋`, i.e., `X ≥ ⌈P/2⌉`. Choose `X = ⌈P/2⌉`. Both `max_price_per_unit` values pass `assert_nn`. The transaction hash is valid. The OS computes `max_fee = 0`. `assert_nn_le(actual_fee, 0)` forces `actual_fee = 0`. The fee ERC-20 transfer executes for zero tokens. The transaction is processed at zero cost.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L134-135)
```text
    // Verify that the charged amount is not larger than the transaction's max_fee field.
    assert_nn_le(calldata.amount.low, max_fee);
```
