### Title
Unbounded Fee Product Arithmetic in `compute_max_possible_fee` Causes `assert_nn_le` Failure and Block Proof Invalidity - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

`compute_max_possible_fee` computes the maximum chargeable fee as a sum of `max_amount * max_price_per_unit` products in Cairo felt arithmetic. `max_amount` is bounded to `[0, 2^64 - 1]` and `max_price_per_unit` is bounded to `[0, 2^128 - 1]` (by `assert_nn`), so their product can reach ~`2^192`. The downstream `assert_nn_le(actual_fee, max_fee)` call implicitly requires `max_fee - actual_fee ≤ 2^128 - 1`. When `max_fee > 2^128 - 1`, this range-check fails for any realistic `actual_fee`, making the OS execution abort and the block proof invalid.

---

### Finding Description

**Step 1 — Bounds established during hash computation**

In `pack_resource_bounds` (`transaction_hash/transaction_hash.cairo`, lines 103–107):

```cairo
func pack_resource_bounds{range_check_ptr}(resource_bounds: ResourceBounds) -> felt {
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);   // max_amount ∈ [0, 2^64-1]
    assert_nn(resource_bounds.max_price_per_unit);            // max_price_per_unit ∈ [0, 2^128-1]
    ...
}
```

`assert_nn` uses the range-check builtin, which checks values in `[0, 2^128 - 1]`. So `max_price_per_unit` is allowed to be any value up to `2^128 - 1`.

**Step 2 — Unchecked product in fee computation**

In `compute_max_possible_fee` (`transaction_impls.cairo`, lines 99–101):

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
    (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
    l1_data_gas_bounds.max_price_per_unit;
```

With `max_amount = 2^64 - 1` and `max_price_per_unit = 2^128 - 1`, a single product is:

```
(2^64 - 1) × (2^128 - 1) ≈ 2^192
```

This is well below the STARK prime (~`2^251`), so no modular wrapping occurs — the felt result is the true integer product `≈ 2^192`. The sum of three such products can reach `≈ 3 × 2^192`.

**Step 3 — Implicit bound violated in `charge_fee`**

In `charge_fee` (`transaction_impls.cairo`, line 135):

```cairo
assert_nn_le(calldata.amount.low, max_fee);
```

`assert_nn_le(a, b)` is implemented as:
1. `assert_nn(a)` → `a ∈ [0, 2^128 - 1]`
2. `assert_nn(b - a)` → `b - a ∈ [0, 2^128 - 1]`

When `max_fee ≈ 2^192` and `actual_fee` is any realistic small value (e.g., `10^9`):

```
max_fee - actual_fee ≈ 2^192  >>  2^128 - 1
```

The range-check on `b - a` fails. This is an OS-level assertion failure (not a user-level revert), which aborts the entire OS execution and invalidates the block proof.

**Why the sequencer may not catch this off-chain**

The StarkNet protocol specification defines `max_price_per_unit` as a `u128` field. A sequencer's off-chain mempool validation would accept any value in `[0, 2^128 - 1]` as valid. The sequencer's fee estimation code likely uses native 64-bit or 128-bit integer arithmetic and would compute `max_fee` as a large but "valid" integer. It would not simulate the `assert_nn_le` range-check constraint that implicitly requires `max_fee ≤ actual_fee + 2^128 - 1`. The sequencer would include the transaction, then fail to produce a valid proof.

---

### Impact Explanation

**High — Network not being able to confirm new transactions.**

If a transaction with `max_price_per_unit ≥ 2^64` (for `max_amount = 2^64 - 1`) is included in a block, the OS aborts at `assert_nn_le(actual_fee, max_fee)` in `charge_fee`. The block proof is invalid and cannot be submitted to L1. The sequencer must reconstruct the block, but without tooling to identify the offending transaction, repeated inclusion attempts could cause sustained proof failures and block production stalls.

---

### Likelihood Explanation

**Medium.** The StarkNet protocol explicitly allows `max_price_per_unit` to be any `u128` value. A user can submit a V3 transaction with `max_price_per_unit = 2^64` (≈ 18 ETH per gas unit — unreasonably high but within the protocol's declared range). The sequencer's off-chain validation, which mirrors the `assert_nn` check, would accept this. The OS-level `assert_nn_le` failure is not caught until proof generation. The attacker requires no special privileges — only the ability to submit a signed V3 transaction.

---

### Recommendation

Add an explicit upper bound on `max_price_per_unit` in `pack_resource_bounds` to ensure the product `max_amount * max_price_per_unit` cannot exceed `2^128 - 1`:

```cairo
// Ensure max_amount * max_price_per_unit fits in 128 bits.
// With max_amount ≤ 2^64 - 1, max_price_per_unit must be ≤ 2^64 - 1.
assert_nn_le(resource_bounds.max_price_per_unit, 2 ** 64 - 1);
```

Alternatively, use a `Uint256` accumulator in `compute_max_possible_fee` and validate the final sum fits in 128 bits before passing it to `assert_nn_le`.

---

### Proof of Concept

1. Craft a V3 invoke transaction with:
   - `l1_gas.max_amount = 2^64 - 1`
   - `l1_gas.max_price_per_unit = 2^64` (passes `assert_nn`; within u128 range)
   - Other resource bounds set to zero
2. Submit to the sequencer. Off-chain validation accepts it (max_price_per_unit is a valid u128).
3. Sequencer includes the transaction in a block and runs the OS prover.
4. OS executes `compute_max_possible_fee`:
   - `max_fee = (2^64 - 1) * 2^64 ≈ 2^128` (no felt wrapping; `2^128 < P`)
5. OS executes `charge_fee` → `assert_nn_le(actual_fee, 2^128)`:
   - `assert_nn(2^128 - actual_fee)` fails because `2^128 - actual_fee > 2^128 - 1`
6. OS execution aborts. Block proof is invalid. Block cannot be finalized on L1.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2)

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
