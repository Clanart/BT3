### Title
Felt Arithmetic Overflow in `compute_max_possible_fee` Enables Complete Fee Bypass - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` computes the transaction fee ceiling using unchecked felt arithmetic (modular arithmetic mod the Stark field prime `p ≈ 2^251 + 17·2^192 + 1`). An unprivileged transaction sender can craft resource bounds whose felt-arithmetic sum wraps to exactly `0 mod p`. The OS then unconditionally skips fee collection, allowing execution of transactions — including repeated reverted ones — with zero fee paid. The resulting OS execution trace is valid and provable, meaning the prover accepts blocks containing such transactions.

---

### Finding Description

**Root cause — `compute_max_possible_fee`:** [1](#0-0) 

```cairo
func compute_max_possible_fee(tx_info: TxInfo*) -> felt {
    ...
    return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
        (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
        l1_data_gas_bounds.max_price_per_unit;
}
```

All arithmetic is felt arithmetic — every multiplication and addition is implicitly `mod p`. There is no range assertion, no Uint256 intermediate, and no overflow guard on any operand or on the final sum.

**Trigger — `charge_fee` early-exit on zero:** [2](#0-1) 

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);

if (max_fee == 0) {
    return ();
}
```

When `max_fee` evaluates to `0` (whether legitimately or via wrap-around), `charge_fee` returns immediately — no ERC-20 transfer is executed, no fee is deducted from the sender.

**Fee is charged unconditionally after execution, including for reverted transactions:** [3](#0-2) 

```cairo
// Charge fee.
charge_fee(block_context=block_context, tx_execution_context=updated_tx_execution_context);
```

This call is outside the `is_reverted` branch, meaning even reverted transactions are supposed to pay fees. The overflow bypass defeats this for all transaction outcomes.

**Gas bound is derived independently from L2 gas only:** [4](#0-3) 

```cairo
func get_initial_user_gas_bound(common_tx_fields: CommonTxFields*) -> felt {
    assert common_tx_fields.n_resource_bounds = 3;
    return common_tx_fields.resource_bounds[L2_GAS_INDEX].max_amount;
}
```

`l2_gas_bounds.max_amount` feeds the gas counter independently of `max_fee`. Setting it to a small non-zero value keeps the transaction alive while the fee sum wraps to zero.

---

### Impact Explanation

The OS-level Cairo proof accepts blocks containing transactions that paid zero fees. This is exploitable at the protocol layer:

1. **Sequencer deception**: An off-chain sequencer implementation likely computes `max_fee` using 256-bit arithmetic (getting a large, non-zero value) and includes the transaction expecting compensation. The OS computes `0` in felt arithmetic and skips the charge. The sequencer receives nothing.
2. **Fee-free spam**: An attacker can submit an unbounded stream of transactions (including deliberately reverted ones) that consume block space without paying. Because the OS proof is valid, these transactions are indistinguishable from legitimate ones at the proof-verification layer.
3. **Network congestion leading to shutdown**: Sustained fee-free spam fills blocks, starving legitimate transactions. Sequencers operating at a loss have no economic incentive to continue, matching the "network not being able to confirm new transactions" impact class.

**Allowed impact matched:** High — Network not being able to confirm new transactions (total network shutdown).

---

### Likelihood Explanation

- **No privileged access required.** Any transaction sender can craft the resource bounds.
- **Trivially constructible.** The attacker only needs to choose felt-valid values whose sum is `≡ 0 (mod p)` (see PoC below).
- **Signed and committed.** The resource bounds are included in the transaction hash and signed by the sender — no forgery is needed; the sender willingly signs the crafted values.
- **No sequencer cooperation required** in a decentralized sequencer environment; in the current centralized setting the sequencer may filter `max_fee == 0` off-chain, but the OS itself provides no such enforcement.

---

### Recommendation

Replace felt arithmetic in `compute_max_possible_fee` with checked Uint256 arithmetic. Each product `max_amount * max_price_per_unit` should be computed as a 256-bit multiplication with an explicit overflow assertion (e.g., assert the high limb is zero or within a safe bound). The final sum should similarly be accumulated in Uint256. Additionally, add an explicit `assert max_fee != 0` guard before the early-return in `charge_fee` to reject transactions whose resource bounds are all zero, rather than silently skipping fee collection.

---

### Proof of Concept

Let `p = 2^251 + 17·2^192 + 1` (the Stark field prime). Choose:

| Field | Value |
|---|---|
| `l1_gas_bounds.max_amount` | `1` |
| `l1_gas_bounds.max_price_per_unit` | `1` |
| `l2_gas_bounds.max_amount` | `1` |
| `l2_gas_bounds.max_price_per_unit` | `p − 1` (valid felt: the additive inverse of 1) |
| `l1_data_gas_bounds.max_amount` | `0` |
| `tip` | `0` |

Felt arithmetic in `compute_max_possible_fee`:

```
max_fee = (1 × 1) + (1 × (p − 1 + 0)) + 0
        = 1 + (p − 1)
        = p
        ≡ 0  (mod p)
```

`charge_fee` hits `if (max_fee == 0) { return (); }` and exits without transferring any tokens.

`get_initial_user_gas_bound` returns `l2_gas_bounds.max_amount = 1`, so the transaction is not immediately rejected for having zero gas. The transaction executes (or reverts due to out-of-gas), and in either case no fee is charged. The resulting execution trace is fully valid and provable.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L75-78)
```text
func get_initial_user_gas_bound(common_tx_fields: CommonTxFields*) -> felt {
    assert common_tx_fields.n_resource_bounds = 3;
    return common_tx_fields.resource_bounds[L2_GAS_INDEX].max_amount;
}
```

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L360-362)
```text
    // Charge fee.
    charge_fee(block_context=block_context, tx_execution_context=updated_tx_execution_context);

```
