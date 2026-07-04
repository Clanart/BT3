### Title
Unchecked Upper Bound on `max_price_per_unit` Enables Felt-Arithmetic Fee Wrap-Around to Zero — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo`)

---

### Summary

`pack_resource_bounds` validates that `max_amount ≤ 2^64 − 1` but only asserts `max_price_per_unit ≥ 0` — it never asserts `max_price_per_unit ≤ 2^128 − 1`. Because `compute_max_possible_fee` then multiplies these felt values together in raw felt arithmetic (mod the Stark prime P ≈ 2^251), an attacker can craft resource bounds whose products sum to exactly 0 mod P. When `compute_max_possible_fee` returns 0, `charge_fee` immediately returns without executing the ERC-20 transfer, so the transaction is executed with zero fee paid.

---

### Finding Description

**`pack_resource_bounds` — missing upper-bound check on `max_price_per_unit`** [1](#0-0) 

```cairo
func pack_resource_bounds{range_check_ptr}(resource_bounds: ResourceBounds) -> felt {
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);   // ✓ bounded
    assert_nn(resource_bounds.max_price_per_unit);            // ✗ only ≥ 0, NOT ≤ 2^128-1
    return (resource_bounds.resource * 2 ** 64 + resource_bounds.max_amount) * 2 ** 128 +
        resource_bounds.max_price_per_unit;
}
```

`assert_nn` only proves the value is a non-negative felt (i.e., in `[0, P−1]`). Values in `(2^128, P−1]` pass silently. The `ResourceBounds` struct stores all fields as raw `felt`: [2](#0-1) 

**`compute_max_possible_fee` — unchecked felt multiplication** [3](#0-2) 

The sum `A1·P1 + A2·(P2 + tip) + A3·P3` is computed entirely in felt arithmetic. No overflow guard exists. If the attacker chooses values such that this sum ≡ 0 (mod P), the function returns 0.

**`charge_fee` — zero-fee early exit** [4](#0-3) 

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);
if (max_fee == 0) {
    return ();   // ← fee transfer is skipped entirely
}
```

When `max_fee == 0`, the ERC-20 transfer to the sequencer is never executed.

---

### Impact Explanation

- **Direct loss of funds (Critical):** The sequencer receives no fee for executing the transaction. The attacker's account balance is never debited.
- **Network halt (High):** With free execution, an attacker can flood the network with computationally expensive transactions at zero cost, exhausting sequencer and prover resources and halting block production.

---

### Likelihood Explanation

The attack requires only a crafted v3 transaction — no privileged role, no key leak, no third-party compromise. The transaction hash computation calls `pack_resource_bounds` (which enforces only the lower bound on `max_price_per_unit`), so the crafted values are committed to in the signed hash and accepted by the OS verifier. A sequencer-level mempool filter might reject obviously out-of-range prices, but the OS itself provides no such guarantee, meaning any sequencer (including a future one with a looser filter, or one that is itself the attacker) can include such a transaction and produce a valid proof.

---

### Recommendation

In `pack_resource_bounds`, add an upper-bound range check on `max_price_per_unit` to enforce the protocol-specified u128 limit:

```cairo
assert_nn_le(resource_bounds.max_price_per_unit, 2 ** 128 - 1);
```

This mirrors the existing check on `max_amount` and closes the felt-arithmetic wrap-around path in `compute_max_possible_fee`. [1](#0-0) 

---

### Proof of Concept

Let P = Stark prime ≈ 2^251 + 17·2^192 + 1.

Craft a v3 invoke transaction with:

| Field | Value |
|---|---|
| `l1_gas.max_amount` | `1` |
| `l1_gas.max_price_per_unit` | `V` (any value in `[1, P−1]`) |
| `l2_gas.max_amount` | `G` (desired gas budget, e.g. 10^6) |
| `l2_gas.max_price_per_unit` | `(P − V) · modinv(G, P) mod P` |
| `l1_data_gas.max_amount` | `0` |
| `tip` | `0` |

Then:

```
max_fee = 1·V + G·((P−V)·modinv(G,P)) + 0
        = V + (P−V)   (mod P)
        = P           (mod P)
        = 0
```

`pack_resource_bounds` accepts `(P−V)·modinv(G,P) mod P` because `assert_nn` passes for any felt ≥ 0. `compute_max_possible_fee` returns 0. `charge_fee` hits the `if (max_fee == 0) { return (); }` branch and the sequencer receives no payment. [5](#0-4) [1](#0-0)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/common/new_syscalls.cairo (L55-62)
```text
struct ResourceBounds {
    // The name of the resource (e.g., 'L1_GAS').
    resource: felt,
    // The maximum amount of the resource allowed for usage during the execution.
    max_amount: felt,
    // The maximum price the user is willing to pay for the resource unit.
    max_price_per_unit: felt,
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
