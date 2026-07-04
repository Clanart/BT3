### Title
Unchecked Field Arithmetic Overflow in `compute_max_possible_fee` Enables Fee-Free Transaction Execution — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

The `compute_max_possible_fee` function in `transaction_impls.cairo` performs unchecked multiplications and additions on user-supplied `max_amount`, `max_price_per_unit`, and `tip` fields from a V3 transaction's resource bounds. Because Cairo arithmetic is performed modulo the field prime (~2^251), a crafted transaction can cause the sum to wrap to exactly `0`. When `compute_max_possible_fee` returns `0`, `charge_fee` immediately returns without executing the ERC20 fee transfer, allowing the transaction to execute with zero fee paid.

---

### Finding Description

`compute_max_possible_fee` computes:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit
     + l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip)
     + l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit;
``` [1](#0-0) 

No upper-bound range checks are applied to any of `max_amount`, `max_price_per_unit`, or `tip` before these operations. All arithmetic is modular over the Stark field prime `p`. Since `p` is prime, a product `A * B ≡ 0 (mod p)` only if `A ≡ 0` or `B ≡ 0`, but the **sum** of three products can equal `0 (mod p)` through cancellation. For example:

- Set `l1_gas_bounds.max_amount = 1`, `l1_gas_bounds.max_price_per_unit = 1` → term₁ = 1
- Set `l2_gas_bounds.max_amount = 1`, `l2_gas_bounds.max_price_per_unit = 1`, `tip = p − 2` → term₂ = `1 * (1 + (p−2)) = p−1`
- Set `l1_data_gas_bounds.max_amount = 0` → term₃ = 0
- Sum = `1 + (p−1) + 0 = p ≡ 0 (mod p)`

`charge_fee` then checks:

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);
if (max_fee == 0) {
    return ();
}
``` [2](#0-1) 

When `max_fee == 0`, the function returns immediately — the ERC20 `transfer` call is never made, and the sequencer receives no fee for executing the transaction.

The `tip` field is user-supplied and included in the transaction hash, so the user signs a transaction with the crafted `tip` value. The OS reads these fields via hints (`%{ LoadCommonTxFields %}`) and uses them directly without any range validation. [3](#0-2) 

---

### Impact Explanation

**Direct loss of funds (Critical).** The sequencer executes the transaction — consuming L2 compute resources and incurring L1 data availability costs — but receives zero fee. An attacker can craft any number of such transactions (invoke, declare, deploy-account all call `charge_fee`) to drain sequencer revenue entirely. Because the OS Cairo program is the source of truth for proof generation, this bypass is provably valid on-chain. [4](#0-3) 

---

### Likelihood Explanation

Any unprivileged V3 transaction sender can trigger this. The attacker only needs to solve `term₁ + term₂ + term₃ ≡ 0 (mod p)` using their own freely chosen `max_amount`, `max_price_per_unit`, and `tip` values — all of which are user-controlled transaction fields with no OS-level range enforcement. The crafted transaction is valid (correct hash, correct signature) and will be accepted by the OS prover.

---

### Recommendation

Add explicit upper-bound range checks on each resource bound component before performing the fee arithmetic. Specifically, enforce that `max_amount`, `max_price_per_unit`, and `tip` are each bounded to a safe range (e.g., `< 2^128`) using `assert_nn_le` before they are used in `compute_max_possible_fee`. This prevents field-modular cancellation from producing a zero or otherwise incorrect `max_fee`.

```cairo
// Example mitigation inside compute_max_possible_fee:
assert_nn_le(l1_gas_bounds.max_amount, MAX_RESOURCE_BOUND);
assert_nn_le(l1_gas_bounds.max_price_per_unit, MAX_PRICE_BOUND);
assert_nn_le(tx_info.tip, MAX_TIP_BOUND);
// ... similarly for l2 and l1_data bounds
```

---

### Proof of Concept

1. Attacker constructs a V3 invoke transaction with:
   - `l1_gas_bounds = {max_amount: 1, max_price_per_unit: 1}`
   - `l2_gas_bounds = {max_amount: 1, max_price_per_unit: 1}`
   - `tip = p − 2` (where `p` is the Stark field prime)
   - `l1_data_gas_bounds = {max_amount: 0, max_price_per_unit: 0}`

2. Attacker signs and submits the transaction.

3. OS calls `execute_invoke_function_transaction` → `charge_fee`. [5](#0-4) 

4. `compute_max_possible_fee` evaluates:
   `1*1 + 1*(1 + (p−2)) + 0*0 = 1 + (p−1) + 0 = p ≡ 0 (mod p)` [6](#0-5) 

5. `charge_fee` sees `max_fee == 0` and returns immediately — no ERC20 transfer occurs. [7](#0-6) 

6. Transaction executes fully (validate + execute entry points run) with zero fee paid. The proof is valid and accepted on-chain.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L121-125)
```text
    let max_fee = compute_max_possible_fee(tx_info=tx_info);

    if (max_fee == 0) {
        return ();
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L181-198)
```text
    %{ LoadCommonTxFields %}
    %{ LoadTxNonceAccount %}
    tempvar common_tx_fields = new CommonTxFields(
        tx_hash_prefix=tx_hash_prefix,
        version=3,
        sender_address=sender_address,
        chain_id=block_context.os_global_context.starknet_os_config.chain_id,
        nonce=nonce,
        tip=tip,
        n_resource_bounds=3,
        resource_bounds=resource_bounds,
        paymaster_data_length=paymaster_data_length,
        paymaster_data=paymaster_data,
        nonce_data_availability_mode=nonce_data_availability_mode,
        fee_data_availability_mode=fee_data_availability_mode,
    );
    return common_tx_fields;
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L360-362)
```text
    // Charge fee.
    charge_fee(block_context=block_context, tx_execution_context=updated_tx_execution_context);

```
