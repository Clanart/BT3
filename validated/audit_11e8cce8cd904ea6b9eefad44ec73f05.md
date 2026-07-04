### Title
Fee Bypass via Felt Arithmetic Overflow in `compute_max_possible_fee` — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary
Any unprivileged V3 transaction sender can set `tip` to a value that causes `compute_max_possible_fee` to produce `max_fee = 0` via modular field-prime overflow. `charge_fee` then skips the ERC20 fee transfer entirely, allowing the attacker to execute transactions without paying fees — a direct loss of STRK tokens owed to the sequencer.

---

### Finding Description

`compute_max_possible_fee` computes the maximum chargeable fee as:

```
max_fee = l1_gas.max_amount * l1_gas.max_price
        + l2_gas.max_amount * (l2_gas.max_price + tip)
        + l1_data_gas.max_amount * l1_data_gas.max_price
``` [1](#0-0) 

All arithmetic is over the Cairo field (mod P, where P ≈ 2^251 + 17·2^192 + 1). Neither `tip` nor any `max_amount`/`max_price_per_unit` field is range-checked anywhere in the OS before this computation. The `tip` is loaded from a hint in `get_account_tx_common_fields` without any bounds assertion: [2](#0-1) 

An attacker sets `tip = P − l2_gas.max_price_per_unit`, making `l2_gas.max_price + tip ≡ 0 (mod P)`. With `l1_gas.max_amount = 0` and `l1_data_gas.max_amount = 0`, the entire expression evaluates to 0.

`charge_fee` then hits the early-return guard:

```cairo
if (max_fee == 0) {
    return ();
}
``` [3](#0-2) 

and returns without executing the ERC20 transfer. The attacker's transaction still executes normally — they retain L2 gas from `get_initial_user_gas_bound`, which returns `l2_gas.max_amount` — but no STRK is deducted from their account.

<cite repo="Annirich/sequencer--017" path="crates/

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L123-125)
```text
    if (max_fee == 0) {
        return ();
    }
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
