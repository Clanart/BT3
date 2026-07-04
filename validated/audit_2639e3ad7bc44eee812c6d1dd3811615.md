### Title
Fee Computation Wraps to Zero via Cairo Field Overflow, Enabling Free Transaction Execution — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` in `transaction_impls.cairo` sums products of `max_amount` and `max_price_per_unit` fields from a V3 transaction's resource bounds using raw Cairo felt arithmetic. Because the only upstream bound on `max_price_per_unit` is `assert_nn` (i.e., the value is in `[0, P/2)` where P ≈ 2^251), and `max_amount` is bounded to `[0, 2^64 - 1]`, individual products can reach ≈ 2^314, far exceeding the field prime. The sum therefore wraps modulo P. An attacker can craft resource-bound values such that the total sum ≡ 0 (mod P), causing `charge_fee` to return immediately without charging any fee.

---

### Finding Description

`compute_max_possible_fee` computes:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit
     + l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip)
     + l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit;
``` [1](#0-0) 

The only bounds established on these fields come from `pack_resource_bounds`, called during transaction hash computation:

```cairo
assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
assert_nn(resource_bounds.max_price_per_unit);
``` [2](#0-1) 

`assert_nn` only constrains `max_price_per_unit` to `[0, P/2)`. It does **not** impose a tight upper bound sufficient to prevent overflow when multiplied by `max_amount`. The product `(2^64 - 1) * (P/2 - 1) ≈ 2^314` is approximately `2^63` times the field prime, so the multiplication wraps around modulo P many times.

`charge_fee` then gates all fee collection on the result:

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);
if (max_fee == 0) {
    return ();
}
``` [3](#0-2) 

If the attacker forces `max_fee ≡ 0 (mod P)`, the function returns without executing the ERC-20 transfer, and the sequencer receives nothing.

---

### Impact Explanation

**Critical — Direct loss of funds.**

An attacker can execute invoke, deploy-account, or declare transactions at zero cost. The sequencer's fee revenue is entirely bypassed. Because the OS proof is generated over these Cairo constraints, the proof will be valid and accepted on L1, permanently recording fee-free execution. There is no fallback check: once `compute_max_possible_fee` returns 0, the fee transfer is unconditionally skipped.

---

### Likelihood Explanation

Any unprivileged V3 transaction sender can trigger this. No special role, key, or operator cooperation is required. The attacker only needs to submit a standard signed V3 transaction with crafted `max_amount` / `max_price_per_unit` values. The values that cause the wrap are easy to compute (see PoC below), and the transaction hash computation does not prevent them — `pack_resource_bounds` only checks `assert_nn`, which the crafted values satisfy. The attack is repeatable across every block.

---

### Recommendation

In `compute_max_possible_fee`, add explicit upper-bound range checks on `max_price_per_unit` for each resource type before performing the multiplication. The bound must be tight enough that the maximum possible product — and the sum of all three products — cannot reach the field prime. For example, enforce `max_price_per_unit ≤ 2^64 - 1` (matching the bound already applied to `max_amount`), which keeps each product within `[0, 2^128)` and the total sum within `[0, 3 * 2^128)`, well below P ≈ 2^251. Alternatively, perform the fee computation in a multi-limb (e.g., Uint256) representation that is immune to field-prime wrap-around.

---

### Proof of Concept

Let P = Cairo field prime ≈ 3618502788666131213697322783095070105623107215331596699973092056135872020481.

Choose:
- `l1_gas_max_amount = 2`, `l1_gas_max_price = k` where `P/4 < k < P/2`
- `l2_gas_max_amount = 1`, `l2_gas_max_price = P − 2k`
- `l1_data_gas_max_amount = 0` (any), `tip = 0`

**Validity of crafted values:**
- `l1_gas_max_amount = 2 ≤ 2^64 − 1` ✓
- `l1_gas_max_price = k ∈ (P/4, P/2)` → passes `assert_nn` ✓
- `l2_gas_max_amount = 1 ≤ 2^64 − 1` ✓
- `l2_gas_max_price = P − 2k`: since `k > P/4`, `2k > P/2`, so `P − 2k < P/2`; since `k < P/2`, `2k < P`, so `P − 2k > 0` → passes `assert_nn` ✓

**Fee computation (mod P):**

```
2 * k  +  1 * (P − 2k)  =  2k + P − 2k  =  P  ≡  0  (mod P)
```

`compute_max_possible_fee` returns `0`. `charge_fee` hits `if (max_fee == 0) { return (); }` and exits without transferring any fee. The transaction executes fully for free, and the resulting STARK proof is valid and accepted on L1.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L99-101)
```text
    return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
        (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
        l1_data_gas_bounds.max_price_per_unit;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L121-125)
```text
    let max_fee = compute_max_possible_fee(tx_info=tx_info);

    if (max_fee == 0) {
        return ();
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L104-105)
```text
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
    assert_nn(resource_bounds.max_price_per_unit);
```
