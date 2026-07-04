### Title
Fee Bypass via Field Arithmetic Overflow in `compute_max_possible_fee` — (File: `execution/transaction_impls.cairo`)

### Summary
The `compute_max_possible_fee` function computes the maximum chargeable fee using unchecked Cairo prime-field arithmetic. An unprivileged transaction sender can craft V3 resource bounds whose products sum to exactly 0 modulo the Stark prime, causing `charge_fee` to skip the ERC-20 transfer entirely and execute the transaction fee-free. This is a direct protocol-level loss of funds.

### Finding Description

`compute_max_possible_fee` in `transaction_impls.cairo` computes:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit
     + l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip)
     + l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit;
``` [1](#0-0) 

All arithmetic in Cairo is modular over the Stark prime `p ≈ 2²⁵¹`. There is **no range check or bounds validation** on any of the six resource-bound field elements before this multiplication. If the true integer sum of the three terms equals `k·p` for any positive integer `k`, the result is `0 mod p`.

`charge_fee` then immediately returns without executing any ERC-20 transfer:

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);
if (max_fee == 0) {
    return ();
}
``` [2](#0-1) 

No other code path in `charge_fee` charges the fee after this early return. The `assert_nn_le(calldata.amount.low, max_fee)` guard is never reached. [3](#0-2) 

The resource bounds are loaded from a sequencer-provided hint but are **part of the signed transaction** (included in the Poseidon transaction hash), so the sequencer cannot alter them. The attacker signs and submits the crafted transaction; the sequencer, using the same field-arithmetic formula, also computes `max_fee = 0` and includes the transaction with `actual_fee = 0`. [4](#0-3) 

The only validation present on resource bounds is the structural assertion `n_resource_bounds = 3` and `version = 3`; no per-field range check exists. [5](#0-4) 

### Impact Explanation

**Direct loss of funds (Critical).** The sequencer receives zero fee for a transaction that consumed real L2 gas and L1 data gas. Because the OS Cairo program is the authoritative verifier whose output is committed on-chain via a STARK proof, a block containing such a transaction is accepted by the L1 verifier. The protocol permanently loses the fee revenue for every such transaction. An attacker can repeat this for every transaction they submit, paying nothing while consuming network resources.

### Likelihood Explanation

The attacker is an ordinary transaction sender (unprivileged). Constructing the overflow is elementary number theory: choose any `l2_gas_bounds.max_amount = G` large enough for execution, set `l2_gas_bounds.max_price_per_unit = P` (any non-zero price), then set `l1_gas_bounds.max_amount = 1` and `l1_gas_bounds.max_price_per_unit = p − G·P − 1`, and `l1_data_gas_bounds.max_amount = 1`, `l1_data_gas_bounds.max_price_per_unit = 1`. The sum is `(p − G·P − 1) + G·P + 1 = p ≡ 0 (mod p)`. The transaction is valid, signable, and the sequencer's identical formula yields the same zero result.

### Recommendation

Before computing the fee product, enforce that each resource-bound field (`max_amount`, `max_price_per_unit`) is a legitimate non-negative integer bounded by a protocol-defined maximum (e.g., `< 2⁶⁴`). Use `assert_nn_le` on each field individually before the multiplication, mirroring how `signature_len` is already range-checked. This prevents the modular wrap-around that produces a spurious zero.

### Proof of Concept

Let `p = 0x800000000000011000000000000000000000000000000000000000000000001` (Stark prime).

1. Attacker picks `G = 10_000_000` (sufficient L2 gas for a typical invoke).
2. Attacker picks `P = 1` (L2 gas price = 1 fri).
3. Attacker sets:
   - `l2_gas_bounds = { max_amount: G, max_price_per_unit: P }` → term₂ = G
   - `l1_gas_bounds = { max_amount: 1, max_price_per_unit: p − G − 1 }` → term₁ = p − G − 1
   - `l1_data_gas_bounds = { max_amount: 1, max_price_per_unit: 1 }` → term₃ = 1
   - `tip = 0`
4. `compute_max_possible_fee` returns `(p − G − 1) + G + 1 = p ≡ 0 (mod p)`.
5. `charge_fee` hits `if (max_fee == 0) { return (); }` and exits — no ERC-20 transfer occurs.
6. The transaction executes fully (G units of L2 gas available via `get_initial_user_gas_bound`), the STARK proof is generated and verified on L1, and the sequencer receives **zero** fee.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L111-125)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L127-165)
```text
    local low_actual_fee;
    %{ LoadActualFee %}
    local calldata: TransferCallData = TransferCallData(
        recipient=block_context.block_info_for_execute.sequencer_address,
        amount=Uint256(low=low_actual_fee, high=0),
    );

    // Verify that the charged amount is not larger than the transaction's max_fee field.
    assert_nn_le(calldata.amount.low, max_fee);

    // TODO(ilya, 01/01/2026): Consider caching the fee_token_class_hash.
    local fee_token_address = block_context.os_global_context.starknet_os_config.fee_token_address;
    let (fee_state_entry: StateEntry*) = dict_read{dict_ptr=contract_state_changes}(
        key=fee_token_address
    );
    let (__fp__, _) = get_fp_and_pc();
    // Use block_info directly from block_context, so that charge_fee will always run in
    // execute-mode rather than validate-mode.
    local execution_context: ExecutionContext = ExecutionContext(
        entry_point_type=ENTRY_POINT_TYPE_EXTERNAL,
        class_hash=fee_state_entry.class_hash,
        calldata_size=TransferCallData.SIZE,
        calldata=&calldata,
        execution_info=new ExecutionInfo(
            block_info=block_context.block_info_for_execute,
            tx_info=tx_info,
            caller_address=tx_info.account_contract_address,
            contract_address=fee_token_address,
            selector=TRANSFER_ENTRY_POINT_SELECTOR,
        ),
        deprecated_tx_info=tx_execution_context.deprecated_tx_info,
    );

    let remaining_gas = DEFAULT_INITIAL_GAS_COST;
    non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
        block_context=block_context, execution_context=&execution_context
    );
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
