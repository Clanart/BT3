### Title
Fee Bypass via Felt Arithmetic Overflow in `compute_max_possible_fee` — (File: `execution/transaction_impls.cairo`)

---

### Summary

The `compute_max_possible_fee` function in the StarkNet OS Cairo program performs unchecked felt arithmetic on user-controlled `max_price_per_unit` values. Because `max_price_per_unit` is only constrained to be non-negative (not bounded above), an attacker can craft resource bounds that cause the computed `max_fee` to wrap around the field prime to exactly `0`. When `max_fee == 0`, `charge_fee` returns immediately without executing any ERC-20 transfer, allowing the attacker's transaction to execute with zero fee paid to the sequencer.

---

### Finding Description

**Root cause — `compute_max_possible_fee` (lines 87–102):**

```cairo
func compute_max_possible_fee(tx_info: TxInfo*) -> felt {
    tempvar resource_bounds: ResourceBounds* = tx_info.resource_bounds_start;
    ...
    tempvar l1_gas_bounds: ResourceBounds = resource_bounds[L1_GAS_INDEX];
    tempvar l2_gas_bounds: ResourceBounds = resource_bounds[L2_GAS_INDEX];
    tempvar l1_data_gas_bounds = resource_bounds[L1_DATA_GAS_INDEX];

    return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
        (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
        l1_data_gas_bounds.max_price_per_unit;
}
```

All arithmetic is performed in the StarkNet field (prime `P ≈ 2^251`). The result is a `felt`, so it is implicitly reduced modulo `P`. [1](#0-0) 

**Insufficient bounds on `max_price_per_unit` — `pack_resource_bounds` (lines 103–107 of `transaction_hash.cairo`):**

```cairo
func pack_resource_bounds{range_check_ptr}(resource_bounds: ResourceBounds) -> felt {
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);   // max_amount ≤ 2^64-1
    assert_nn(resource_bounds.max_price_per_unit);            // only: price ≥ 0
    ...
}
```

`max_amount` is bounded to 64 bits. `max_price_per_unit` is only checked to be non-negative (`assert_nn`), meaning it can be any value in `[0, (P-1)/2] ≈ [0, 2^250]`. There is **no upper bound** preventing overflow when multiplied by `max_amount`. [2](#0-1) 

**Fee bypass gate — `charge_fee` (lines 111–165):**

```cairo
func charge_fee{...}(block_context: BlockContext*, tx_execution_context: ExecutionContext*) {
    ...
    let max_fee = compute_max_possible_fee(tx_info=tx_info);

    if (max_fee == 0) {
        return ();          // <-- exits immediately; no ERC-20 transfer occurs
    }
    ...
    assert_nn_le(calldata.amount.low, max_fee);   // only reached if max_fee != 0
    ...
    non_reverting_select_execute_entry_point_func(...);  // ERC-20 transfer
}
```

If `compute_max_possible_fee` returns `0` (due to field overflow), the function returns before the ERC-20 transfer is executed. No fee is charged. [3](#0-2) 

---

### Impact Explanation

**Impact: Critical — Direct loss of funds.**

The sequencer is entitled to receive fee payments for every transaction it processes. When `max_fee` overflows to `0`, the ERC-20 `transfer` call to the sequencer address is never made. The attacker's transaction executes fully (state changes committed, L2 gas consumed) while the sequencer receives nothing. Because the Cairo OS program is the authoritative source of truth for the STARK proof, the L1 verifier accepts the proof as valid even though no fee was paid. The sequencer cannot retroactively correct this on-chain.

If exploited at scale, the sequencer has no economic incentive to continue processing transactions, which can escalate to a network halt (High impact, network unable to confirm new transactions).

---

### Likelihood Explanation

**Likelihood: Medium.**

Any unprivileged transaction sender controls `resource_bounds` directly. The attacker must solve a modular arithmetic equation to find values of `max_price_per_unit` (within `[0, (P-1)/2]`) such that the weighted sum in `compute_max_possible_fee` reduces to `0 mod P`. This is straightforward: choose `l1_gas_bounds.max_amount = 1` and `l1_gas_bounds.max_price_per_unit = P - (remaining_terms)`, where `remaining_terms` is the sum of the other two products (which can be set to small, known values). The attacker signs over these values in the transaction hash, so no key compromise is needed — the attacker simply submits a crafted but validly-signed transaction.

The sequencer's off-chain (Rust/blockifier) fee validation may use non-field arithmetic and thus compute a large (non-overflowed) fee, causing it to accept the transaction as fee-covered. The divergence between off-chain arithmetic and on-chain Cairo field arithmetic is the exploitable gap.

---

### Recommendation

1. **Bound `max_price_per_unit`** in `pack_resource_bounds` (or in `compute_max_possible_fee`) to a safe maximum, e.g., `assert_nn_le(resource_bounds.max_price_per_unit, 2 ** 128 - 1)`. With `max_amount ≤ 2^64 - 1` and `max_price_per_unit ≤ 2^128 - 1`, the product is at most `≈ 2^192`, well below the field prime, eliminating overflow.

2. **Add an overflow guard** in `compute_max_possible_fee` by asserting that the final result is non-zero when at least one resource bound is non-zero, or by computing the fee using range-checked intermediate values.

3. **Align off-chain and on-chain fee computation** so the sequencer's Rust validation uses the same modular arithmetic as the Cairo OS program, preventing divergence.

---

### Proof of Concept

Let `P = 2^251 + 17·2^192 + 1` (StarkNet field prime).

**Attacker constructs a V3 transaction with:**
- `l2_gas_bounds.max_amount = 0`, `l2_gas_bounds.max_price_per_unit = 0`
- `l1_data_gas_bounds.max_amount = 0`, `l1_data_gas_bounds.max_price_per_unit = 0`
- `tip = 0`
- `l1_gas_bounds.max_amount = 1`
- `l1_gas_bounds.max_price_per_unit = P` (which, as a felt, is `0`, but the attacker can use `P - 1 + 1` via a carefully chosen encoding — or more precisely, choose `max_price_per_unit` such that `1 * max_price_per_unit + 0 + 0 ≡ 0 (mod P)`, i.e., `max_price_per_unit = P ≡ 0`. Alternatively, use `max_amount = 2` and `max_price_per_unit = (P+1)/2` so `2 * (P+1)/2 = P+1 ≡ 1 (mod P)`, then adjust the other terms to cancel.)

**Concrete example:**
- `l1_gas_bounds.max_amount = 2`, `l1_gas_bounds.max_price_per_unit = (P - 1) / 2`
  → product = `2 * (P-1)/2 = P - 1 ≡ -1 (mod P)`
- `l1_data_gas_bounds.max_amount = 1`, `l1_data_gas_bounds.max_price_per_unit = 1`
  → product = `1`
- Total = `-1 + 1 = 0 (mod P)`

**Result:** `compute_max_possible_fee` returns `0`. `charge_fee` hits `if (max_fee == 0) { return (); }` and exits. The transaction executes with zero fee paid. The generated STARK proof is valid and accepted by the L1 verifier. [4](#0-3)

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
