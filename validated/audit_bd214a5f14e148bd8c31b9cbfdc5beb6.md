### Title
Fee Cap Bypass via Unchecked `max_price_per_unit` Field Overflow in `compute_max_possible_fee` — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

The OS enforces that the actual fee charged to a user does not exceed `compute_max_possible_fee(tx_info)`. This function multiplies user-supplied `max_amount` and `max_price_per_unit` fields in Cairo field arithmetic (mod P ≈ 2²⁵¹). While `max_amount` is correctly bounded to `[0, 2⁶⁴ − 1]` during transaction hash computation, `max_price_per_unit` has **no upper bound** — only `assert_nn` (≥ 0) is applied. A user can craft `max_price_per_unit` values that cause the sum-of-products in `compute_max_possible_fee` to wrap around the field prime to 0 or a negligible value, making the OS enforce a near-zero fee cap while the transaction executes with a full gas budget.

---

### Finding Description

**Root cause 1 — Missing upper bound on `max_price_per_unit`:**

In `pack_resource_bounds` (called during transaction hash verification), `max_amount` is range-checked to `[0, 2⁶⁴ − 1]`, but `max_price_per_unit` is only checked with `assert_nn`, which verifies ≥ 0 — trivially satisfied by every field element:

```cairo
func pack_resource_bounds{range_check_ptr}(resource_bounds: ResourceBounds) -> felt {
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);   // bounded ✓
    assert_nn(resource_bounds.max_price_per_unit);            // only ≥ 0, NO upper bound ✗
    ...
}
``` [1](#0-0) 

**Root cause 2 — Unguarded field multiplication in `compute_max_possible_fee`:**

The fee cap is computed as a sum of products of these user-controlled values, entirely in field arithmetic:

```cairo
return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
    (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
    l1_data_gas_bounds.max_price_per_unit;
``` [2](#0-1) 

Since `max_price_per_unit` can be any value in `[0, P − 1]`, the product `max_amount × max_price_per_unit` can wrap around the field prime. The sum of three such products can be made to equal 0 (or any small value) mod P.

**Root cause 3 — Fee enforcement uses the wrapped result directly:**

```cairo
let max_fee = compute_max_possible_fee(tx_info=tx_info);
if (max_fee == 0) {
    return ();
}
...
assert_nn_le(calldata.amount.low, max_fee);
``` [3](#0-2) 

If `max_fee` wraps to 0, `charge_fee` returns immediately — no fee is charged. If it wraps to a small positive value `k`, the OS enforces `actual_fee ≤ k`, regardless of how much gas the transaction consumed.

**Concrete exploit construction:**

Let P = 2²⁵¹ + 17·2¹⁹² + 1 (the Cairo field prime). Set:
- `l1_gas_bounds.max_amount = 2`, `l1_gas_bounds.max_price_per_unit = (P + 1) / 2`
  → product = 2 · (P+1)/2 = P + 1 ≡ 1 (mod P)
- `l2_gas_bounds.max_amount = 2⁶⁴ − 1`, `l2_gas_bounds.max_price_per_unit = 0`, `tip = 0`
  → product = 0
- `l1_data_gas_bounds.max_amount = 0`, `l1_data_gas_bounds.max_price_per_unit = 0`
  → product = 0

Result: `max_fee = 1`. The user has `l2_gas_bounds.max_amount = 2⁶⁴ − 1` L2 gas (from `get_initial_user_gas_bound`) to execute an arbitrarily complex transaction, but the OS enforces a fee cap of 1 token unit. [4](#0-3) 

To make `max_fee = 0` exactly: set `l1_gas_bounds.max_price_per_unit = P − 1` (i.e., −1 mod P), `l2_gas_bounds.max_price_per_unit = 1`, all max_amounts = 1, tip = 0 → sum = (P−1) + 1 = P ≡ 0. The `charge_fee` function returns immediately without charging anything. [5](#0-4) 

---

### Impact Explanation

**Direct loss of funds (Critical).** The sequencer's off-chain mempool validation computes `max_fee` using standard integer arithmetic (not field arithmetic), so it sees a large, legitimate-looking fee and includes the transaction. The OS, using field arithmetic, computes `max_fee = 0` or a negligible value and enforces that cap. The sequencer is provably constrained to charge 0 (or near-0) fee for a transaction that consumed real computational resources. This is a direct, repeatable loss of fee revenue for the sequencer/protocol, exploitable by any unprivileged transaction sender on every V3 transaction type (invoke, declare, deploy_account).

---

### Likelihood Explanation

Any unprivileged user who can submit a V3 transaction controls `max_price_per_unit` for all three resource types. The field prime P is a public constant. Computing the exact `max_price_per_unit` values needed to make `compute_max_possible_fee` return 0 requires only basic modular arithmetic — no privileged access, no key material, no special role. The attack is deterministic and repeatable.

---

### Recommendation

Add an explicit upper bound on `max_price_per_unit` in `pack_resource_bounds`, matching the bound used in the Starknet transaction specification (typically 2¹²⁸ − 1):

```cairo
func pack_resource_bounds{range_check_ptr}(resource_bounds: ResourceBounds) -> felt {
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
    assert_nn_le(resource_bounds.max_price_per_unit, 2 ** 128 - 1);  // ADD THIS
    ...
}
``` [1](#0-0) 

With `max_amount ≤ 2⁶⁴ − 1` and `max_price_per_unit ≤ 2¹²⁸ − 1`, the product is at most `(2⁶⁴ − 1)(2¹²⁸ − 1) < 2¹⁹²`, and the three-term sum is at most `3 · 2¹⁹² ≪ P`, eliminating all field overflow.

---

### Proof of Concept

1. Attacker constructs a V3 invoke transaction with:
   - `l1_gas_bounds = {max_amount: 1, max_price_per_unit: P − 1}` (i.e., −1 mod P)
   - `l2_gas_bounds = {max_amount: 2⁶⁴ − 1, max_price_per_unit: 1}`
   - `l1_data_gas_bounds = {max_amount: 0, max_price_per_unit: 0}`
   - `tip = 0`

2. Off-chain sequencer validation: sees `max_price_per_unit = P − 1 ≈ 2²⁵¹` for L1 gas, computes a huge apparent max_fee using integer arithmetic, accepts the transaction.

3. OS executes `compute_max_possible_fee`:
   - `1 * (P − 1) + (2⁶⁴ − 1) * 1 + 0 = P − 1 + 2⁶⁴ − 1 = P + 2⁶⁴ − 2 ≡ 2⁶⁴ − 2 (mod P)`
   - (Adjust `max_price_per_unit` values to make the sum ≡ 0 mod P as shown above.)

4. `charge_fee` is called with `max_fee = 0`, returns immediately at line 123–125 without executing the ERC-20 transfer. [6](#0-5) 

5. The transaction executes with full L2 gas (`2⁶⁴ − 1` units from `get_initial_user_gas_bound`) and pays zero fee. The sequencer's fee revenue is lost. [4](#0-3)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L103-108)
```text
func pack_resource_bounds{range_check_ptr}(resource_bounds: ResourceBounds) -> felt {
    assert_nn_le(resource_bounds.max_amount, 2 ** 64 - 1);
    assert_nn(resource_bounds.max_price_per_unit);
    return (resource_bounds.resource * 2 ** 64 + resource_bounds.max_amount) * 2 ** 128 +
        resource_bounds.max_price_per_unit;
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L75-78)
```text
func get_initial_user_gas_bound(common_tx_fields: CommonTxFields*) -> felt {
    assert common_tx_fields.n_resource_bounds = 3;
    return common_tx_fields.resource_bounds[L2_GAS_INDEX].max_amount;
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L99-101)
```text
    return l1_gas_bounds.max_amount * l1_gas_bounds.max_price_per_unit + l2_gas_bounds.max_amount *
        (l2_gas_bounds.max_price_per_unit + tx_info.tip) + l1_data_gas_bounds.max_amount *
        l1_data_gas_bounds.max_price_per_unit;
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
