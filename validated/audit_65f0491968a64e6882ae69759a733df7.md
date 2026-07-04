### Title
Unchecked Field Arithmetic in `compute_max_possible_fee` Enables Silent Fee Bypass via Modular Overflow - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` in `transaction_impls.cairo` performs direct field-element multiplication on `ResourceBounds.max_amount` and `ResourceBounds.max_price_per_unit` without first validating that these values are within their protocol-specified ranges (u64 and u128 respectively). Because Cairo arithmetic is modular over the Stark prime P ≈ 2²⁵¹, an attacker can craft specific felt values for these fields such that the entire sum wraps to exactly 0 mod P. When `max_fee == 0`, `charge_fee` unconditionally skips fee collection, allowing the attacker to execute transactions without paying any fee.

---

### Finding Description

`compute_max_possible_fee` computes the authorized fee ceiling as:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit
     + l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip)
     + l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit;
``` [1](#0-0) 

All six operands (`max_amount` × 3, `max_price_per_unit` × 3) are raw `felt` values loaded from the transaction via the hint `LoadCommonTxFields`. No `assert_nn_le` or range-check constraint is applied to any of them before this arithmetic. The Starknet spec defines `max_amount` as u64 and `max_price_per_unit` as u128, but the OS never enforces these bounds. [2](#0-1) 

The result is consumed immediately in `charge_fee`:

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);
if (max_fee == 0) {
    return ();
}
``` [3](#0-2) 

If `max_fee` evaluates to 0 (mod P), fee charging is silently skipped entirely. The subsequent guard `assert_nn_le(calldata.amount.low, max_fee)` is never reached. [4](#0-3) 

---

### Impact Explanation

**Direct loss of funds (Critical).**

The OS is the authoritative proof artifact. If the OS accepts a block where `compute_max_possible_fee` overflows to 0 and fee charging is skipped, the resulting STARK proof is valid for a block in which the sequencer collected no fees. The protocol has no recourse: the proof is sound by construction, but the economic invariant (users pay for execution) is violated. At scale, an attacker can drain sequencer revenue entirely by submitting only crafted zero-fee transactions.

---

### Likelihood Explanation

**High.** The attack requires only that a transaction sender choose specific felt values for `max_amount` and `max_price_per_unit` in their V3 transaction's resource bounds. No privileged access, leaked key, or external dependency is needed. The attacker signs the transaction hash (which commits to these values), so the signature check passes. The construction is deterministic and requires only basic modular arithmetic to find valid inputs.

**Concrete example of overflow-to-zero inputs:**

The Stark prime P = 2²⁵¹ + 17·2¹⁹² + 1. Set:
- `l1_gas_bounds.max_amount = 1`, `l1_gas_bounds.max_price_per_unit = P − 1` (= −1 in the field, a valid felt)
- `l2_gas_bounds.max_amount = 1`, `l2_gas_bounds.max_price_per_unit = 1`, `tip = 0`
- `l1_data_gas_bounds.max_amount = 0`

Then:
```
1·(P−1) + 1·(1+0) + 0 = P − 1 + 1 = P ≡ 0 (mod P)
```

`compute_max_possible_fee` returns 0. Fee charging is skipped.

---

### Recommendation

Before performing any arithmetic in `compute_max_possible_fee`, validate that each `ResourceBounds` field is within its protocol-specified range. Add explicit range checks when the resource bounds are loaded (in `get_account_tx_common_fields` or `fill_account_tx_info`):

```cairo
// Validate resource bounds are within spec-defined ranges.
// max_amount must be a u64: [0, 2^64 - 1]
assert_nn_le(resource_bounds[L1_GAS_INDEX].max_amount, MAX_U64);
assert_nn_le(resource_bounds[L2_GAS_INDEX].max_amount, MAX_U64);
assert_nn_le(resource_bounds[L1_DATA_GAS_INDEX].max_amount, MAX_U64);
// max_price_per_unit must be a u128: [0, 2^128 - 1]
assert_nn_le(resource_bounds[L1_GAS_INDEX].max_price_per_unit, MAX_U128);
assert_nn_le(resource_bounds[L2_GAS_INDEX].max_price_per_unit, MAX_U128);
assert_nn_le(resource_bounds[L1_DATA_GAS_INDEX].max_price_per_unit, MAX_U128);
// tip must also be bounded (u64)
assert_nn_le(tip, MAX_U64);
```

With these constraints in place, the maximum possible product of any two terms is bounded by u64 × u128 = 2¹⁹², which is far below P ≈ 2²⁵¹, making field overflow impossible.

---

### Proof of Concept

1. Attacker constructs a V3 `invoke` transaction with:
   - `l1_gas_bounds = ResourceBounds(max_amount=1, max_price_per_unit=P−1)`
   - `l2_gas_bounds = ResourceBounds(max_amount=1, max_price_per_unit=1)`
   - `l1_data_gas_bounds = ResourceBounds(max_amount=0, max_price_per_unit=0)`
   - `tip = 0`
2. Attacker signs the transaction hash (which commits to these values). Signature verification passes normally.
3. OS calls `compute_max_possible_fee`:
   - `1·(P−1) + 1·(1+0) + 0·0 = P ≡ 0 (mod P)`
4. `charge_fee` checks `if (max_fee == 0) { return (); }` — fee charging is skipped.
5. The transaction executes fully (e.g., a large `__execute__` call) with zero fee paid.
6. The OS produces a valid STARK proof for this block. The proof is accepted by the verifier.
7. The sequencer collects no fee for the transaction despite full execution.

The root cause is at: [1](#0-0) 

with the missing bounds validation that should appear in: [5](#0-4)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L134-135)
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
