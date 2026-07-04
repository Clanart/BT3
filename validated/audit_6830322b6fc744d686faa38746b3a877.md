### Title
Unchecked Felt Arithmetic Overflow in `compute_max_possible_fee` Enables Complete Fee Bypass — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` computes the transaction fee cap using raw felt-field arithmetic with no overflow guard. Because Cairo `felt` values are elements of a prime field (Stark prime P ≈ 2²⁵¹), multiplications and additions silently wrap modulo P. An unprivileged transaction sender can craft V3 `resource_bounds` values that make the function return exactly `0`. The caller `charge_fee` then hits the `if (max_fee == 0) { return (); }` early-exit and skips the ERC-20 fee transfer entirely, so the transaction executes with zero fee paid.

---

### Finding Description

`compute_max_possible_fee` is defined as:

```cairo
func compute_max_possible_fee(tx_info: TxInfo*) -> felt {
    ...
    return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
        (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
        l1_data_gas_bounds.max_price_per_unit;
}
``` [1](#0-0) 

The only upstream bounds enforced on the inputs come from `pack_resource_bounds` (called during transaction-hash computation):

```cairo
assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);   // max_amount ∈ [0, 2⁶⁴−1]
assert_nn(resource_bounds.max_price_per_unit);             // max_price ∈ [0, (P−1)/2]
``` [2](#0-1) 

And `tip` is bounded to `[0, 2⁶⁴−1]`: [3](#0-2) 

With `max_amount ≤ 2⁶⁴−1` and `max_price_per_unit ≤ (P−1)/2 ≈ 2²⁵⁰`, the product `max_amount × max_price_per_unit` can reach ≈ 2³¹⁴, which wraps modulo P an arbitrary number of times. No `assert_nn_le` or range-check is applied to the *result* of `compute_max_possible_fee`.

**Concrete wrap-to-zero example** (all values satisfy their individual constraints):

| Field | Value |
|---|---|
| `l1_gas_bounds.max_amount` | `2` |
| `l1_gas_bounds.max_price_per_unit` | `(P−1)/2` |
| `l2_gas_bounds.max_amount` | `1` |
| `l2_gas_bounds.max_price_per_unit` | `1` |
| `tip` | `0` |
| `l1_data_gas_bounds.max_amount` | `0` |

Felt arithmetic: `2 × (P−1)/2 + 1×1 + 0 = (P−1) + 1 = P ≡ 0 (mod P)`.

All individual `assert_nn_le` / `assert_nn` checks pass. `compute_max_possible_fee` returns `0`.

`charge_fee` then executes:

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);
if (max_fee == 0) {
    return ();          // ← fee transfer is skipped entirely
}
``` [4](#0-3) 

The ERC-20 `transfer` to the sequencer is never executed. The transaction is included in a proven block with zero fee paid.

---

### Impact Explanation

**Direct loss of funds (Critical).**

The sequencer is entitled to the fee for executing the transaction. When `max_fee` wraps to `0`, the OS-enforced ERC-20 debit from the user's account is skipped. The sequencer receives nothing. Because the StarkNet OS is the authoritative enforcement layer whose output is verified on L1, a valid proof can be generated for a block containing fee-free transactions. The user's account balance is never debited; the sequencer's balance is never credited. This constitutes a direct, provable loss of funds at the protocol level.

A secondary variant: if the attacker crafts bounds so that `max_fee` wraps to a small non-zero value (e.g., `1`), the `assert_nn_le(calldata.amount.low, max_fee)` check caps the actual fee at that tiny value, allowing arbitrarily expensive execution for near-zero cost. [5](#0-4) 

---

### Likelihood Explanation

**High.** Any unprivileged V3 transaction sender controls `resource_bounds` and `tip` directly. The arithmetic to find a wrap-to-zero combination requires only solving a simple modular equation over the Stark prime, which is public knowledge. No privileged access, leaked key, or operator cooperation is required. The attacker submits the crafted transaction; a non-malicious sequencer may include it (its off-chain fee estimator, written in Python/Rust with 256-bit integers, would compute a large non-zero fee and consider the transaction valid), while the OS silently skips the fee.

---

### Recommendation

After computing `max_fee`, assert that the result is within a safe integer range before using it as a fee cap:

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);
// Ensure the felt arithmetic did not wrap; max_fee must fit in a u128.
assert_nn_le(max_fee, MAX_SAFE_FEE_BOUND);  // e.g., 2**128 - 1
if (max_fee == 0) {
    return ();
}
```

Alternatively, perform the fee computation using `Uint256` arithmetic (as already used for the transfer calldata) so that overflow is structurally impossible, or add individual range checks on each product before summing.

---

### Proof of Concept

1. Construct a V3 invoke transaction with:
   - `l1_gas_bounds = { max_amount: 2, max_price_per_unit: (P−1)/2 }`
   - `l2_gas_bounds = { max_amount: 1, max_price_per_unit: 1 }`
   - `tip = 0`
   - `l1_data_gas_bounds = { max_amount: 0, max_price_per_unit: 0 }`

2. All individual field checks pass:
   - `assert_nn_le(2, 2⁶⁴−1)` ✓
   - `assert_nn((P−1)/2)` ✓ (value is exactly at the `assert_nn` boundary)
   - `assert_nn_le(0, 2⁶⁴−1)` ✓ (tip)

3. `compute_max_possible_fee` computes:
   `2 × (P−1)/2 + 1 × (1 + 0) + 0 × 0 = (P−1) + 1 = P ≡ 0 (mod P)`

4. `charge_fee` hits `if (max_fee == 0) { return (); }` and returns without executing the ERC-20 transfer.

5. The transaction's `__execute__` entry point runs normally (state changes committed), but no fee is deducted from the sender's account.

6. The OS generates a valid proof for this block. The proof is accepted on L1. The sequencer has processed a transaction and received zero compensation. [6](#0-5) [7](#0-6)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L103-108)
```text
func pack_resource_bounds{range_check_ptr}(resource_bounds: ResourceBounds) -> felt {
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
    assert_nn(resource_bounds.max_price_per_unit);
    return (resource_bounds.resource * 2 ** 64 + resource_bounds.max_amount) * 2 ** 128 +
        resource_bounds.max_price_per_unit;
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L116-117)
```text
    assert data_to_hash[0] = tip;
    assert_nn_le(tip, 2 ** 64 - 1);
```
