### Title
Missing Bounds Validation on Resource Bounds Values Enables Fee Bypass via Felt Arithmetic Wrap-Around — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` multiplies and sums user-controlled resource bounds fields (`max_amount`, `max_price_per_unit`, `tip`) in Cairo felt arithmetic without any bounds validation. Because Cairo arithmetic is modulo the Stark prime P ≈ 2²⁵¹, a transaction sender can craft these values so the sum wraps to exactly 0 mod P. When `max_fee == 0`, `charge_fee` returns immediately without charging any fee, allowing the transaction to execute for free.

---

### Finding Description

`compute_max_possible_fee` computes:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit
     + l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip)
     + l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit;
``` [1](#0-0) 

All six operands (`max_amount` × 3, `max_price_per_unit` × 3, `tip`) are loaded from the transaction via hints with **no range or sanity checks**: [2](#0-1) 

The result is used immediately in `charge_fee`:

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);
if (max_fee == 0) {
    return ();
}
``` [3](#0-2) 

When `max_fee == 0`, the function returns before the `assert_nn_le(calldata.amount.low, max_fee)` guard is ever reached, and no ERC-20 transfer is executed. [4](#0-3) 

---

### Impact Explanation

**Critical — Direct loss of funds.**

The sequencer receives zero fee for executing the transaction. At scale, an attacker can submit an unbounded volume of economically free transactions, draining sequencer revenue and potentially causing a network halt from transaction spam. The OS proof remains valid because the OS itself enforces no bounds on these fields; the prover can include such transactions and produce a sound proof.

---

### Likelihood Explanation

**High.** Any unprivileged transaction sender controls `max_amount`, `max_price_per_unit`, and `tip` in their own signed transaction. No privileged access, leaked key, or external dependency is required. The attacker only needs to solve a simple modular arithmetic equation over the publicly known Stark prime.

---

### Recommendation

Add explicit range checks on each resource bounds field before using them in fee arithmetic. For example, assert that each `max_amount` fits in a u64 and each `max_price_per_unit` fits in a u128 using `assert_nn_le`:

```cairo
assert_nn_le(l1_gas_bounds.max_amount, MAX_U64);
assert_nn_le(l1_gas_bounds.max_price_per_unit, MAX_U128);
// ... repeat for l2_gas and l1_data_gas bounds, and for tip
```

This mirrors the existing pattern used for `signature_len` and `calldata_size`: [5](#0-4) [6](#0-5) 

---

### Proof of Concept

Let P = Stark prime = 2²⁵¹ + 17·2¹⁹² + 1.

Craft a V3 transaction with:

| Field | Value |
|---|---|
| `l1_gas.max_amount` | `1` |
| `l1_gas.max_price_per_unit` | `P − 1` |
| `l2_gas.max_amount` | `1` |
| `l2_gas.max_price_per_unit` | `1` |
| `tip` | `0` |
| `l1_data_gas.max_amount` | `0` |
| `l1_data_gas.max_price_per_unit` | `0` |

Computation inside `compute_max_possible_fee`:

```
max_fee = 1·(P−1) + 1·(1+0) + 0·0
        = P − 1 + 1
        = P
        ≡ 0  (mod P)
```

`charge_fee` receives `max_fee = 0`, hits the early-return branch, and charges nothing. The transaction's `__validate__` and `__execute__` entry points run normally, and the OS proof is valid. [7](#0-6)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L134-136)
```text
    // Verify that the charged amount is not larger than the transaction's max_fee field.
    assert_nn_le(calldata.amount.low, max_fee);

```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L170-198)
```text
func get_account_tx_common_fields(
    block_context: BlockContext*, tx_hash_prefix: felt, sender_address: felt
) -> CommonTxFields* {
    alloc_locals;
    local resource_bounds: ResourceBounds*;
    local tip;
    local paymaster_data_length;
    local paymaster_data: felt*;
    local nonce_data_availability_mode;
    local fee_data_availability_mode;
    local nonce;
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L218-218)
```text
    assert_nn_le(signature_len, SIERRA_ARRAY_LEN_BOUND - 1);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L485-485)
```text
    assert_nn_le(tx_execution_context.calldata_size, SIERRA_ARRAY_LEN_BOUND - 1);
```
