### Title
Unchecked Felt Arithmetic Overflow in `compute_max_possible_fee` Allows Complete Fee Bypass — (File: `execution/transaction_impls.cairo`)

---

### Summary

The `compute_max_possible_fee` function performs unchecked felt-field arithmetic on user-controlled `ResourceBounds` values. Because Cairo felt arithmetic is modular (mod the Stark prime P ≈ 2²⁵¹), an attacker can craft resource-bound values whose product-sum wraps to exactly 0 mod P. This causes `charge_fee` to return immediately without collecting any fee, letting the attacker execute transactions at zero cost.

---

### Finding Description

`compute_max_possible_fee` in `transaction_impls.cairo` computes:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit
     + l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip)
     + l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit;
``` [1](#0-0) 

All six operands (`max_amount`, `max_price_per_unit` for each of the three resource types) are raw felt values loaded from the user-supplied transaction fields via the hint `%{ LoadCommonTxFields %}`. No `assert_nn_le` or range-check constraint is applied to any of them before the multiplication. [2](#0-1) 

The only validation performed on the `TxInfo` struct is `assert_deprecated_tx_fields_consistency`, which only checks version-field consistency, not the magnitude of resource-bound values. [3](#0-2) 

In `charge_fee`, the result of `compute_max_possible_fee` is immediately tested for zero:

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);
if (max_fee == 0) {
    return ();
}
``` [4](#0-3) 

If `max_fee == 0`, the function returns without executing the ERC-20 transfer, so the sequencer receives nothing. Even if the early-return branch is not taken, the subsequent constraint `assert_nn_le(calldata.amount.low, max_fee)` forces `low_actual_fee ≤ max_fee`, so a `max_fee` of 0 still mandates a zero-fee transfer. [5](#0-4) 

---

### Impact Explanation

**Direct loss of funds (Critical).** The sequencer is the entity that receives fees. When `max_fee` wraps to 0, the OS skips the fee-transfer call entirely. The sequencer expends L1 proof-generation cost and L2 execution resources for the transaction but receives zero compensation. An attacker who repeatedly submits such transactions drains the sequencer's operational budget without paying anything, which can also degrade sequencer profitability to the point of network shutdown (High).

---

### Likelihood Explanation

**Medium.** Any unprivileged transaction sender controls all six resource-bound felt values. The transaction hash commits to these values, so the attacker must sign them — but that is trivially done by the attacker themselves. The sequencer's off-chain mempool may apply additional heuristic checks, but the OS itself imposes no such constraint, meaning a sequencer that does not independently validate resource-bound magnitudes will include and process the transaction for free. The arithmetic to find a valid overflow tuple is elementary (see PoC).

---

### Recommendation

Add explicit range checks on `max_amount` and `max_price_per_unit` for each resource bound immediately after they are loaded, before `compute_max_possible_fee` is called. For example:

```cairo
assert_nn_le(resource_bounds[i].max_amount, MAX_RESOURCE_AMOUNT);        // e.g. 2^64 - 1
assert_nn_le(resource_bounds[i].max_price_per_unit, MAX_RESOURCE_PRICE); // e.g. 2^128 - 1
```

With both values bounded to at most 2⁶⁴ and 2¹²⁸ respectively, the maximum product per resource is 2¹⁹², and the sum of three such products is at most 3 × 2¹⁹², which is far below the Stark prime (≈ 2²⁵¹), eliminating any possibility of modular wrap-around.

---

### Proof of Concept

Let P = 2²⁵¹ + 17·2¹⁹² + 1 (the Stark field prime).

Choose:
- `l1_gas_bounds.max_amount = 1`, `l1_gas_bounds.max_price_per_unit = P − 1`
- `l2_gas_bounds.max_amount = 1`, `l2_gas_bounds.max_price_per_unit = 1`, `tip = 0`
- `l1_data_gas_bounds.max_amount = 0`, `l1_data_gas_bounds.max_price_per_unit = 0`

Then:

```
compute_max_possible_fee
  = 1·(P−1) + 1·(1+0) + 0·0
  = (P−1) + 1
  = P
  ≡ 0  (mod P)
```

`charge_fee` hits the `if (max_fee == 0) { return (); }` branch and exits without transferring any tokens to the sequencer. The transaction is executed and its state changes are committed, but the sequencer receives zero fee.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L121-125)
```text
    let max_fee = compute_max_possible_fee(tx_info=tx_info);

    if (max_fee == 0) {
        return ();
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L127-135)
```text
    local low_actual_fee;
    %{ LoadActualFee %}
    local calldata: TransferCallData = TransferCallData(
        recipient=block_context.block_info_for_execute.sequencer_address,
        amount=Uint256(low=low_actual_fee, high=0),
    );

    // Verify that the charged amount is not larger than the transaction's max_fee field.
    assert_nn_le(calldata.amount.low, max_fee);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L170-198)
```text
func get_account_tx_common_fields(
    block_context: BlockContext*, tx_hash_prefix: felt, sender_address: felt
) -> CommonTxFields* {
    alloc_locals;
    local resource_bounds: ResourceBounds*;
    local tip;
    local paymaster_data_length;
    local paymaster_data: felt*;
    local nonce_data_availability_mode;
    local fee_data_availability_mode;
    local nonce;
    %{ LoadCommonTxFields %}
    %{ LoadTxNonceAccount %}
    tempvar common_tx_fields = new CommonTxFields(
        tx_hash_prefix=tx_hash_prefix,
        version=3,
        sender_address=sender_address,
        chain_id=block_context.os_global_context.starknet_os_config.chain_id,
        nonce=nonce,
        tip=tip,
        n_resource_bounds=3,
        resource_bounds=resource_bounds,
        paymaster_data_length=paymaster_data_length,
        paymaster_data=paymaster_data,
        nonce_data_availability_mode=nonce_data_availability_mode,
        fee_data_availability_mode=fee_data_availability_mode,
    );
    return common_tx_fields;
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L39-59)
```text
func assert_deprecated_tx_fields_consistency(tx_info: TxInfo*) {
    tempvar version = tx_info.version;
    if (version * (version - 1) * (version - 2) == 0) {
        let nullptr = cast(0, felt*);
        assert tx_info.tip = 0;
        assert tx_info.resource_bounds_start = cast(0, ResourceBounds*);
        assert tx_info.resource_bounds_end = cast(0, ResourceBounds*);
        assert tx_info.paymaster_data_start = nullptr;
        assert tx_info.paymaster_data_end = nullptr;
        assert tx_info.nonce_data_availability_mode = 0;
        assert tx_info.fee_data_availability_mode = 0;
        assert tx_info.account_deployment_data_start = nullptr;
        assert tx_info.account_deployment_data_end = nullptr;
    } else {
        with_attr error_message("Invalid transaction version: {version}.") {
            assert version = 3;
        }
        assert tx_info.max_fee = 0;
    }
    return ();
}
```
