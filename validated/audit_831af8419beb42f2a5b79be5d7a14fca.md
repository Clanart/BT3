### Title
Felt Arithmetic Overflow in `compute_max_possible_fee` Enables Fee-Free Transaction Execution — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

The `compute_max_possible_fee` function computes the maximum chargeable fee using felt (modular) arithmetic on user-supplied resource bounds, with no range checks enforcing that the values fit within their intended u64/u128 domains. An attacker can craft resource bound values such that the three-term sum of products wraps to zero modulo the Stark prime, causing `charge_fee` to immediately return without executing the ERC20 fee transfer — allowing full transaction execution at zero cost.

---

### Finding Description

`compute_max_possible_fee` (lines 87–102 of `transaction_impls.cairo`) computes:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit
     + l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip)
     + l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit;
``` [1](#0-0) 

All arithmetic is modular over the Stark prime P ≈ 2²⁵¹ + 17·2¹⁹² + 1. The six resource bound fields (`max_amount`, `max_price_per_unit` for each of L1 gas, L2 gas, L1 data gas) are loaded from the transaction via the hint `%{ LoadCommonTxFields %}` inside `get_account_tx_common_fields` with **no `assert_nn_le` range assertions** enforcing they fit within u64/u128. [2](#0-1) 

These fields are part of the signed transaction: the user freely chooses any felt values for them, and the transaction hash commits to those values. Since P is prime, for any target T there exist non-zero felt pairs (a, b) with a·b ≡ T (mod P). An attacker can therefore choose six non-zero felt values such that the three-term sum ≡ 0 (mod P), making `compute_max_possible_fee` return 0.

In `charge_fee` (lines 111–165):

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);
if (max_fee == 0) {
    return ();
}
``` [3](#0-2) 

When `max_fee == 0`, the function returns immediately — the ERC20 `transfer` call to the sequencer is never made. The transaction's `__execute__` entry point still runs in full.

The structural analog to the external report: just as the auction owner sets `auctionMultiplier`/`auctionDecrement` to drive `tokensNeeded → 0`, here the transaction sender sets resource bounds to drive `max_fee → 0` via modular wrap-around, bypassing payment entirely.

---

### Impact Explanation

**Critical. Direct loss of funds.**

The fee token ERC20 transfer that should occur is entirely skipped. The attacker executes arbitrary V3 transactions (invoke, declare, deploy\_account) without transferring any fee tokens to the sequencer. At scale, an attacker can drain execution capacity from the network at zero cost, and the sequencer/fee recipients permanently lose the fee revenue that should have been collected. Because the OS is the authoritative proven computation, a valid proof can be generated for a block containing such transactions, making the fee bypass provably correct from the protocol's perspective.

---

### Likelihood Explanation

**Medium.**

The attacker controls all six resource bound fields as part of the signed transaction. Finding values that sum to 0 mod P is a straightforward linear algebra problem over F\_p: given any two of the three products K and M, set the third product to −K−M (mod P). The only practical barrier is whether the sequencer's off-chain blockifier validation rejects transactions with out-of-range resource bounds before they reach the OS. However, the OS itself — the authoritative proven computation — contains no such enforcement, creating a protocol-level gap that persists regardless of sequencer-side mitigations.

---

### Recommendation

1. **Add range checks on resource bound fields** immediately after loading them from the hint in `get_account_tx_common_fields`: use `assert_nn_le(max_amount, MAX_U64)` and `assert_nn_le(max_price_per_unit, MAX_U128)` for each resource type.
2. **Assert `max_fee != 0`** for non-system V3 transactions after computing it, or assert that each individual product is within a safe range before summing, to prevent modular wrap-around from producing a spurious zero.
3. **Align OS and blockifier validation**: ensure the blockifier's resource bound constraints are identical to those enforced in the OS so there is no discrepancy between what the sequencer accepts and what the OS enforces.

---

### Proof of Concept

Let P = Stark prime. Choose:

- `l1_gas.max_amount = A`, `l1_gas.max_price_per_unit = B` → A·B ≡ K (mod P)
- `l2_gas.max_amount = C`, `l2_gas.max_price_per_unit = D`, `tip = 0` → C·D ≡ M (mod P)
- `l1_data_gas.max_amount = E`, `l1_data_gas.max_price_per_unit = F` → E·F ≡ P − K − M (mod P)

Then K + M + (P − K − M) ≡ 0 (mod P), so `compute_max_possible_fee` returns 0.

**Steps:**
1. Attacker constructs a V3 invoke transaction with the above resource bounds (all non-zero felt values).
2. Attacker signs the transaction — the hash commits to these resource bound values.
3. Transaction is submitted to the sequencer and included in a block.
4. OS executes `charge_fee`: `max_fee = 0`, returns immediately — no ERC20 transfer occurs.
5. The `__execute__` entry point runs normally; the attacker's contract logic executes at zero fee cost.
6. A valid STARK proof is generated for the block, making the fee bypass provably correct at the protocol level. [4](#0-3)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L111-165)
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
