### Title
Missing Upper Bound on `max_price_per_unit` Enables Felt Arithmetic Wrap-Around to Zero Fee, Allowing Fee-Free Transaction Execution — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo`)

---

### Summary

`pack_resource_bounds` validates `max_price_per_unit` only with `assert_nn` (non-negative), not with `assert_nn_le(..., 2**128 - 1)`. Because Cairo felt arithmetic is modulo the STARK prime p ≈ 2^251, an unprivileged transaction sender can supply `max_price_per_unit` values ≥ 2^128 that pass the check but cause `compute_max_possible_fee` to wrap to exactly 0 mod p. `charge_fee` then short-circuits on `max_fee == 0` and charges nothing, allowing the transaction to execute for free.

---

### Finding Description

In `pack_resource_bounds`:

```cairo
func pack_resource_bounds{range_check_ptr}(resource_bounds: ResourceBounds) -> felt {
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);   // ✓ bounded
    assert_nn(resource_bounds.max_price_per_unit);            // ✗ only non-negative, NOT ≤ 2**128-1
    return (resource_bounds.resource * 2 ** 64 + resource_bounds.max_amount) * 2 ** 128 +
        resource_bounds.max_price_per_unit;
}
```

`assert_nn(x)` in Cairo checks `x < p/2 ≈ 2^250`, so `max_price_per_unit` can be any value in `[0, (p-1)/2]`. Values ≥ 2^128 are accepted without error. [1](#0-0) 

`compute_max_possible_fee` then multiplies these unbounded values in felt arithmetic:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
    (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
    l1_data_gas_bounds.max_price_per_unit;
``` [2](#0-1) 

Because all arithmetic is mod p, an attacker can choose `max_price_per_unit` values such that the sum wraps to 0. `charge_fee` then hits:

```cairo
if (max_fee == 0) {
    return ();
}
``` [3](#0-2) 

…and returns without executing any ERC-20 transfer, so the sequencer receives zero fee.

---

### Impact Explanation

**Critical — Direct loss of funds.**

The sequencer/protocol loses all fee revenue for any transaction crafted with the wrap-around values. Because the OS proof is what the L1 verifier trusts, a block containing such transactions is accepted on-chain with zero fees collected. An attacker can spam the network with computationally expensive transactions at zero cost, draining sequencer revenue and potentially making the network economically unviable.

---

### Likelihood Explanation

**High.** Any unprivileged V3 transaction sender can craft the required `max_price_per_unit` values. No privileged access, leaked key, or external dependency is required. The values are simple arithmetic over the public STARK prime. The only constraint is that the values pass `assert_nn` (i.e., `< p/2`), which the crafted values satisfy. The attack is deterministic and repeatable.

---

### Recommendation

Replace the weak `assert_nn` with a tight upper-bound check in `pack_resource_bounds`:

```cairo
// Before (vulnerable):
assert_nn(resource_bounds.max_price_per_unit);

// After (fixed):
assert_nn_le(resource_bounds.max_price_per_unit, 2 ** 128 - 1);
```

This mirrors the existing bound already applied to `max_amount` and matches the StarkNet SNIP-8 specification that defines `max_price_per_unit` as a `u128`. [4](#0-3) 

---

### Proof of Concept

The STARK prime is `p = 2^251 + 17·2^192 + 1`. Note that `p + 1 ≡ 0 (mod 3)` (verifiable: `2^251 mod 3 = 2`, `17·2^192 mod 3 = 2`, `1 mod 3 = 1`, sum = 5 ≡ 2 mod 3; so `p mod 3 = 2`, `(p+1) mod 3 = 0` ✓). Also `p − 1` is even.

Craft a V3 transaction with:

| Field | Value |
|---|---|
| L1 gas `max_amount` | `3` |
| L1 gas `max_price_per_unit` | `(p+1)/3` ≈ 2^249.4 — passes `assert_nn` since `(p+1)/3 < p/2` |
| L2 gas `max_amount` | `2` |
| L2 gas `max_price_per_unit` | `(p−1)/2` — passes `assert_nn` since `(p−1)/2 < p/2` |
| L1 data gas `max_amount` | `0` |
| `tip` | `0` |

Fee computation:

```
fee = 3 · (p+1)/3  +  2 · (p−1)/2  +  0
    = (p+1)        +  (p−1)
    = 2p
    ≡ 0  (mod p)
```

`compute_max_possible_fee` returns `0`. `charge_fee` returns immediately at `if (max_fee == 0)`. The transaction executes with `initial_user_gas = max_amount[L2] = 2` units of L2 gas and pays **zero fee**.

Both `max_price_per_unit` values are ≥ 2^128, confirming the missing bound is the root cause. The `assert_nn_le(..., 2**128 - 1)` fix would reject both values and prevent the wrap-around. [1](#0-0) [5](#0-4)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L87-125)
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

// Charges a fee from the user.
// If max_fee is not 0, validates that the selector matches the entry point of an account contract
// and executes an ERC20 transfer on the behalf of that account contract.
//
// Arguments:
// block_context - a global context that is fixed throughout the block.
// tx_execution_context - The execution context of the transaction that pays the fee.
func charge_fee{
    range_check_ptr,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    outputs: OsCarriedOutputs*,
}(block_context: BlockContext*, tx_execution_context: ExecutionContext*) {
    alloc_locals;

    local tx_info: TxInfo* = tx_execution_context.execution_info.tx_info;
    let max_fee = compute_max_possible_fee(tx_info=tx_info);

    if (max_fee == 0) {
        return ();
    }
```
