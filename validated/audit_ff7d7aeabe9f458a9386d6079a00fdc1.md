### Title
Felt Arithmetic Overflow in `compute_max_possible_fee` Allows Complete Fee Bypass — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

A user can craft V3 transaction resource bounds such that `compute_max_possible_fee` returns exactly `0` in Cairo's modular felt arithmetic, causing `charge_fee` to silently skip the ERC-20 fee transfer. The sequencer's off-chain (Rust/big-integer) fee computation sees a legitimately large `max_fee` and includes the transaction, while the OS-level Cairo proof enforces zero fee. The result is provably-valid, fee-free execution — a direct loss of funds for the sequencer and, at scale, a path to total network shutdown.

---

### Finding Description

`compute_max_possible_fee` computes the fee ceiling in raw felt arithmetic with no overflow guard:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit
     + l2_gas_bounds.max_amount * (l2_gas_bounds.max_price_per_unit + tx_info.tip)
     + l1_data_gas_bounds.max_amount * l1_data_gas_bounds.max_price_per_unit;
``` [1](#0-0) 

The only range constraints on the inputs come from `pack_resource_bounds`, called during hash computation:

- `max_amount` is bounded to `[0, 2^64 − 1]`
- `max_price_per_unit` is only checked to be non-negative (`assert_nn`), placing it in `[0, (P−1)/2]` [2](#0-1) 

Because `max_amount` can be up to `2^64 − 1` and `max_price_per_unit` up to `(P−1)/2 ≈ 2^250`, their product can reach `≈ 2^314`, far exceeding the Stark prime `P ≈ 2^251`. The sum of three such products is computed modulo `P` with no saturation or overflow check.

`charge_fee` then gates the entire fee transfer on this value:

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);
if (max_fee == 0) {
    return ();
}
``` [3](#0-2) 

If `max_fee ≡ 0 (mod P)`, the ERC-20 transfer is never executed and the sequencer receives nothing.

---

### Proof of Concept

Let `P = 2^251 + 17·2^192 + 1` (the Stark prime). Craft the following resource bounds:

| Field | Value |
|---|---|
| `l1_gas_bounds.max_amount` | `2` |
| `l1_gas_bounds.max_price_per_unit` | `(P − 1) / 2` |
| `l2_gas_bounds.max_amount` | `1` |
| `l2_gas_bounds.max_price_per_unit` | `1` |
| `tip` | `0` |
| `l1_data_gas_bounds.max_amount` | `0` |
| `l1_data_gas_bounds.max_price_per_unit` | `0` |

**Validity checks all pass:**
- `max_amount = 2 ≤ 2^64 − 1` ✓ (`assert_nn_le`)
- `max_price_per_unit = (P−1)/2 < P/2` ✓ (`assert_nn`)
- `tip = 0 ≤ 2^64 − 1` ✓ (`assert_nn_le` in `hash_fee_fields`)

**Felt arithmetic result:**

```
max_fee = 2 · (P−1)/2  +  1 · (1 + 0)  +  0
        = (P − 1)      +  1
        = P
        ≡ 0  (mod P)
```

The OS evaluates `max_fee == 0` as `TRUE` and returns from `charge_fee` without executing the ERC-20 transfer.

**Sequencer discrepancy:** The sequencer's Rust/Python off-chain code computes `max_fee` using arbitrary-precision integers, obtaining `P ≈ 2^251` — a legitimately enormous fee ceiling. It includes the transaction. The Cairo OS proof then enforces zero fee. The block is valid and provable; the sequencer is simply not paid. [4](#0-3) 

---

### Impact Explanation

**Direct loss of funds (Critical).** The sequencer executes real computation (L2 gas, L1 data gas) and receives zero fee. Every transaction crafted this way is provably valid — the proof verifier on L1 accepts it — so the loss is permanent and unrecoverable. At scale, an attacker can flood the network with zero-fee transactions, exhausting sequencer resources without payment, which also maps to **total network shutdown (High)** as the sequencer becomes economically non-viable.

---

### Likelihood Explanation

Any unprivileged transaction sender can exploit this. The required resource-bound values are fixed constants derivable from the public Stark prime. No privileged access, leaked key, or social engineering is needed. The transaction passes all signature and hash checks because the malicious values are committed into the transaction hash and signed by the attacker's own key.

---

### Recommendation

In `compute_max_possible_fee`, add explicit upper-bound range checks on `max_price_per_unit` before performing felt multiplication, mirroring the `max_amount` bound already enforced in `pack_resource_bounds`:

```cairo
assert_nn_le(l1_gas_bounds.max_price_per_unit, MAX_PRICE_PER_UNIT_BOUND);
assert_nn_le(l2_gas_bounds.max_price_per_unit, MAX_PRICE_PER_UNIT_BOUND);
assert_nn_le(l1_data_gas_bounds.max_price_per_unit, MAX_PRICE_PER_UNIT_BOUND);
```

where `MAX_PRICE_PER_UNIT_BOUND` is chosen so that `(2^64 − 1) · MAX_PRICE_PER_UNIT_BOUND · 3 < P`. This ensures the sum of all three products cannot wrap around the field. Alternatively, enforce the bound inside `pack_resource_bounds` (which is already called for every resource entry during hash computation) so a single enforcement point covers both hashing and fee arithmetic. [2](#0-1) [5](#0-4)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L103-108)
```text
func pack_resource_bounds{range_check_ptr}(resource_bounds: ResourceBounds) -> felt {
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
    assert_nn(resource_bounds.max_price_per_unit);
    return (resource_bounds.resource * 2 ** 64 + resource_bounds.max_amount) * 2 ** 128 +
        resource_bounds.max_price_per_unit;
}
```
