### Title
Missing Minimum L2 Gas Validation in OS Transaction Execution Causes Proof Generation Failure — (File: `execution/transaction_impls.cairo`)

---

### Summary

The StarkNet OS does not enforce any minimum L2 gas bound when processing user-submitted account transactions. The user-controlled field `resource_bounds[L2_GAS_INDEX].max_amount` is consumed directly as `remaining_gas` with only an upper cap applied. If this value is below `ENTRY_POINT_INITIAL_BUDGET` (10,000 gas units), the validate entry point returns `is_reverted=1` due to out-of-gas, and `non_reverting_select_execute_entry_point_func` unconditionally asserts `is_reverted = 0`, causing the OS Cairo program to panic. A panicking OS cannot produce a valid STARK proof for the block, halting the network.

---

### Finding Description

**Step 1 — User gas is taken without a minimum check.**

`get_initial_user_gas_bound` reads the user-supplied `max_amount` field directly:

```cairo
// transaction_impls.cairo:75-78
func get_initial_user_gas_bound(common_tx_fields: CommonTxFields*) -> felt {
    assert common_tx_fields.n_resource_bounds = 3;
    return common_tx_fields.resource_bounds[L2_GAS_INDEX].max_amount;
}
```

No lower bound is asserted. A user may supply `max_amount = 0`.

**Step 2 — `cap_remaining_gas` enforces only a ceiling, never a floor.**

```cairo
// execute_transaction_utils.cairo:165-177
func cap_remaining_gas{range_check_ptr, remaining_gas: felt}(max_gas: felt) {
    ...
    if (remaining_gas_gt_max != FALSE) {
        assert_nn_le(max_gas, remaining_gas - 1);
        tempvar remaining_gas = max_gas;
    } else {
        assert_nn_le(remaining_gas, max_gas);   // only proves remaining_gas ≤ max_gas
        tempvar remaining_gas = remaining_gas;
    }
    return ();
}
```

After this call, `remaining_gas` may still be 0 or any value below `ENTRY_POINT_INITIAL_BUDGET`.

**Step 3 — The validate step is called through a non-reverting wrapper.**

In `execute_invoke_function_transaction` (and identically in `execute_deploy_account_transaction` and `execute_declare_transaction`):

```cairo
// transaction_impls.cairo:326-330
with remaining_gas {
    cap_remaining_gas(max_gas=VALIDATE_MAX_SIERRA_GAS);
    let pre_validate_gas = remaining_gas;
    run_validate(block_context=block_context, tx_execution_context=tx_execution_context);
}
```

`run_validate` calls `non_reverting_select_execute_entry_point_func`:

```cairo
// execute_transaction_utils.cairo:181-197
func non_reverting_select_execute_entry_point_func{...}(...) -> (...) {
    let revert_log = init_revert_log();
    let (is_reverted, retdata_size, retdata, is_deprecated) = select_execute_entry_point_func{...}(...);
    assert is_reverted = 0;   // <-- UNCONDITIONAL PANIC if validate reverts
    return (retdata_size, retdata, is_deprecated);
}
```

**Step 4 — `execute_entry_point` returns `is_reverted=1` when gas is below the initial budget.**

```cairo
// execute_entry_point.cairo:196-204
if (is_remaining_gas_lt_initial_budget != FALSE) {
    assert_lt(remaining_gas, ENTRY_POINT_INITIAL_BUDGET);
    %{ ExitCall %}
    let (retdata: felt*) = alloc();
    assert retdata[0] = ERROR_OUT_OF_GAS;
    return (is_reverted=1, retdata_size=1, retdata=retdata);
}
```

`ENTRY_POINT_INITIAL_BUDGET = 10000` (constants.cairo:98). Any `remaining_gas < 10000` triggers this path.

**Step 5 — Sierra gas mode propagates the user gas directly.**

In `select_execute_entry_point_func` (entry_point_utils.cairo:49-53), when `is_sierra_gas_mode = TRUE` (the intended mode for all new Cairo 1 contracts), `inner_remaining_gas = remaining_gas` — the user-supplied value flows unmodified into `execute_entry_point`. The non-Sierra fallback uses `DEFAULT_INITIAL_GAS_COST` and is immune, but it is explicitly a legacy compatibility path.

**Combined effect:** A user submitting any account transaction (Invoke, DeployAccount, Declare) with `resource_bounds[L2_GAS_INDEX].max_amount < 10000` causes the OS to hit `assert is_reverted = 0` with `is_reverted = 1`, which is an unsatisfiable Cairo constraint. The prover cannot produce a valid proof for any block containing such a transaction.

---

### Impact Explanation

When the OS Cairo program encounters an unsatisfiable assertion, the STARK proof for the entire block cannot be generated. The sequencer is unable to commit the block to L1. If an attacker can reliably inject such a transaction into blocks, block finalization halts permanently for as long as the attack continues — matching the **High: Network not being able to confirm new transactions (total network shutdown)** impact category.

---

### Likelihood Explanation

The attacker's entry point is the public transaction submission API. The attacker submits a v3 Invoke (or DeployAccount/Declare) transaction with `resource_bounds[L2_GAS_INDEX].max_amount` set to any value below 10,000. The OS itself performs no minimum gas assertion; the only defense is off-chain sequencer/gateway pre-validation. If the gateway's minimum gas floor is absent, misconfigured, or bypassable (e.g., via a direct mempool injection or a gateway validation gap), the transaction reaches the OS and triggers the panic. Because the OS is the authoritative execution layer and contains no floor check, any gap in the gateway layer is directly exploitable. The cost to the attacker is a single transaction fee (or zero, if `max_amount = 0` and `max_price = 0` passes fee checks).

---

### Recommendation

Add an explicit minimum gas assertion in `get_initial_user_gas_bound` or immediately after it is consumed, before any entry point is invoked:

```cairo
// In transaction_impls.cairo, after line 77:
let gas = common_tx_fields.resource_bounds[L2_GAS_INDEX].max_amount;
with_attr error_message("L2 gas bound below minimum required.") {
    assert_nn_le(ENTRY_POINT_INITIAL_BUDGET, gas);  // gas >= ENTRY_POINT_INITIAL_BUDGET
}
return gas;
```

Alternatively, convert the `assert is_reverted = 0` in `non_reverting_select_execute_entry_point_func` into a graceful revert path for the out-of-gas case specifically during validate, so that an under-gassed transaction is rejected (reverted) rather than causing an OS panic. Each packet/transaction type (Invoke, DeployAccount, Declare) should have its own enforced minimum that covers at least the validate phase cost.

---

### Proof of Concept

1. Attacker constructs a valid v3 Invoke transaction targeting any deployed Sierra contract, with:
   - `resource_bounds[L2_GAS_INDEX].max_amount = 0` (or any value < 10,000)
   - `resource_bounds[L2_GAS_INDEX].max_price_per_unit = 0`
2. Transaction passes signature and nonce checks (these do not depend on gas amount).
3. Sequencer includes the transaction in a block (no OS-level floor prevents this).
4. OS executes `execute_invoke_function_transaction`:
   - `initial_user_gas_bound = 0`
   - `cap_remaining_gas(VALIDATE_MAX_SIERRA_GAS)` → `remaining_gas = 0`
   - `run_validate` → `non_reverting_select_execute_entry_point_func` → `select_execute_entry_point_func` (Sierra gas mode) → `execute_entry_point` with `remaining_gas = 0`
   - `0 < ENTRY_POINT_INITIAL_BUDGET (10000)` → returns `(is_reverted=1, ...)`
   - `non_reverting_select_execute_entry_point_func`: `assert is_reverted = 0` → **UNSATISFIABLE CONSTRAINT**
5. Prover cannot generate a STARK proof for the block.
6. Block is never committed to L1; network halts.

**Affected functions (all share the same root cause):**
- `execute_invoke_function_transaction` — `transaction_impls.cairo:322-330`
- `execute_deploy_account_transaction` — `transaction_impls.cairo:634-648, 674-680`
- `execute_declare_transaction` — `transaction_impls.cairo:799-807` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L75-78)
```text
func get_initial_user_gas_bound(common_tx_fields: CommonTxFields*) -> felt {
    assert common_tx_fields.n_resource_bounds = 3;
    return common_tx_fields.resource_bounds[L2_GAS_INDEX].max_amount;
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L322-332)
```text
    let initial_user_gas_bound = get_initial_user_gas_bound(common_tx_fields=common_tx_fields);
    let remaining_gas = initial_user_gas_bound;

    // Validate.
    with remaining_gas {
        cap_remaining_gas(max_gas=VALIDATE_MAX_SIERRA_GAS);
        let pre_validate_gas = remaining_gas;
        run_validate(block_context=block_context, tx_execution_context=tx_execution_context);
    }
    let validate_gas_consumed = pre_validate_gas - remaining_gas;
    tempvar remaining_gas = initial_user_gas_bound - validate_gas_consumed;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L165-177)
```text
func cap_remaining_gas{range_check_ptr, remaining_gas: felt}(max_gas: felt) {
    alloc_locals;
    local remaining_gas_gt_max;
    %{ RemainingGasGtMax %}
    if (remaining_gas_gt_max != FALSE) {
        assert_nn_le(max_gas, remaining_gas - 1);
        tempvar remaining_gas = max_gas;
    } else {
        assert_nn_le(remaining_gas, max_gas);
        tempvar remaining_gas = remaining_gas;
    }
    return ();
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L181-197)
```text
func non_reverting_select_execute_entry_point_func{
    range_check_ptr,
    remaining_gas: felt,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    outputs: OsCarriedOutputs*,
}(block_context: BlockContext*, execution_context: ExecutionContext*) -> (
    retdata_size: felt, retdata: felt*, is_deprecated: felt
) {
    let revert_log = init_revert_log();
    let (is_reverted, retdata_size, retdata, is_deprecated) = select_execute_entry_point_func{
        revert_log=revert_log
    }(block_context=block_context, execution_context=execution_context);
    assert is_reverted = 0;
    return (retdata_size, retdata, is_deprecated);
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L196-204)
```text
    local is_remaining_gas_lt_initial_budget;
    %{ IsRemainingGasLtInitialBudget %}
    if (is_remaining_gas_lt_initial_budget != FALSE) {
        assert_lt(remaining_gas, ENTRY_POINT_INITIAL_BUDGET);
        %{ ExitCall %}
        let (retdata: felt*) = alloc();
        assert retdata[0] = ERROR_OUT_OF_GAS;
        return (is_reverted=1, retdata_size=1, retdata=retdata);
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/entry_point_utils.cairo (L46-67)
```text
    local caller_remaining_gas = remaining_gas;
    local is_sierra_gas_mode;
    %{ IsSierraGasMode %}
    if (is_sierra_gas_mode != FALSE) {
        tempvar inner_remaining_gas = remaining_gas;
    } else {
        // Run with high enough gas to avoid out-of-gas.
        tempvar inner_remaining_gas = DEFAULT_INITIAL_GAS_COST;
    }
    %{ DebugExpectedInitialGas %}

    let (is_reverted, retdata_size, retdata) = execute_entry_point{
        remaining_gas=inner_remaining_gas
    }(block_context=block_context, execution_context=execution_context);

    if (is_sierra_gas_mode != FALSE) {
        tempvar remaining_gas = inner_remaining_gas;
    } else {
        // Do not count Sierra gas for the caller in this case.
        tempvar remaining_gas = caller_remaining_gas;
    }
    return (is_reverted=is_reverted, retdata_size=retdata_size, retdata=retdata, is_deprecated=0);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants.cairo (L89-101)
```text
const DEFAULT_INITIAL_GAS_COST = 10000000000;
const VALIDATE_MAX_SIERRA_GAS = 100000000;
const EXECUTE_MAX_SIERRA_GAS = 1100000000;
const DEFAULT_INITIAL_GAS_COST_NO_L2 = VALIDATE_MAX_SIERRA_GAS + EXECUTE_MAX_SIERRA_GAS;

// Compiler gas costs.

// The initial budget at an entry point. This needs to be high enough to cover the initial get_gas.
// The entry point may refund whatever remains from the initial budget.
const ENTRY_POINT_INITIAL_BUDGET = 10000;
// The gas cost of each syscall libfunc (this value is hard-coded by the compiler).
// This needs to be high enough to cover OS costs in the case of failure due to out of gas.
const SYSCALL_BASE_GAS_COST = 10000;
```
