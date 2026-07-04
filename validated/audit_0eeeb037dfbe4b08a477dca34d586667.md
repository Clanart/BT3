### Title
Felt Arithmetic Overflow in `compute_max_possible_fee` Enables Complete Fee Bypass - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` computes the maximum chargeable fee using unchecked felt arithmetic. Because `max_price_per_unit` has no upper-bound constraint in the OS, a transaction sender can craft resource-bounds values whose product-sum wraps to exactly 0 modulo the Stark prime. The OS then treats `max_fee == 0` as a signal to skip fee charging entirely, allowing the transaction to execute for free.

---

### Finding Description

`compute_max_possible_fee` returns a raw felt sum of three products:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
    (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
    l1_data_gas_bounds.max_price_per_unit;
``` [1](#0-0) 

All arithmetic is modular (mod P, the Stark prime ≈ 2²⁵¹). The only constraint enforced on `max_price_per_unit` during transaction-hash computation is `assert_nn` — a non-negativity check with **no upper bound**:

```cairo
assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
assert_nn(resource_bounds.max_price_per_unit);   // ← no upper bound
``` [2](#0-1) 

`max_amount` is bounded to [0, 2⁶⁴−1] and `tip` to [0, 2⁶⁴−1]: [3](#0-2) 

But `max_price_per_unit` can be any felt in [0, P−1]. A user can therefore choose three `max_price_per_unit` values such that the entire sum in `compute_max_possible_fee` equals exactly P ≡ 0 (mod P).

`charge_fee` then hits the early-exit guard:

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);
if (max_fee == 0) {
    return ();          // ← fee transfer is completely skipped
}
``` [4](#0-3) 

The subsequent `assert_nn_le(calldata.amount.low, max_fee)` guard is never reached, so no ERC-20 transfer is executed. [5](#0-4) 

This affects every V3 account transaction type: invoke, declare, and deploy-account, all of which call `charge_fee`. [6](#0-5) 

---

### Impact Explanation

**Critical — Direct loss of funds.**

The sequencer is entitled to collect fees for every executed transaction. When `compute_max_possible_fee` wraps to 0, the OS proof certifies a state transition in which the fee token balance of the sequencer is never incremented, yet the transaction's side-effects (storage writes, L2→L1 messages, contract deployments) are fully applied. Any verifier (L1 core contract) that accepts this proof accepts a block where real computation was performed without any fee payment, constituting a direct, provable loss of fee revenue for the sequencer and, by extension, the protocol.

---

### Likelihood Explanation

**Medium.**

The attack requires a single unprivileged V3 transaction sender. The math to find a valid set of `max_price_per_unit` values is trivial (solve `a·p1 + b·(p2+tip) + c·p3 ≡ 0 mod P` with known `a,b,c` ≤ 2⁶⁴−1). The signed transaction hash commits to these values, so the crafted transaction is self-consistent and passes hash verification. The only practical barrier is that the sequencer's off-chain mempool code (written in Python/Rust with arbitrary-precision integers) would compute a large non-zero `max_fee` and accept the transaction, while the OS Cairo code computes 0 and skips the fee — the discrepancy is the root of the exploit.

---

### Recommendation

Add an upper-bound range check on `max_price_per_unit` in `pack_resource_bounds` (e.g., `assert_nn_le(resource_bounds.max_price_per_unit, 2 ** 128 - 1)`) so that each product `max_amount * max_price_per_unit` is bounded to less than 2¹⁹² and the three-term sum cannot wrap around the Stark prime. Alternatively, add an explicit assertion in `compute_max_possible_fee` that the returned value is non-zero whenever at least one resource bound is non-zero, or use `assert_nn_le` on the final result to enforce it fits within a safe range. [7](#0-6) 

---

### Proof of Concept

**Setup:** Craft a V3 invoke transaction with `max_amount = 1` for all three resource types and `tip = 0`.

**Goal:** Choose `p1 = max_price_per_unit` for L1 gas, `p2` for L2 gas, `p3` for L1 data gas such that:

```
1·p1 + 1·p2 + 1·p3 ≡ 0  (mod P)
```

**Solution (example):**
```
p1 = 1
p2 = 1
p3 = P - 2   (a valid felt, passes assert_nn since P-2 > 0 in felt representation)
```

Then `p1 + p2 + p3 = 1 + 1 + (P−2) = P ≡ 0 (mod P)`.

**Execution path:**
1. User signs and submits the transaction. `hash_fee_fields` calls `pack_resource_bounds` for each bound; `assert_nn(P−2)` passes because `P−2` is a valid non-negative felt.
2. The sequencer's off-chain code computes `max_fee = 1 + 1 + (P−2)` in Python big-integer arithmetic ≈ 2²⁵¹, sees a large positive value, and includes the transaction in a block.
3. The OS Cairo code evaluates `compute_max_possible_fee` in felt arithmetic: `1 + 1 + (P−2) mod P = 0`.
4. `charge_fee` hits `if (max_fee == 0) { return (); }` and exits without transferring any tokens.
5. The transaction's `__execute__` body runs normally; all state changes are committed.
6. The resulting STARK proof is valid and accepted by the L1 verifier. The sequencer receives zero fee for a fully executed transaction. [8](#0-7)

### Citations

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L360-361)
```text
    // Charge fee.
    charge_fee(block_context=block_context, tx_execution_context=updated_tx_execution_context);
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L117-117)
```text
    assert_nn_le(tip, 2 ** 64 - 1);
```
