### Title
Field Arithmetic Overflow in `compute_max_possible_fee` Enables Complete Fee Bypass — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

The `compute_max_possible_fee` function in `transaction_impls.cairo` performs unchecked field arithmetic (mod STARK prime P) on user-controlled resource bound values. Because no range constraints are enforced on these felt-typed inputs in the OS Cairo code, an attacker can craft a v3 transaction whose resource bounds cause the fee sum to wrap to exactly 0 mod P. When `compute_max_possible_fee` returns 0, `charge_fee` exits immediately without executing the ERC-20 transfer, meaning the transaction is processed with zero fee charged.

---

### Finding Description

`compute_max_possible_fee` computes:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit
     + l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip)
     + l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit;
``` [1](#0-0) 

All six operands (`max_amount`, `max_price_per_unit`, `tip`) are felt values loaded from the transaction via hints, with no `assert_nn_le` or range-check constraint applied to them anywhere in the OS Cairo code before this multiplication. [2](#0-1) 

The caller, `charge_fee`, immediately returns if the result is 0:

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);
if (max_fee == 0) {
    return ();
}
``` [3](#0-2) 

The ERC-20 `transfer` call that actually debits the fee token is never reached, so no fee is deducted from the sender's balance and no payment is made to the sequencer. [4](#0-3) 

The resource bounds are loaded from hints without any bounds enforcement in `get_account_tx_common_fields`: [5](#0-4) 

`fill_account_tx_info` and `assert_deprecated_tx_fields_consistency` perform no range checks on the individual resource bound fields: [6](#0-5) 

---

### Impact Explanation

**Direct loss of funds (Critical).** The fee token balance of the attacker is never decremented. The sequencer receives no payment. Because the OS Cairo program is the authoritative state-transition function whose execution is proven on-chain, a proof generated from a block containing such a transaction is valid — the L1 verifier accepts it — yet the fee transfer never occurred. This constitutes a direct, provable loss of fee revenue from the fee token pool and the sequencer.

---

### Likelihood Explanation

Any v3 transaction sender can set `max_amount` and `max_price_per_unit` to arbitrary felt values. The OS Cairo code imposes no upper-bound constraint on these fields. Constructing a set of values whose product-sum wraps to 0 mod P is straightforward arithmetic (see PoC below). The only external gate is the sequencer's off-chain mempool validation; however, the OS itself — the code that generates the proof — does not enforce this invariant, making the protocol-level guarantee absent.

---

### Recommendation

1. **Add range checks on resource bound fields.** Before `compute_max_possible_fee` is called, assert that each `max_amount` fits in u64 and each `max_price_per_unit` fits in u128 using `assert_nn_le`. With u64 × u128 products, the maximum sum of three terms is `3 × (2^64−1) × (2^128−1) ≈ 2^193.6`, which is safely below the STARK prime (~2^251), making overflow impossible.

2. **Assert non-decreasing fee token balance.** After `charge_fee`, assert that the fee token balance of the sender has decreased by at least the actual fee, analogous to the "invariant non-decreasing" recommendation in the reference report.

---

### Proof of Concept

Let P = 3618502788666131213697322783095070105623107215331596699973092056135872020481 (STARK prime).

Set the following resource bounds in a v3 transaction:

| Field | Value |
|---|---|
| `l1_gas_bounds.max_amount` | 1 |
| `l1_gas_bounds.max_price_per_unit` | P − 1 |
| `l2_gas_bounds.max_amount` | 1 |
| `l2_gas_bounds.max_price_per_unit` | 1 |
| `tip` | 0 |
| `l1_data_gas_bounds.max_amount` | 0 |

Computation:

```
A = 1 × (P−1) = P−1
B = 1 × (1 + 0) = 1
C = 0 × anything = 0
A + B + C = P ≡ 0 (mod P)
```

`compute_max_possible_fee` returns 0. `charge_fee` returns at the `if (max_fee == 0)` branch. The transaction executes fully — including any state changes — with zero fee charged.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L127-164)
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L203-243)
```text
func fill_account_tx_info{range_check_ptr}(
    transaction_hash: felt,
    common_tx_fields: CommonTxFields*,
    account_deployment_data_size: felt,
    account_deployment_data: felt*,
    proof_facts_size: felt,
    proof_facts: felt*,
    tx_info_dst: TxInfo*,
    deprecated_tx_info_dst: DeprecatedTxInfo*,
) {
    alloc_locals;

    local signature_start: felt*;
    local signature_len: felt;
    %{ GenSignatureArg %}
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
}
```
