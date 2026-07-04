### Title
Felt Arithmetic Overflow in `compute_max_possible_fee` Enables Complete Fee Bypass — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

The `compute_max_possible_fee` function performs unchecked felt (modular) arithmetic on user-supplied `ResourceBounds` fields. Because Cairo felt arithmetic is modular (mod the Stark prime P ≈ 2²⁵¹), an unprivileged transaction sender can craft resource bounds whose products sum to exactly 0 mod P, causing `max_fee = 0` and causing `charge_fee` to return immediately without charging any fee.

---

### Finding Description

`compute_max_possible_fee` in `transaction_impls.cairo` computes:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
    (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
    l1_data_gas_bounds.max_price_per_unit;
``` [1](#0-0) 

All arithmetic is in the felt field. There are **no range checks** (`assert_nn_le` or equivalent) on `max_amount` or `max_price_per_unit` before this multiplication. Any felt value in `[0, P-1]` is accepted.

The result is used immediately in `charge_fee`:

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);
if (max_fee == 0) {
    return ();
}
``` [2](#0-1) 

If `max_fee == 0`, the function returns immediately — **no ERC-20 transfer is executed, no fee is charged, and no further validation occurs**.

The `ResourceBounds` values originate from the transaction itself, loaded via hint `%{ LoadCommonTxFields %}` and committed to in the transaction hash: [3](#0-2) 

The OS never validates that `max_amount` or `max_price_per_unit` fit within a safe integer range (e.g., u64 or u128) before performing the multiplication. The only size check present in the function is `assert n_resource_bounds = 3`, which does not constrain field values.

---

### Impact Explanation

**Critical — Direct loss of funds.**

If `max_fee` wraps to 0, the sequencer receives zero fee for executing the transaction. An attacker who can get such a transaction included in a block executes arbitrary contract logic for free. In a decentralized sequencer environment (the direction StarkNet is heading), any sequencer that does not independently validate resource bounds off-chain would produce a valid OS proof for a zero-fee transaction. This enables:

1. **Direct loss of sequencer revenue** — the fee transfer is skipped entirely.
2. **Unbounded spam** — an attacker can flood the network with computationally expensive transactions at zero cost, preventing legitimate transactions from being confirmed (network shutdown).

---

### Likelihood Explanation

**Medium.** The attack path is:

1. The attacker crafts a V3 transaction with `ResourceBounds` values chosen so that the felt sum of products ≡ 0 (mod P).
2. The transaction is signed (the hash commits to these values, but the account's `__validate__` does not check resource bound magnitudes).
3. The transaction is submitted to a sequencer. A sequencer that does not independently validate resource bound magnitudes (or a future decentralized sequencer with different validation rules) includes it.
4. The OS computes `max_fee = 0` and skips fee charging.
5. A valid STARK proof is produced for a fee-free execution.

The current centralized sequencer's gateway may reject extreme values off-chain, but **the OS itself — the only component whose correctness is enforced by the on-chain verifier — provides no such guarantee**. As StarkNet decentralizes, this gap becomes directly exploitable by any sequencer node.

---

### Recommendation

Add explicit range checks on each `ResourceBounds` field before performing arithmetic in `compute_max_possible_fee`. For example:

```cairo
func compute_max_possible_fee(tx_info: TxInfo*) -> felt {
    ...
    // Validate that individual fields fit within u64 to prevent felt overflow.
    assert_nn_le(l1_gas_bounds.max_amount, MAX_RESOURCE_BOUND);
    assert_nn_le(l1_gas_bounds.max_price_per_unit, MAX_RESOURCE_BOUND);
    assert_nn_le(l2_gas_bounds.max_amount, MAX_RESOURCE_BOUND);
    assert_nn_le(l2_gas_bounds.max_price_per_unit, MAX_RESOURCE_BOUND);
    assert_nn_le(l1_data_gas_bounds.max_amount, MAX_RESOURCE_BOUND);
    assert_nn_le(l1_data_gas_bounds.max_price_per_unit, MAX_RESOURCE_BOUND);
    assert_nn_le(tx_info.tip, MAX_RESOURCE_BOUND);
    ...
}
```

Where `MAX_RESOURCE_BOUND` is a constant such as `2^64 - 1`. With all inputs bounded to u64, the maximum possible sum is `3 * (2^64)^2 < 2^129`, which is well below P and cannot overflow.

---

### Proof of Concept

Let P = Stark prime. Choose:

| Field | Value |
|---|---|
| `l1_gas_bounds.max_amount` | `P − 1` |
| `l1_gas_bounds.max_price_per_unit` | `1` |
| `l2_gas_bounds.max_amount` | `1` |
| `l2_gas_bounds.max_price_per_unit` | `1` |
| `tip` | `0` |
| `l1_data_gas_bounds.max_amount` | `0` |
| `l1_data_gas_bounds.max_price_per_unit` | `0` |

Computation:

```
max_fee = (P−1)·1 + 1·(1+0) + 0·0
        = (P−1) + 1 + 0
        = P
        ≡ 0  (mod P)
```

Result: `max_fee = 0`. The branch `if (max_fee == 0) { return (); }` fires, and `charge_fee` exits without executing the ERC-20 transfer. The transaction runs for free, and a valid proof is produced. [4](#0-3)

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
