### Title
Fee Transfer Revert in `charge_fee` via `non_reverting_select_execute_entry_point_func` Assertion Causes Unprovable Block — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo`)

---

### Summary

The `charge_fee` function in `transaction_impls.cairo` pushes fee ERC20 transfers to the sequencer using `non_reverting_select_execute_entry_point_func`, which hard-asserts `is_reverted = 0`. If the fee token contract's `transfer` entry point reverts for any reason — including blacklisting of the sender's address — the Cairo assertion fails, the STARK proof cannot be generated, and the block cannot be finalized on L1. Because every subsequent block depends on the previous state root, a single unprovable block causes a total network halt.

---

### Finding Description

`charge_fee` is called unconditionally for every account transaction (invoke, deploy-account, declare), including reverted ones: [1](#0-0) 

Inside `charge_fee`, an ERC20 `transfer` execution context is constructed with the user's account as `caller_address` and the sequencer as `recipient`, then dispatched through: [2](#0-1) 

`non_reverting_select_execute_entry_point_func` is defined as: [3](#0-2) 

The critical line is `assert is_reverted = 0` at line 195. In Cairo, a failed assertion does not raise a runtime exception — it makes the entire execution trace unsatisfiable, so no valid STARK proof can be produced for the block. There is no `try/catch`, no fallback path, and no pull-pattern alternative.

The fee token address is a protocol-level ERC20 contract (STRK): [4](#0-3) 

If the fee token contract's `transfer` function reverts — for example because the sender's address is blacklisted in the token contract — the assertion at line 195 of `execute_transaction_utils.cairo` fails, the proof is unsatisfiable, and the block cannot be submitted to L1.

The same `non_reverting_select_execute_entry_point_func` is also used for `__validate__` and `__validate_declare__`/`__validate_deploy__`: [5](#0-4) [6](#0-5) 

However, the fee transfer path is the most dangerous because it is called for **every** transaction, including already-reverted ones, and the sequencer has no in-protocol mechanism to skip it.

---

### Impact Explanation

If `charge_fee` cannot produce a satisfiable trace, the OS Cairo program (`os.cairo` → `execute_blocks` → `execute_transactions` → `execute_transactions_inner` → `charge_fee`) cannot generate a valid proof for the block. [7](#0-6) 

Without a valid proof, the block cannot be posted to L1. The network cannot advance its state root, and no further transactions can be confirmed — a **total network shutdown** (High impact per the allowed scope).

---

### Likelihood Explanation

The STRK fee token is an upgradeable StarkNet contract. If the token contract is upgraded to include a blacklist (a common ERC20 extension, as seen with USDC on Ethereum), any blacklisted address that submits a transaction will cause the block containing that transaction to be unprovable. The sequencer has no in-OS mechanism to detect this before proof generation; the only defense is off-chain filtering, which is not enforced by the protocol. An unprivileged transaction sender (invoke, deploy-account, or declare) is the attacker-controlled entry point — no privileged role is required beyond having a blacklisted address in the fee token.

---

### Recommendation

Replace the push-based fee collection in `charge_fee` with a pull pattern: record the owed fee in storage and let the sequencer claim it separately, outside the block-proof critical path. Alternatively, wrap the fee transfer in a reverting execution context (analogous to how user transactions are handled with `is_reverted` checks) so that a failed fee transfer marks the transaction as reverted rather than making the entire block unprovable.

---

### Proof of Concept

1. The STRK fee token contract is upgraded to add address blacklisting (a realistic upgrade for a production ERC20).
2. Attacker address `A` is blacklisted in the fee token.
3. Attacker submits an invoke transaction from address `A` with valid signature and sufficient declared resource bounds.
4. The sequencer includes the transaction in a block (it passes mempool validation because the sequencer's off-chain check does not inspect the fee token's blacklist).
5. The OS executes the block: `execute_transactions_inner` → `execute_invoke_function_transaction`.
6. The user's `__execute__` runs (possibly reverts, does not matter).
7. `charge_fee` is called unconditionally (line 361 of `transaction_impls.cairo`).
8. `charge_fee` calls `non_reverting_select_execute_entry_point_func` to invoke `transfer` on the fee token with `caller_address = A`.
9. The fee token's `transfer` reverts because `A` is blacklisted.
10. `assert is_reverted = 0` (line 195 of `execute_transaction_utils.cairo`) fails.
11. The Cairo execution trace is unsatisfiable; no STARK proof can be generated.
12. The block cannot be submitted to L1; the network halts.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L138-141)
```text
    local fee_token_address = block_context.os_global_context.starknet_os_config.fee_token_address;
    let (fee_state_entry: StateEntry*) = dict_read{dict_ptr=contract_state_changes}(
        key=fee_token_address
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L160-163)
```text
    let remaining_gas = DEFAULT_INITIAL_GAS_COST;
    non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
        block_context=block_context, execution_context=&execution_context
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L360-361)
```text
    // Charge fee.
    charge_fee(block_context=block_context, tx_execution_context=updated_tx_execution_context);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L677-679)
```text
        let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(
            block_context=block_context, execution_context=validate_deploy_execution_context
        );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L148-151)
```text
    // The __validate__ function should not revert.
    let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(
        block_context=block_context, execution_context=validate_execution_context
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L181-196)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os.cairo (L221-225)
```text
    // Execute transactions.
    let outputs = initial_carried_outputs;
    with contract_state_changes, contract_class_changes, outputs {
        execute_transactions(block_context=block_context);
    }
```
