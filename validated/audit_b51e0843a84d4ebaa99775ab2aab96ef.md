### Title
Unchecked Field Arithmetic Overflow in `compute_max_possible_fee` Enables Complete Fee Bypass - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

### Summary

`compute_max_possible_fee` performs unchecked multiplications of `max_amount * max_price_per_unit` in the Cairo prime field. Because `max_price_per_unit` is only validated as non-negative (`assert_nn`, allowing values up to `(P-1)/2 ≈ 2^250`) rather than bounded to 128 bits, an attacker can craft resource bounds whose products sum to `0 mod P`. This causes `charge_fee` to skip the fee transfer entirely, allowing arbitrary transactions to execute without paying fees.

### Finding Description

`pack_resource_bounds` in `transaction_hash.cairo` validates `max_price_per_unit` only with `assert_nn`: [1](#0-0) 

`assert_nn` only checks the value is in `[0, (P-1)/2]` — it does **not** bound it to `2^128 - 1` as the SNIP-8 specification requires. `max_amount` is correctly bounded to `[0, 2^64 - 1]`.

`compute_max_possible_fee` then multiplies these values directly in the Cairo field without overflow protection: [2](#0-1) 

All arithmetic is modular (mod P). The sum `A + B + C` can equal `0 mod P` for attacker-chosen valid inputs.

`charge_fee` then short-circuits on `max_fee == 0`: [3](#0-2) 

### Impact Explanation

When `compute_max_possible_fee` returns `0`, `charge_fee` returns immediately at line 123–125 without executing the ERC-20 transfer. The sequencer receives zero fee tokens for the transaction. An attacker can execute arbitrarily expensive `__execute__` calls (up to `EXECUTE_MAX_SIERRA_GAS = 1,100,000,000`) at zero cost. This constitutes **direct loss of funds** (sequencer fee revenue) and enables free spam that can cause **network shutdown**.

### Likelihood Explanation

The attack requires only crafting a valid V3 transaction with specific resource bound values — no privileged access, no leaked keys, no operator cooperation. The attacker signs the transaction hash (which commits to the crafted bounds), so the signature check passes. The sequencer's off-chain fee estimator uses integer arithmetic (not field arithmetic), so it computes a large `max_fee` and includes the transaction. The OS then computes `max_fee = 0 mod P` and skips the fee.

### Recommendation

Add an explicit upper-bound check on `max_price_per_unit` in `pack_resource_bounds`:

```cairo
assert_nn_le(resource_bounds.max_price_per_unit, 2 ** 128 - 1);
```

This mirrors the existing bound on `max_amount` and prevents field overflow in `compute_max_possible_fee`.

### Proof of Concept

Choose the following resource bounds (all pass existing validation):

| Field | Value |
|---|---|
| `l1_gas.max_amount` | `2` |
| `l1_gas.max_price_per_unit` | `(P-1)/2` |
| `l2_gas.max_amount` | `1` |
| `l2_gas.max_price_per_unit` | `1` |
| `tip` | `0` |
| `l1_data_gas.max_amount` | `0` |
| `l1_data_gas.max_price_per_unit` | `0` |

Validation checks:
- `assert_nn_le(2, 2^64 - 1)` ✓
- `assert_nn((P-1)/2)` ✓ (it equals `(P-1)/2 < P/2`)
- `assert_nn_le(tip=0, 2^64 - 1)` ✓

`compute_max_possible_fee` computes:

```
A = 2 * (P-1)/2 = P - 1 ≡ -1 (mod P)
B = 1 * (1 + 0) = 1
C = 0 * 0 = 0
max_fee = A + B + C = -1 + 1 + 0 = 0 (mod P)
```

`charge_fee` hits `if (max_fee == 0) { return (); }` and exits without transferring any tokens. The transaction executes with zero fee paid. [4](#0-3)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L86-135)
```text
// Returns the maximum possible fee that can be charged for the transaction.
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

    local low_actual_fee;
    %{ LoadActualFee %}
    local calldata: TransferCallData = TransferCallData(
        recipient=block_context.block_info_for_execute.sequencer_address,
        amount=Uint256(low=low_actual_fee, high=0),
    );

    // Verify that the charged amount is not larger than the transaction's max_fee field.
    assert_nn_le(calldata.amount.low, max_fee);
```
