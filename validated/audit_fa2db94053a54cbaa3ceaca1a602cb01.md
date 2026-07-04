### Title
Fee Bypass via Stark Field Arithmetic Overflow in `compute_max_possible_fee` — (`execution/transaction_impls.cairo`)

---

### Summary

The `compute_max_possible_fee` function in `transaction_impls.cairo` computes the maximum chargeable fee using unchecked Stark field arithmetic. Because all operands (`max_amount`, `max_price_per_unit`, `tip`) are user-controlled `felt` values with no range constraints enforced by the OS, an attacker can craft resource bounds whose products sum to exactly `0 (mod p)`. This causes `charge_fee` to return immediately without transferring any fee, allowing arbitrary transaction execution at zero cost.

---

### Finding Description

`compute_max_possible_fee` computes:

```
max_fee = l1_gas.max_amount * l1_gas.max_price_per_unit
        + l2_gas.max_amount * (l2_gas.max_price_per_unit + tip)
        + l1_data_gas.max_amount * l1_data_gas.max_price_per_unit
```

All six operands are `felt` values sourced directly from the user-supplied transaction fields and loaded into `TxInfo` via the hint `%{ LoadCommonTxFields %}` with no OS-level range checks applied to them. [1](#0-0) 

The result is a single `felt` computed entirely in the Stark prime field (`p = 2²⁵¹ + 17·2¹⁹² + 1`). If the attacker chooses values such that the sum of products is congruent to `0 mod p`, the function returns `0`. [2](#0-1) 

`charge_fee` then immediately returns without executing the ERC-20 transfer:

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);
if (max_fee == 0) {
    return ();
}
``` [3](#0-2) 

The only constraint the OS enforces on the actual fee is `assert_nn_le(calldata.amount.low, max_fee)`, which is never reached when `max_fee == 0`. [4](#0-3) 

The resource bounds are loaded from hints without any OS-enforced upper-bound range checks on `max_amount` or `max_price_per_unit`: [5](#0-4) 

The only size checks present in `fill_account_tx_info` apply to `signature_len` and `calldata_size`, not to the resource bound field values themselves: [6](#0-5) 

This affects all three account transaction types: `execute_invoke_function_transaction`, `execute_deploy_account_transaction`, and `execute_declare_transaction`, all of which call `charge_fee` after execution. [7](#0-6) [8](#0-7) [9](#0-8) 

---

### Impact Explanation

**Critical — Direct loss of funds.**

An attacker who executes transactions with zero fee:

1. Pays nothing for arbitrary contract execution, including calls to transfer tokens out of contracts they control.
2. Can drain the fee token contract indirectly: by executing free `__execute__` calls that invoke token transfers, the attacker moves funds without the protocol receiving any fee compensation.
3. Can flood the network with zero-cost transactions, since the OS proof remains valid and L1 verification accepts it — this also satisfies the High / network-shutdown impact class.

The OS-generated proof is cryptographically valid in all cases; L1 verifiers cannot distinguish a legitimately fee-exempt transaction from an overflow-crafted one.

---

### Likelihood Explanation

**Medium-High.**

- The attacker controls all six operands of the fee sum through the transaction's `resource_bounds` array and `tip` field, which are user-signed data.
- Constructing a zero-sum is elementary: e.g., set `l1_gas = (1, p−1)`, `l2_gas = (1, 1)`, `tip = 0`, `l1_data_gas = (0, 0)` → `1·(p−1) + 1·(1+0) + 0 = p ≡ 0 (mod p)`.
- The transaction hash commits to these values, so the attacker signs the crafted transaction — no key theft or privileged access is required.
- The only external barrier is sequencer mempool policy, which is not enforced by the OS and can be bypassed by a sequencer that is either compromised, permissive, or operated by the attacker themselves.

---

### Recommendation

1. **Range-check all resource bound fields** (`max_amount`, `max_price_per_unit`) to `[0, 2¹²⁸)` using `assert_nn_le` before they are used in fee arithmetic. This prevents field-element values near `p` from participating in the multiplication.
2. **Validate the intermediate products** are within safe bounds before summing, or restructure `compute_max_possible_fee` to use `Uint256` arithmetic so overflow is explicit and detectable.
3. **Reject `max_fee == 0` for non-free transactions** at the OS level rather than silently skipping fee charging, so that a zero result from overflow is treated as an error rather than a legitimate fee waiver.

---

### Proof of Concept

```
Stark prime: p = 3618502788666131213697322783095070105623107215331596699973092056135872020481

Craft resource bounds:
  l1_gas.max_amount        = 1
  l1_gas.max_price_per_unit = p - 1   (= -1 in the field)
  l2_gas.max_amount        = 1
  l2_gas.max_price_per_unit = 1
  tip                      = 0
  l1_data_gas.max_amount   = 0
  l1_data_gas.max_price_per_unit = 0

compute_max_possible_fee:
  = 1*(p-1) + 1*(1+0) + 0*0
  = (p-1) + 1
  = p
  ≡ 0 (mod p)

charge_fee:
  max_fee = 0  →  early return, no ERC-20 transfer executed.

Result:
  Transaction executes fully (validate + execute entry points run).
  No fee is deducted from the sender.
  OS proof is valid; L1 accepts the block.
```

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L134-135)
```text
    // Verify that the charged amount is not larger than the transaction's max_fee field.
    assert_nn_le(calldata.amount.low, max_fee);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L174-197)
```text
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L218-242)
```text
    assert_nn_le(signature_len, SIERRA_ARRAY_LEN_BOUND - 1);
    assert [tx_info_dst] = TxInfo(
        version=common_tx_fields.version,
        account_contract_address=common_tx_fields.sender_address,
        max_fee=0,
        signature_start=signature_start,
        signature_end=&signature_start[signature_len],
        transaction_hash=transaction_hash,
        chain_id=common_tx_fields.chain_id,
        nonce=common_tx_fields.nonce,
        resource_bounds_start=common_tx_fields.resource_bounds,
        resource_bounds_end=&common_tx_fields.resource_bounds[common_tx_fields.n_resource_bounds],
        tip=common_tx_fields.tip,
        paymaster_data_start=common_tx_fields.paymaster_data,
        paymaster_data_end=&common_tx_fields.paymaster_data[common_tx_fields.paymaster_data_length],
        nonce_data_availability_mode=common_tx_fields.nonce_data_availability_mode,
        fee_data_availability_mode=common_tx_fields.fee_data_availability_mode,
        account_deployment_data_start=account_deployment_data,
        account_deployment_data_end=&account_deployment_data[account_deployment_data_size],
        proof_facts_start=proof_facts,
        proof_facts_end=&proof_facts[proof_facts_size],
    );
    fill_deprecated_tx_info(tx_info=tx_info_dst, dst=deprecated_tx_info_dst);
    assert_deprecated_tx_fields_consistency(tx_info=tx_info_dst);
    return ();
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
