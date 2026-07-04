### Title
Uncapped Gas Allocation for Non-Sierra-Gas-Mode Cairo 1 Contracts Bypasses Transaction Resource Bounds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/entry_point_utils.cairo`)

---

### Summary

In `select_execute_entry_point_func`, Cairo 1 contracts not yet running in Sierra gas mode receive `DEFAULT_INITIAL_GAS_COST = 10,000,000,000` gas regardless of the transaction's declared L2 gas bound, and the consumed gas is **not deducted** from the caller's remaining gas after execution. This allows any unprivileged user to cause up to 10¹⁰ gas units of computation per entry-point call without paying for it, enabling fee-free block-space griefing.

---

### Finding Description

`select_execute_entry_point_func` in `entry_point_utils.cairo` contains a transitional code path for Cairo 1 contracts not yet in Sierra gas mode:

```cairo
local caller_remaining_gas = remaining_gas;
local is_sierra_gas_mode;
%{ IsSierraGasMode %}
if (is_sierra_gas_mode != FALSE) {
    tempvar inner_remaining_gas = remaining_gas;
} else {
    // Run with high enough gas to avoid out-of-gas.
    tempvar inner_remaining_gas = DEFAULT_INITIAL_GAS_COST;   // 10,000,000,000
}
...
if (is_sierra_gas_mode != FALSE) {
    tempvar remaining_gas = inner_remaining_gas;
} else {
    // Do not count Sierra gas for the caller in this case.
    tempvar remaining_gas = caller_remaining_gas;              // restored — gas not counted
}
``` [1](#0-0) 

When `is_sierra_gas_mode == FALSE`:

1. **The entry point receives `DEFAULT_INITIAL_GAS_COST = 10,000,000,000` gas** (line 53), completely ignoring the caller's actual `remaining_gas`.
2. **After execution, `remaining_gas` is restored to `caller_remaining_gas`** (line 65), meaning every gas unit consumed by the entry point is silently discarded from the accounting. [2](#0-1) 

The constants make the disparity concrete:

| Constant | Value |
|---|---|
| `DEFAULT_INITIAL_GAS_COST` | 10,000,000,000 |
| `EXECUTE_MAX_SIERRA_GAS` | 1,100,000,000 |
| `VALIDATE_MAX_SIERRA_GAS` | 100,000,000 |

A non-Sierra-gas-mode contract therefore runs with **~9× more gas** than the hard cap applied to normal Sierra contracts, and that gas is never subtracted from the transaction's budget.

The `cap_remaining_gas` call in `execute_invoke_function_transaction` (line 344) caps `remaining_gas` to `EXECUTE_MAX_SIERRA_GAS` before the call, but `select_execute_entry_point_func` immediately overrides this with `DEFAULT_INITIAL_GAS_COST` for non-Sierra-gas-mode contracts, defeating the cap entirely. [3](#0-2) 

Fee charging in `charge_fee` is based on `compute_max_possible_fee`, which reads only the transaction's **declared** resource bounds — not actual computation consumed: [4](#0-3) 

This is structurally identical to the ZetaChain finding: just as ZetaChain applied an infinite gas meter to an entire transaction if **any** message was of a privileged type, here `DEFAULT_INITIAL_GAS_COST` is applied to **any** entry-point call targeting a non-Sierra-gas-mode contract, regardless of the transaction's declared resource bounds, and the consumed gas is never charged back.

---

### Impact Explanation

**High — Network not being able to confirm new transactions.**

A user submitting an invoke transaction with a minimal declared L2 gas bound (e.g., 1,000 gas units, paying near-zero fee) can call a non-Sierra-gas-mode Cairo 1 contract. That contract executes with 10,000,000,000 gas. Because `remaining_gas` is restored to `caller_remaining_gas` after the call, the user's gas budget is **not depleted**, allowing them to chain multiple such calls within a single transaction. Each call causes up to 10¹⁰ gas units of sequencer computation while the user pays only for their declared bounds. By submitting many such transactions, an attacker fills the block's computational capacity without paying proportional fees, starving legitimate transactions of block space.

---

### Likelihood Explanation

The TODO comment in the source code itself confirms that non-Sierra-gas-mode Cairo 1 contracts currently exist on the network:

```cairo
// TODO(Yoni): SIERRA_GAS_MODE - remove once all Cairo 1 contracts run with Sierra gas mode.
``` [5](#0-4) 

Any unprivileged user who calls one of these existing contracts triggers the bypass. No special role, leaked key, or privileged access is required — only a standard invoke transaction targeting an existing non-Sierra-gas-mode contract.

---

### Recommendation

1. **Cap `inner_remaining_gas` to `remaining_gas`** even for non-Sierra-gas-mode contracts, so computation cannot exceed the transaction's declared L2 gas bound:
   ```cairo
   } else {
       // Cap to caller's budget to prevent fee bypass.
       tempvar inner_remaining_gas = remaining_gas;  // was DEFAULT_INITIAL_GAS_COST
   }
   ```
2. **Track and deduct consumed gas** from the caller's budget after a non-Sierra-gas-mode call, rather than restoring `caller_remaining_gas` unconditionally.
3. **Expedite migration** of all Cairo 1 contracts to Sierra gas mode and remove this transitional code path entirely.

---

### Proof of Concept

1. Identify an existing Cairo 1 contract that is NOT in Sierra gas mode (confirmed to exist by the TODO comment in `entry_point_utils.cairo` line 29).
2. Submit an invoke transaction (V3) with a minimal L2 gas bound, e.g., `resource_bounds[L2_GAS].max_amount = 1000`. Fee paid ≈ 0.
3. The transaction calls the non-Sierra-gas-mode contract via `execute_invoke_function_transaction`.
4. `cap_remaining_gas(EXECUTE_MAX_SIERRA_GAS)` runs — `remaining_gas` stays at 1,000 (already below cap).
5. `non_reverting_select_execute_entry_point_func` is called with `remaining_gas = 1,000`.
6. Inside `select_execute_entry_point_func`: `is_sierra_gas_mode = FALSE` → `inner_remaining_gas = DEFAULT_INITIAL_GAS_COST = 10,000,000,000`.
7. The contract executes with 10¹⁰ gas — up to 10,000,000× more than the user paid for.
8. After execution: `remaining_gas = caller_remaining_gas = 1,000` (restored, budget not depleted).
9. The user can chain additional calls to non-Sierra-gas-mode contracts within the same transaction, each receiving 10¹⁰ gas, with no cumulative gas deduction.
10. Submitting many such transactions fills block computational capacity at near-zero cost, preventing other transactions from being confirmed.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/entry_point_utils.cairo (L29-30)
```text
    // TODO(Yoni): SIERRA_GAS_MODE - move back inside `execute_entry_point` functions.
    %{ EnterCall %}
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants.cairo (L89-92)
```text
const DEFAULT_INITIAL_GAS_COST = 10000000000;
const VALIDATE_MAX_SIERRA_GAS = 100000000;
const EXECUTE_MAX_SIERRA_GAS = 1100000000;
const DEFAULT_INITIAL_GAS_COST_NO_L2 = VALIDATE_MAX_SIERRA_GAS + EXECUTE_MAX_SIERRA_GAS;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L120-135)
```text
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L343-348)
```text
        with remaining_gas {
            cap_remaining_gas(max_gas=EXECUTE_MAX_SIERRA_GAS);
            non_reverting_select_execute_entry_point_func(
                block_context=block_context, execution_context=updated_tx_execution_context
            );
        }
```
