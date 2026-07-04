### Title
Felt Arithmetic Overflow in `compute_max_possible_fee` Enables Complete Fee Bypass — (File: `execution/transaction_impls.cairo`)

---

### Summary

In `charge_fee`, the function `compute_max_possible_fee` performs felt (field) arithmetic on user-controlled resource bounds. Because `max_price_per_unit` is only bounded to `[0, P/2)` and `max_amount` to `[0, 2^64-1]`, their products can overflow the Stark field prime P and wrap around. A transaction sender can craft resource bounds such that the sum evaluates to exactly `0 mod P`, causing `charge_fee` to return immediately without executing any ERC20 fee transfer.

---

### Finding Description

In `charge_fee`:

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);

if (max_fee == 0) {
    return ();   // <-- fee collection entirely skipped
}
```

`compute_max_possible_fee` computes:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit
     + l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip)
     + l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit;
``` [1](#0-0) 

All arithmetic is felt arithmetic (modular, mod P ≈ 2^251). The only constraints enforced on the inputs (via `pack_resource_bounds` during hash computation) are:

- `max_amount ∈ [0, 2^64 - 1]` (`assert_nn_le`)
- `max_price_per_unit ∈ [0, P/2)` (`assert_nn`)
- `tip ∈ [0, 2^64 - 1]` (`assert_nn_le`) [2](#0-1) 

The product `max_amount * max_price_per_unit` can reach up to `(2^64 - 1) * (P/2 - 1) ≈ 2^313`, which is approximately `2^62 * P`. This wraps around the field ~2^62 times, and the result mod P can be any value in `[0, P-1]`.

**Concrete construction**: A sender wants to execute a transaction with `l2_gas_bounds.max_amount = G` (large, e.g., 10^9) and `l2_gas_bounds.max_price_per_unit = D`. The L2 contribution is `G * D mod P`. The attacker sets:

- `l1_gas_bounds.max_amount = A` (small, e.g., 2)
- `l1_gas_bounds.max_price_per_unit = B = (P - G*D) * A^{-1} mod P`

Since P is prime, `A^{-1} mod P` always exists for `A ≠ 0`. The attacker checks whether `B < P/2`; if not, they try `A = 3, 4, ...` — roughly half of all choices of `A` yield `B < P/2`. Then:

```
A*B + G*D + 0 ≡ (P - G*D) + G*D ≡ 0 (mod P)
```

`compute_max_possible_fee` returns `0`, and `charge_fee` returns immediately without transferring any fee tokens. [3](#0-2) 

The transaction still has `l2_gas_bounds.max_amount = G` gas available (from `get_initial_user_gas_bound`), so it can execute meaningful state changes. [4](#0-3) 

---

### Impact Explanation

**Critical. Direct loss of funds.**

The fee ERC20 transfer from the user's account to the sequencer is entirely skipped. The user retains the fee tokens they should have paid. The sequencer receives nothing for processing the transaction. Because the OS proof accepts the block as valid (the Cairo constraints are satisfied), the sequencer cannot reject the block after the fact. Any transaction type — invoke, declare, deploy-account — that calls `charge_fee` is affected. [5](#0-4) [6](#0-5) [7](#0-6) 

---

### Likelihood Explanation

Any unprivileged transaction sender can exploit this. The attacker only needs to solve a linear equation over the Stark field (finding `A, B` such that `A*B ≡ T mod P` with `B < P/2`), which is trivial with basic modular arithmetic. No special access, leaked keys, or privileged roles are required. The crafted resource bounds pass all existing hash-time validation checks (`assert_nn_le`, `assert_nn`). The attack is deterministic and repeatable.

---

### Recommendation

In `compute_max_possible_fee`, enforce that each multiplicative term does not overflow the field before summing. Specifically, add range checks ensuring that each product `max_amount * max_price_per_unit` fits within a safe bound (e.g., `< 2^128`) before performing felt arithmetic. Alternatively, bound `max_price_per_unit` to a value small enough that `max_amount * max_price_per_unit < P` is guaranteed (e.g., `max_price_per_unit ≤ 2^64 - 1`), and then verify the sum of three such products also cannot overflow P. The analogous fix to the pool report is: enforce a meaningful lower bound on the computed fee rather than treating a zero result as "no fee due."

---

### Proof of Concept

1. Attacker wants to invoke a contract with `l2_gas_bounds.max_amount = 1_000_000_000` and `l2_gas_bounds.max_price_per_unit = 1_000` (so the honest fee would be `10^12` tokens).
2. Compute `T = P - 10^12` (the negation of the L2 contribution mod P).
3. Set `l1_gas_bounds.max_amount = 2`. Compute `B = T * 2^{-1} mod P = (P - 10^12) / 2` (integer division; since P is odd and `10^12` is even, `P - 10^12` is odd — try `A = 3` instead: `B = T * 3^{-1} mod P`). Verify `B < P/2`.
4. Set `l1_data_gas_bounds.max_amount = 0`, `tip = 0`.
5. Sign the transaction with these resource bounds (the hash commits to them).
6. Submit the transaction. The OS computes `compute_max_possible_fee = A*B + 10^12 + 0 ≡ (P - 10^12) + 10^12 ≡ 0 (mod P)`.
7. `charge_fee` hits `if (max_fee == 0) { return (); }` and exits — no fee is charged.
8. The transaction executes with `1_000_000_000` L2 gas, performing arbitrary state changes for free. [8](#0-7)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L74-78)
```text
// Returns the transaction's initial gas derived from its resource bounds.
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L120-135)
```text
    local tx_info: TxInfo* = tx_execution_context.execution_info.tx_info;
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L360-362)
```text
    // Charge fee.
    charge_fee(block_context=block_context, tx_execution_context=updated_tx_execution_context);

```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L686-688)
```text
    // Charge fee.
    charge_fee(block_context=block_context, tx_execution_context=validate_deploy_execution_context);

```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L821-824)
```text
    // Charge fee.
    charge_fee(
        block_context=block_context, tx_execution_context=validate_declare_execution_context
    );
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
