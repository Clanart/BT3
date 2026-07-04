### Title
Unchecked Return Value of Fee Transfer Execution Allows Fee-Free Transaction Processing — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

The `charge_fee` function in `transaction_impls.cairo` calls `non_reverting_select_execute_entry_point_func` to execute the fee token's `transfer` entry point but completely discards the return value. The OS never verifies whether the fee transfer actually succeeded. This is the direct Cairo/StarkNet analog of the `RewardsClaimer` unchecked-return-value class: a critical operation's success indicator is silently ignored, allowing the operation to fail while the surrounding logic proceeds as if it succeeded.

---

### Finding Description

In `charge_fee`, after constructing the `ExecutionContext` for the ERC20 `transfer` call, the OS invokes:

```cairo
let remaining_gas = DEFAULT_INITIAL_GAS_COST;
non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
    block_context=block_context, execution_context=&execution_context
);
return ();
``` [1](#0-0) 

`non_reverting_select_execute_entry_point_func` returns a tuple `(retdata_size, retdata, is_deprecated)`. In `charge_fee` none of these are captured or inspected. Because the function is *non-reverting*, even if the underlying `transfer` entry point fails (returns `failure_flag=1` or returns `false` in its retdata), execution continues normally and the OS considers the fee as paid.

The contrast with the validate steps is stark. Both `execute_declare_transaction` and `execute_deploy_account_transaction` call the same function and explicitly assert the return value:

```cairo
let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(
    block_context=block_context, execution_context=validate_declare_execution_context
);
if (is_deprecated == 0) {
    assert retdata_size = 1;
    assert retdata[0] = VALIDATED;
}
``` [2](#0-1) [3](#0-2) 

The OS enforces that `__validate__` returns `VALIDATED`, but enforces nothing about the fee transfer result. The only check performed before the transfer is that `actual_fee ≤ max_fee`:

```cairo
assert_nn_le(calldata.amount.low, max_fee);
``` [4](#0-3) 

There is no check that the sender holds sufficient fee token balance, and no check that the transfer call succeeded. `charge_fee` is invoked for every account transaction type — invoke, deploy-account, and declare: [5](#0-4) [6](#0-5) [7](#0-6) 

---

### Impact Explanation

**Critical — Direct loss of funds.**

The StarkNet OS is the proof system that enforces protocol invariants. A valid proof is supposed to guarantee that every processed transaction paid its declared fee. Because the OS does not assert that the fee transfer succeeded, a valid STARK proof can be generated for a block in which transactions were executed without any fee being transferred to the sequencer/fee recipient. The fee token balance of the sender is never decremented in the committed state, yet the transaction's state changes are fully applied. The fee recipient suffers a direct, provable loss of funds for every such transaction included in a proven block.

---

### Likelihood Explanation

The attack is reachable by an unprivileged transaction sender. A user can submit a transaction whose fee token balance is insufficient at execution time (e.g., by draining the balance in a prior transaction within the same block, or via a race condition between sequencer mempool admission and block execution). The sequencer's off-chain admission check does not prevent this at the proof level. Because the OS never asserts transfer success, the resulting proof is unconditionally valid and will be accepted by the L1 verifier. No privileged access, leaked key, or operator collusion is required.

---

### Recommendation

Capture and validate the return value of `non_reverting_select_execute_entry_point_func` inside `charge_fee`, mirroring the pattern already used for the validate steps:

```cairo
let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func{
    remaining_gas=remaining_gas
}(block_context=block_context, execution_context=&execution_context);
// For Cairo 1 ERC20, transfer returns a single felt equal to 1 (TRUE) on success.
if (is_deprecated == 0) {
    assert retdata_size = 1;
    assert retdata[0] = 1;  // TRUE / success
}
```

This ensures the OS proof guarantees that every fee transfer actually succeeded before the block is considered valid.

---

### Proof of Concept

1. User `A` holds exactly 0 STRK in their fee token balance but submits an invoke transaction with `max_fee > 0` and `resource_bounds.l2_gas.max_amount > 0`.
2. The sequencer admits the transaction (balance check at admission time may pass if the drain happens concurrently, or the sequencer is simply honest but the OS is the enforcement layer).
3. The OS executes the transaction body, then calls `charge_fee`.
4. Inside `charge_fee`, `non_reverting_select_execute_entry_point_func` calls the fee token's `transfer(sequencer, actual_fee)`.
5. The fee token's `transfer` reverts because `A`'s balance is 0. The entry point returns `failure_flag=1`.
6. Because `non_reverting_select_execute_entry_point_func` is non-reverting, it returns normally. Because `charge_fee` ignores the return value, it returns normally.
7. The OS commits the block with `A`'s transaction fully applied and zero fee transferred.
8. A valid STARK proof is generated and accepted on L1.
9. The sequencer/fee recipient receives 0 tokens; `A`'s transaction was processed for free.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L135-135)
```text
    assert_nn_le(calldata.amount.low, max_fee);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L160-164)
```text
    let remaining_gas = DEFAULT_INITIAL_GAS_COST;
    non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
        block_context=block_context, execution_context=&execution_context
    );
    return ();
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L361-361)
```text
    charge_fee(block_context=block_context, tx_execution_context=updated_tx_execution_context);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L677-684)
```text
        let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(
            block_context=block_context, execution_context=validate_deploy_execution_context
        );
    }
    if (is_deprecated == 0) {
        assert retdata_size = 1;
        assert retdata[0] = VALIDATED;
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L687-687)
```text
    charge_fee(block_context=block_context, tx_execution_context=validate_deploy_execution_context);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L804-812)
```text
        let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(
            block_context=block_context, execution_context=validate_declare_execution_context
        );
    }
    // TODO(Yoni): calculate the gas consumed and use it to charge fee (for all transactions).
    if (is_deprecated == 0) {
        assert retdata_size = 1;
        assert retdata[0] = VALIDATED;
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L822-822)
```text
    charge_fee(
```
