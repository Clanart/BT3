### Title
Fee Computation Overflow via User-Controlled Resource Bounds Allows Zero-Fee Transaction Execution — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` performs unchecked felt arithmetic (mod P) over user-controlled `max_price_per_unit` fields. An unprivileged transaction sender can craft resource bounds that cause the fee sum to wrap to exactly 0 mod P. When `max_fee == 0`, `charge_fee` returns immediately without debiting the sender's account, allowing arbitrary transaction execution with no fee payment.

---

### Finding Description

`compute_max_possible_fee` computes the maximum chargeable fee as a plain felt sum of products:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
    (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
    l1_data_gas_bounds.max_price_per_unit;
``` [1](#0-0) 

All arithmetic is modulo the Cairo prime P ≈ 2^251. The user-controlled fields are bounded as follows:

- `max_amount` ≤ 2^64 − 1 (enforced by `assert_nn_le` in `pack_resource_bounds`)
- `max_price_per_unit` ∈ [0, (P−1)/2] (enforced only by `assert_nn`, which checks non-negativity, **not** an upper bit-width bound)
- `tip` ≤ 2^64 − 1 [2](#0-1) 

Because `max_price_per_unit` can be up to ~2^250, the product `max_amount × max_price_per_unit` can reach ~2^314, wrapping around modulo P an arbitrary number of times. The attacker can choose values such that the entire sum ≡ 0 (mod P).

When `max_fee == 0`, `charge_fee` exits immediately:

```cairo
if (max_fee == 0) {
    return ();
}
``` [3](#0-2) 

No ERC-20 transfer is executed, no `assert_nn_le` bound check is reached, and the sender's account balance is never debited.

The `charge_fee` function is called unconditionally after every invoke, deploy-account, and declare transaction: [4](#0-3) [5](#0-4) [6](#0-5) 

---

### Impact Explanation

**Critical — Direct loss of funds.**

The sender's account balance is not debited for the transaction fee. The sequencer receives no STRK/ETH payment for executing the transaction. Because the OS is the authoritative execution environment whose output is proven and verified on L1, a block containing such transactions is accepted as valid by the protocol. Any number of transactions can be executed for free, draining sequencer revenue and violating the fundamental protocol invariant that every transaction must pay fees.

---

### Likelihood Explanation

**Medium-High.** The attacker is an ordinary transaction sender. The required computation is elementary modular arithmetic: find `(X, Y, Z)` with each in `[0, (P−1)/2]` such that `X + Y + Z = P`. A concrete solution is `X = Y = ⌊P/3⌋`, `Z = P − 2⌊P/3⌋`. The attacker sets `max_amount = 1` for each resource and `max_price_per_unit` to these values, signs the transaction (committing to these fields via the transaction hash), and submits it. The sequencer may not independently validate for felt-arithmetic overflow in fee computation before including the transaction.

---

### Recommendation

Add an explicit upper-bound range check on `max_price_per_unit` in `pack_resource_bounds` (e.g., `assert_nn_le(resource_bounds.max_price_per_unit, 2 ** 128 - 1)`) so that the product `max_amount × max_price_per_unit` cannot exceed the field prime. Additionally, add a post-computation assertion in `compute_max_possible_fee` that the returned value is non-zero and within a safe range before it is used as a fee cap.

---

### Proof of Concept

1. Choose Cairo prime P = 2^251 + 17·2^192 + 1.
2. Set `l1_gas_bounds = {max_amount: 1, max_price_per_unit: ⌊P/3⌋}`.
3. Set `l2_gas_bounds = {max_amount: 1, max_price_per_unit: ⌊P/3⌋}`, `tip = 0`.
4. Set `l1_data_gas_bounds = {max_amount: 1, max_price_per_unit: P − 2·⌊P/3⌋}`.
5. Each `max_price_per_unit` value is in `[0, (P−1)/2]`, so `assert_nn` passes in `pack_resource_bounds`.
6. `compute_max_possible_fee` computes `⌊P/3⌋ + ⌊P/3⌋ + (P − 2·⌊P/3⌋) = P ≡ 0 (mod P)`.
7. `charge_fee` hits `if (max_fee == 0) { return (); }` and exits without transferring any tokens.
8. The transaction (invoke/declare/deploy-account) executes fully with zero fee deducted from the sender's account.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L99-101)
```text
    return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
        (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
        l1_data_gas_bounds.max_price_per_unit;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L123-125)
```text
    if (max_fee == 0) {
        return ();
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L360-361)
```text
    // Charge fee.
    charge_fee(block_context=block_context, tx_execution_context=updated_tx_execution_context);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L686-687)
```text
    // Charge fee.
    charge_fee(block_context=block_context, tx_execution_context=validate_deploy_execution_context);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L821-824)
```text
    // Charge fee.
    charge_fee(
        block_context=block_context, tx_execution_context=validate_declare_execution_context
    );
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
