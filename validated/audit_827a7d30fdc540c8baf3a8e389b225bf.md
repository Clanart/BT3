I now have a complete picture of the vulnerability. The critical finding is a **discrepancy between the blockifier's saturating arithmetic and the OS's raw felt arithmetic** in fee computation.

---

### Title
Felt Arithmetic in `compute_max_possible_fee` Produces Values Exceeding `assert_nn_le` Range, Causing Proof Generation Failure and Network Halt — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

The StarkNet OS function `compute_max_possible_fee` computes the maximum fee using raw Cairo felt arithmetic (no bounds checks), while the blockifier's equivalent `ValidResourceBounds::max_possible_fee` uses **saturating arithmetic**. When a user submits a V3 transaction with resource bounds whose product exceeds 2^128 (easily achievable within the `u64 × u128` type limits), the OS computes a `max_fee` value that is a valid felt but exceeds 2^128. The subsequent `assert_nn_le(calldata.amount.low, max_fee)` call in `charge_fee` then unconditionally fails because the Cairo range-check builtin requires `max_fee − calldata.amount.low < 2^128`. The blockifier, using saturating arithmetic, silently clamps the same value to `u128::MAX` and accepts the transaction. The sequencer includes the transaction; the prover cannot generate a valid proof; the block is unprovable — a network halt.

---

### Finding Description

**Root cause — OS side (`transaction_impls.cairo`, lines 87–102):**

```cairo
func compute_max_possible_fee(tx_info: TxInfo*) -> felt {
    ...
    return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
        (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
        l1_data_gas_bounds.max_price_per_unit;
}
```

All arithmetic is raw felt multiplication and addition — no range assertions on the inputs, no overflow guard. The result is a felt value that can legally be anywhere in `[0, P)`.

**Downstream failure — `charge_fee` (`transaction_impls.cairo`, lines 111–165):**

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);
if (max_fee == 0) { return (); }
...
assert_nn_le(calldata.amount.low, max_fee);   // ← fails when max_fee ≥ 2^128
```

`assert_nn_le(a, b)` is implemented as `assert_nn(a)` + `assert_le(a, b)`, where `assert_le(a, b)` internally calls `assert_nn(b − a)`. The range-check builtin enforces `0 ≤ b − a < 2^128`. When `max_fee ≥ 2^128`, the difference `max_fee − calldata.amount.low ≥ 2^128` regardless of what the sequencer sets `calldata.amount.low` to (it must be `< 2^128` to pass `assert_nn(a)`). The constraint is unsatisfiable; proof generation aborts.

**Contrast — blockifier side (`crates/starknet_api/src/transaction/fields.rs`, lines 383–403):**

```rust
pub fn max_possible_fee(&self, tip: Tip) -> Fee {
    ...
    l1_gas.max_amount.saturating_mul(l1_gas.max_price_per_unit)
        .saturating_add(l2_gas.max_amount.saturating_mul(...))
        .saturating_add(l1_data_gas.max_amount.saturating_mul(...))
}
```

Saturating arithmetic silently clamps any overflow to `u128::MAX`. The blockifier therefore accepts the transaction as having `max_fee = u128::MAX`, which is a valid, non-zero fee. Pre-validation passes; the transaction enters the block.

**Arithmetic reachability:**

`max_amount` is typed `GasAmount(u64)` and `max_price_per_unit` is typed `GasPrice(u128)`. Their product can reach `(2^64 − 1) × (2^128 − 1) ≈ 2^192`, which is:
- Far below the Stark prime P ≈ 2^251 → **no felt wrap-around**, the OS computes the true product.
- Far above 2^128 → **`assert_nn_le` unconditionally fails**.

A minimal trigger: `l1_gas.max_amount = 2`, `l1_gas.max_price_per_unit = 2^127 + 1` → product = `2^128 + 2 > 2^128`.

---

### Impact Explanation

When the sequencer includes such a transaction, the prover runs the OS and hits the unsatisfiable `assert_nn_le` constraint inside `charge_fee`. Proof generation for the entire block fails. The block cannot be finalized on L1. If an attacker repeatedly submits such transactions (they are cheap to craft and sign), every block that includes one becomes unprovable, causing a **total network shutdown** — no new transactions can be confirmed.

This matches the allowed impact: **High — Network not being able to confirm new transactions (total network shutdown)**.

---

### Likelihood Explanation

- **Trivial to craft**: any user can sign a V3 transaction with `l1_gas.max_amount = 2` and `l1_gas.max_price_per_unit = 2^127 + 1`. Both values are within the protocol-defined type bounds (`u64`, `u128`).
- **No privileged access required**: the attacker is an ordinary transaction sender.
- **Sequencer is unaware**: the blockifier's saturating arithmetic hides the overflow; standard pre-validation (`check_resource_bounds`) only verifies `max_price_per_unit ≥ actual_gas_price` and `max_amount ≥ minimal_gas_amount` — neither check detects the overflow.
- **Repeatable**: the attacker can submit many such transactions across many blocks.

---

### Recommendation

1. **In the OS**: add explicit range assertions on `max_amount` and `max_price_per_unit` before computing the fee, or compute the fee using a checked/bounded multiplication that asserts each intermediate product fits in 128 bits:

   ```cairo
   // Before computing the product, assert inputs are within u64/u128 bounds.
   assert_nn_le(l1_gas_bounds.max_amount, MAX_U64);
   assert_nn_le(l1_gas_bounds.max_price_per_unit, MAX_U128);
   // ... similarly for l2 and l1_data bounds ...
   ```

2. **Alternatively**: cap `max_fee` to `MAX_U128` after computing it, mirroring the blockifier's saturating behavior:

   ```cairo
   let raw_fee = l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + ...;
   let max_fee = if raw_fee > MAX_U128 { MAX_U128 } else { raw_fee };
   ```

3. **In the blockifier**: add a pre-validation check that rejects transactions where the true (non-saturating) product of any resource bound exceeds `u128::MAX`, so the blockifier and OS agree on which transactions are valid.

---

### Proof of Concept

1. Attacker constructs a V3 `INVOKE` transaction with:
   - `l1_gas.max_amount = 2` (fits in `u64`)
   - `l1_gas.max_price_per_unit = 2^127 + 1` (fits in `u128`)
   - `l2_gas.max_amount = 0`, `l1_data_gas.max_amount = 0` (zeroed to isolate the trigger)
   - Valid signature, valid nonce, valid calldata.

2. **Blockifier path**: `max_possible_fee` computes `2 × (2^127 + 1) = 2^128 + 2`, which overflows `u128`, saturates to `u128::MAX`. Pre-validation passes. Transaction enters the mempool and is selected for a block.

3. **OS path** (inside `charge_fee`, `transaction_impls.cairo` line 121):
   - `compute_max_possible_fee` returns `2 × (2^127 + 1) = 2^128 + 2` as a felt (no overflow, since `2^128 + 2 ≪ P`).
   - `max_fee ≠ 0`, so execution continues.
   - Sequencer hint `LoadActualFee` sets `low_actual_fee = 0` (the only value that could pass `assert_nn(a)`).
   - `assert_nn_le(0, 2^128 + 2)` → internally calls `assert_nn(2^128 + 2 − 0)` → range-check on `2^128 + 2` fails (value ≥ 2^128).
   - Cairo VM raises an unsatisfiable constraint; proof generation for the block aborts.

4. The block is unprovable. The sequencer must discard it. Repeated submissions halt the network. [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L111-135)
```text
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

    local low_actual_fee;
    %{ LoadActualFee %}
    local calldata: TransferCallData = TransferCallData(
        recipient=block_context.block_info_for_execute.sequencer_address,
        amount=Uint256(low=low_actual_fee, high=0),
    );

    // Verify that the charged amount is not larger than the transaction's max_fee field.
    assert_nn_le(calldata.amount.low, max_fee);
```

**File:** crates/starknet_api/src/transaction/fields.rs (L380-404)
```rust
    /// Returns the maximum possible fee that can be charged for the transaction.
    /// The computation is saturating, meaning that if the result is larger than the maximum
    /// possible fee, the maximum possible fee is returned.
    pub fn max_possible_fee(&self, tip: Tip) -> Fee {
        match self {
            ValidResourceBounds::L1Gas(l1_bounds) => {
                l1_bounds.max_amount.saturating_mul(l1_bounds.max_price_per_unit)
            }
            ValidResourceBounds::AllResources(AllResourceBounds {
                l1_gas,
                l2_gas,
                l1_data_gas,
            }) => l1_gas
                .max_amount
                .saturating_mul(l1_gas.max_price_per_unit)
                .saturating_add(
                    l2_gas
                        .max_amount
                        .saturating_mul(l2_gas.max_price_per_unit.saturating_add(tip.into())),
                )
                .saturating_add(
                    l1_data_gas.max_amount.saturating_mul(l1_data_gas.max_price_per_unit),
                ),
        }
    }
```
