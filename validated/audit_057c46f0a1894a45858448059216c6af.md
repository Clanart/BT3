### Title
Fee Bypass via Zero `max_price_per_unit` in V3 Transactions — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` in `transaction_impls.cairo` computes the maximum chargeable fee as a pure product of user-supplied `max_price_per_unit` and `max_amount` fields. If a user sets all three `max_price_per_unit` values and `tip` to zero, the function returns `0`. `charge_fee` then unconditionally skips the ERC-20 transfer, allowing the transaction to execute with no fee paid. The OS-level proof remains valid, so the L1 verifier accepts the block.

---

### Finding Description

`compute_max_possible_fee` (lines 87–102) computes:

```
l1_gas.max_amount * l1_gas.max_price_per_unit
+ l2_gas.max_amount * (l2_gas.max_price_per_unit + tip)
+ l1_data_gas.max_amount * l1_data_gas.max_price_per_unit
``` [1](#0-0) 

If an attacker sets all three `max_price_per_unit` fields to `0` and `tip` to `0`, the entire expression evaluates to `0` regardless of `max_amount`.

`charge_fee` then checks:

```cairo
if (max_fee == 0) {
    return ();
}
``` [2](#0-1) 

This early return skips the ERC-20 transfer entirely. No fee is charged.

The only validation on `max_price_per_unit` in the OS is `assert_nn(resource_bounds.max_price_per_unit)` inside `pack_resource_bounds`, which asserts `>= 0` — it explicitly permits zero. [3](#0-2) 

Similarly, `tip` is only validated with `assert_nn_le(tip, 2**64 - 1)`, which also permits zero. [4](#0-3) 

Meanwhile, `get_initial_user_gas_bound` returns `l2_gas_bounds.max_amount` as the execution gas budget: [5](#0-4) 

An attacker sets `l2_gas_bounds.max_amount` to a sufficient value (e.g., 10,000,000) while keeping all prices at zero. The transaction executes with full gas budget and zero fee.

This affects all three V3 account transaction types: invoke, declare, and deploy\_account, all of which call `charge_fee`. [6](#0-5) 

---

### Impact Explanation

**Critical — Direct loss of funds.**

The fee mechanism is the protocol's economic enforcement layer. By bypassing it at the OS level, an attacker can execute arbitrary state-changing transactions (token transfers, contract calls, storage writes) without paying any fee. The generated STARK proof is valid; the L1 verifier accepts the block. The sequencer receives no compensation, and the attacker's account balance is not debited. At scale, this constitutes direct, provable loss of funds from the fee-collection system and enables unlimited free execution of value-extracting transactions.

---

### Likelihood Explanation

A V3 transaction with all-zero prices is syntactically valid and passes all OS-level assertions. The only barrier is sequencer-level mempool policy. If the sequencer does not enforce `max_price_per_unit > 0` in its mempool (a check that is not mandated by the OS proof system), the transaction is included and proven. Even a single colluding or misconfigured sequencer is sufficient. The attacker controls all relevant fields directly as transaction parameters.

---

### Recommendation

Add an explicit non-zero check inside `compute_max_possible_fee` or at the entry of `charge_fee`. The OS should assert that `max_fee > 0` is required for any transaction that is not explicitly designated as fee-exempt (e.g., L1 handlers). Concretely, after computing `max_fee`, assert:

```cairo
assert_nn_le(1, max_fee);  // enforce max_fee >= 1
```

Alternatively, enforce `max_price_per_unit >= 1` for at least one resource inside `pack_resource_bounds` or `hash_fee_fields`, mirroring the pattern used for `max_amount`.

---

### Proof of Concept

1. Construct a V3 invoke transaction with:
   - `l1_gas_bounds = ResourceBounds(resource=L1_GAS, max_amount=0, max_price_per_unit=0)`
   - `l2_gas_bounds = ResourceBounds(resource=L2_GAS, max_amount=10_000_000, max_price_per_unit=0)`
   - `l1_data_gas_bounds = ResourceBounds(resource=L1_DATA_GAS, max_amount=0, max_price_per_unit=0)`
   - `tip = 0`
2. Submit to a sequencer (or directly inject into a block being built).
3. The OS executes `compute_max_possible_fee`: `0*0 + 10_000_000*(0+0) + 0*0 = 0`.
4. `charge_fee` hits `if (max_fee == 0) { return (); }` and returns without transferring any tokens.
5. The transaction's `__validate__` and `__execute__` entry points run normally with `remaining_gas = 10_000_000`.
6. The resulting STARK proof is valid; the L1 verifier accepts the block.
7. The attacker's fee token balance is unchanged; the sequencer receives zero fee.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L75-78)
```text
func get_initial_user_gas_bound(common_tx_fields: CommonTxFields*) -> felt {
    assert common_tx_fields.n_resource_bounds = 3;
    return common_tx_fields.resource_bounds[L2_GAS_INDEX].max_amount;
}
```

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L104-105)
```text
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
    assert_nn(resource_bounds.max_price_per_unit);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L117-117)
```text
    assert_nn_le(tip, 2 ** 64 - 1);
```
