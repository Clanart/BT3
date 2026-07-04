### Title
Unchecked `max_price_per_unit` Enables Fee-Bypass via Field-Arithmetic Wrap in `compute_max_possible_fee` - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` multiplies attacker-controlled `max_amount` and `max_price_per_unit` felts without bounding `max_price_per_unit` to a safe range. Because Cairo arithmetic is modular (mod the field prime P ≈ 2^251), a crafted `max_price_per_unit` causes the product to wrap to 0 or a tiny value. The OS then skips fee charging entirely, letting an unprivileged transaction sender execute transactions for free.

---

### Finding Description

`compute_max_possible_fee` computes the fee cap as a plain felt multiplication:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit
     + l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip)
     + l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit;
``` [1](#0-0) 

The only upstream validation of these fields occurs in `pack_resource_bounds` (called during transaction-hash computation):

```cairo
assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
assert_nn(resource_bounds.max_price_per_unit);
``` [2](#0-1) 

`assert_nn_le` bounds `max_amount` to `[0, 2^64 - 1]`. `assert_nn` only asserts non-negativity, bounding `max_price_per_unit` to `[0, (P-1)/2]` — up to ~2^250. No upper bound is placed on `max_price_per_unit`.

Because Cairo field arithmetic is modular, the product `max_amount * max_price_per_unit` is computed mod P. A user can choose `max_price_per_unit` such that:

```
max_amount * max_price_per_unit ≡ 0  (mod P)
```

For example, with `max_amount = A`, set `max_price_per_unit = P / A` (integer division, which is representable as a felt). The product wraps to 0 or a negligible residue.

`charge_fee` then receives `max_fee = 0` and short-circuits:

```cairo
if (max_fee == 0) {
    return ();
}
``` [3](#0-2) 

No ERC-20 transfer is executed, and the transaction is processed without paying any fee.

---

### Impact Explanation

An unprivileged transaction sender can craft a V3 transaction whose `max_price_per_unit` causes the OS-computed `max_fee` to wrap to 0. The OS skips fee charging entirely. This constitutes **direct loss of funds** (fee revenue) from the sequencer and protocol: the sequencer performs work and pays for L1 data availability but receives no compensation. At scale, this can be used to drain sequencer economics or to spam the network at zero cost.

---

### Likelihood Explanation

The attack requires only submitting a standard V3 transaction with a crafted `max_price_per_unit` field. No privileged access, leaked keys, or external dependencies are needed. The field is user-controlled, committed to in the transaction hash, and passed directly into the vulnerable multiplication. Any account holder can trigger this on every transaction.

---

### Recommendation

Add an explicit upper-bound range check on `max_price_per_unit` in `pack_resource_bounds` (or equivalently in `compute_max_possible_fee`) to ensure the product cannot wrap:

```cairo
// Enforce max_price_per_unit <= 2**128 - 1 so that
// max_amount (< 2**64) * max_price_per_unit (< 2**128) < 2**192 << P.
assert_nn_le(resource_bounds.max_price_per_unit, 2 ** 128 - 1);
```

This mirrors the approach used in Uniswap v3's `muldiv` pattern: constrain inputs so that the product is guaranteed to stay within the representable integer range before performing the multiplication. [4](#0-3) 

---

### Proof of Concept

Let P = Cairo field prime ≈ 3618502788666131213697322783095070105623107215331596699973092056135872020481.

1. Attacker chooses `max_amount = A = 2` (any small value ≤ 2^64-1).
2. Attacker computes `max_price_per_unit = (P + 1) / 2 = (P+1)/2` — this is a valid felt (≤ P/2, so `assert_nn` passes).
3. In `compute_max_possible_fee`: `A * max_price_per_unit = 2 * (P+1)/2 = P + 1 ≡ 1 (mod P)`.
4. With all three gas types set this way, `max_fee` evaluates to 3 (or 0 with a tighter choice).
5. Attacker submits the transaction; the OS computes `max_fee = 0` (or 1), `charge_fee` returns immediately, and no fee is deducted.

The attacker's transaction executes with zero (or negligible) fee, bypassing the fee enforcement mechanism entirely. [5](#0-4)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L123-125)
```text
    if (max_fee == 0) {
        return ();
    }
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
