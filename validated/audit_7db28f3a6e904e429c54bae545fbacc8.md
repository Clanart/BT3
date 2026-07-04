### Title
Felt Arithmetic Overflow in `compute_max_possible_fee` Enables Complete Fee Bypass via Crafted Resource Bounds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` performs unchecked felt arithmetic (mod P, the Stark prime ≈ 2²⁵¹) over `max_price_per_unit` values that are only validated to be non-negative — with no upper bound. An unprivileged transaction sender can craft V3 resource bounds such that the sum of products wraps to exactly 0 modulo P. `charge_fee` then hits its early-return guard (`if (max_fee == 0) { return (); }`) and skips the ERC-20 fee transfer entirely, allowing arbitrary transaction execution with zero fee paid.

---

### Finding Description

**Step 1 — Missing upper-bound on `max_price_per_unit`**

In `pack_resource_bounds`, called during transaction hash verification:

```cairo
assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
assert_nn(resource_bounds.max_price_per_unit);   // ← only checks >= 0, no ceiling
```

`max_price_per_unit` is accepted as any felt in `[0, P)`. [1](#0-0) 

**Step 2 — Unchecked felt multiplication in `compute_max_possible_fee`**

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
    (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
    l1_data_gas_bounds.max_price_per_unit;
```

All arithmetic is modular (mod P). With `max_price_per_unit` unbounded, the sum of three products can wrap to any value in `[0, P)`, including 0. [2](#0-1) 

**Step 3 — Fee bypass guard**

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);
if (max_fee == 0) {
    return ();
}
```

When the overflow-induced result is 0, `charge_fee` returns immediately — no ERC-20 transfer is executed, no fee is collected. [3](#0-2) 

**Secondary issue — `assert_nn_le` range-check failure when `max_fee > 2¹²⁸`**

If the overflow produces a non-zero felt > 2¹²⁸, the subsequent check `assert_nn_le(calldata.amount.low, max_fee)` will always fail because Cairo range checks are bounded by 2¹²⁸ and `max_fee − actual_fee` overflows the range-check cell. This makes any block containing such a transaction unprovable. [4](#0-3) 

---

### Impact Explanation

**Primary — Direct loss of funds (Critical):** The sequencer/protocol permanently loses fee revenue for every transaction that exploits the bypass. Because the OS proof is valid (all Cairo constraints are satisfied), the block is accepted on-chain with zero fee collected. The fee tokens that should have been transferred to the sequencer are never moved; they remain in the sender's account — an accounting discrepancy that cannot be corrected after the fact.

**Secondary — Network halt (High):** Free execution enables unlimited spam. An attacker can flood the sequencer with zero-cost transactions, exhausting block capacity and preventing legitimate transactions from being confirmed.

---

### Likelihood Explanation

Any V3 transaction sender (invoke, deploy-account, declare) can trigger this. No privileged role, leaked key, or external dependency is required. The attacker only needs to solve a simple linear equation over the felt field to find `max_price_per_unit` values that sum to 0 mod P — a trivial offline computation. The transaction passes signature verification normally because the hash commits to the raw field values, not to their arithmetic product.

---

### Recommendation

Add an explicit upper-bound check on `max_price_per_unit` inside `pack_resource_bounds` (or equivalently inside `compute_max_possible_fee`) so that the product `max_amount * max_price_per_unit` cannot overflow 2¹²⁸:

```cairo
// In pack_resource_bounds (transaction_hash.cairo):
assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
assert_nn_le(resource_bounds.max_price_per_unit, 2 ** 128 - 1);  // add this
```

With both operands bounded to 64 bits and 128 bits respectively, the product fits in 192 bits, well below P ≈ 2²⁵¹, eliminating the overflow. Additionally, `compute_max_possible_fee` should use `Uint256` arithmetic or equivalent range-checked addition to prevent the sum of three such products from overflowing.

---

### Proof of Concept

Choose the following resource bounds for a V3 invoke transaction (tip = 0):

```
l1_gas_bounds:      max_amount = 1,  max_price_per_unit = P - Y   (for any Y in [1, P-1])
l2_gas_bounds:      max_amount = 1,  max_price_per_unit = Y        (tip = 0, so Y - tip = Y)
l1_data_gas_bounds: max_amount = 0,  max_price_per_unit = 0
```

`compute_max_possible_fee` evaluates to:

```
1*(P - Y) + 1*(Y + 0) + 0*0  =  P - Y + Y  =  P  ≡  0  (mod P)
```

All individual checks pass:
- `assert_nn_le(max_amount, 2^64 - 1)` → 1 ≤ 2⁶⁴ − 1 ✓
- `assert_nn(max_price_per_unit)` → P − Y ≥ 0 (valid felt) ✓
- `assert_nn_le(tip, 2^64 - 1)` → 0 ≤ 2⁶⁴ − 1 ✓

The transaction hash is computed and signed normally. When the OS processes the block, `charge_fee` computes `max_fee = 0` and returns at line 123 without executing any ERC-20 transfer. The transaction's state changes are committed, and no fee is paid. [5](#0-4) [1](#0-0)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L134-135)
```text
    // Verify that the charged amount is not larger than the transaction's max_fee field.
    assert_nn_le(calldata.amount.low, max_fee);
```
