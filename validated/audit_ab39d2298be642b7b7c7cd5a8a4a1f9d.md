### Title
Fee Bypass via Felt Arithmetic Overflow in `compute_max_possible_fee` — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

The `compute_max_possible_fee` function computes the maximum chargeable fee using Cairo felt arithmetic (modular arithmetic mod the Stark prime P ≈ 2²⁵¹) without range-checking the user-supplied resource bounds values. A transaction sender can craft `max_amount` and `max_price_per_unit` values such that the sum of products wraps to 0 mod P. The `charge_fee` function unconditionally skips fee charging when `max_fee == 0`, allowing the transaction to execute for free.

---

### Finding Description

In `transaction_impls.cairo`, `compute_max_possible_fee` (lines 87–102) computes:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
    (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
    l1_data_gas_bounds.max_price_per_unit;
``` [1](#0-0) 

All arithmetic is over the Cairo field (mod P). The OS does **not** apply any `assert_nn_le` or range-check constraints to the individual `max_amount` or `max_price_per_unit` fields of the `ResourceBounds` struct before this computation. The resource bounds are loaded directly from a hint with no bounds enforcement:

```cairo
local resource_bounds: ResourceBounds*;
...
%{ LoadCommonTxFields %}
``` [2](#0-1) 

The `charge_fee` function then performs:

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);

if (max_fee == 0) {
    return ();
}
``` [3](#0-2) 

When `max_fee == 0`, the function returns immediately — no ERC20 transfer is executed, no fee is charged, and the transaction runs for free.

**Concrete overflow example:**

| Field | Value |
|---|---|
| `l1_gas_bounds.max_amount` | `1` |
| `l1_gas_bounds.max_price_per_unit` | `P − 1` (≡ −1 mod P) |
| `l2_gas_bounds.max_amount` | `1` |
| `l2_gas_bounds.max_price_per_unit` | `1` |
| `tip` | `0` |
| `l1_data_gas_bounds.max_amount` | `0` |

Computation: `(1 × (P−1)) + (1 × (1+0)) + 0 = P−1+1 = P ≡ 0 (mod P)`

Result: `max_fee = 0` → fee charging is skipped entirely.

The transaction hash is computed from these resource bounds values and signed by the user, making the transaction cryptographically valid from the OS's perspective. [4](#0-3) 

---

### Impact Explanation

**Direct loss of funds (Critical).** The sequencer receives no fee payment for executing the transaction. The ERC20 `transfer` call in `charge_fee` is never reached. At scale, this enables any user to execute arbitrary transactions for free, draining sequencer revenue and enabling unbounded transaction spam. The latter maps directly to **network not being able to confirm new transactions** (High), as free spam saturates block capacity.

---

### Likelihood Explanation

The attacker is the transaction sender. They:
1. Choose resource bounds values that cause the felt overflow.
2. Sign the transaction hash (which commits to these values) — the signature is valid.
3. Submit to any sequencer that does not independently validate resource bounds at the API layer (the OS itself is the authoritative validator in the proof system).

The OS is the final arbiter of transaction validity in the STARK proof. If the OS accepts the transaction without charging fees, the resulting proof is valid and the L1 verifier accepts the block. No privileged access is required — only the ability to submit a signed transaction.

---

### Recommendation

Before calling `compute_max_possible_fee`, add explicit range checks on each resource bounds field to enforce that values are within their intended types (e.g., `max_amount ≤ 2⁶⁴ − 1`, `max_price_per_unit ≤ 2¹²⁸ − 1`). Use `assert_nn_le` for each field:

```cairo
assert_nn_le(l1_gas_bounds.max_amount, MAX_AMOUNT_BOUND);
assert_nn_le(l1_gas_bounds.max_price_per_unit, MAX_PRICE_BOUND);
// ... repeat for l2 and l1_data bounds
```

This ensures the products cannot overflow the field and that `max_fee` accurately reflects the user's stated willingness to pay.

---

### Proof of Concept

1. Attacker constructs a V3 invoke transaction with:
   - `l1_gas_bounds = {max_amount: 1, max_price_per_unit: P−1}`
   - `l2_gas_bounds = {max_amount: 1, max_price_per_unit: 1}`
   - `tip = 0`
   - `l1_data_gas_bounds = {max_amount: 0, max_price_per_unit: 0}`
2. Attacker signs the Poseidon hash of this transaction (computed by `compute_invoke_transaction_hash`). The hash commits to these exact resource bounds.
3. Transaction is submitted to a sequencer and included in a block.
4. OS executes `compute_max_possible_fee`:
   - `(1 × (P−1)) + (1 × (1+0)) + (0 × 0) = P ≡ 0 (mod P)`
5. `charge_fee` evaluates `if (max_fee == 0) { return (); }` — returns immediately.
6. No ERC20 transfer occurs. The transaction's `__execute__` runs to completion with zero fee paid.
7. The STARK proof is valid; the L1 verifier accepts the block. [5](#0-4)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L174-181)
```text
    local resource_bounds: ResourceBounds*;
    local tip;
    local paymaster_data_length;
    local paymaster_data: felt*;
    local nonce_data_availability_mode;
    local fee_data_availability_mode;
    local nonce;
    %{ LoadCommonTxFields %}
```
