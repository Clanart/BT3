### Title
`deploy_contract` Hard-Asserts Uninitialized Address Without Graceful Failure, Enabling Proof-Breaking Front-Run - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo`)

---

### Summary

The `deploy_contract` function enforces that the target address is uninitialized via a hard Cairo `assert`. Unlike a Solidity revert, a failed Cairo `assert` is a proof-constraint violation that prevents block proof generation entirely. Compounding this, `execute_deploy_account_transaction` calls `deploy_contract` with no `IsReverted` guard — unlike every other transaction type in the OS. An unprivileged attacker can front-run a `deploy_account` transaction, causing the OS to encounter the failing assertion and making the block unprovable, halting the network.

---

### Finding Description

**Root cause 1 — Hard assertion in `deploy_contract`:** [1](#0-0) 

```cairo
local state_entry: StateEntry*;
%{ GetContractAddressStateEntry %}
assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;
assert state_entry.nonce = 0;
```

In Cairo, `assert` is a proof constraint. If `state_entry.class_hash != UNINITIALIZED_CLASS_HASH` (i.e., the contract already exists), the constraint is unsatisfiable and the STARK proof for the entire block cannot be generated. This is not a graceful revert — it is a proof failure. The developer TODO at line 28 explicitly acknowledges this is unresolved: [2](#0-1) 

```cairo
// TODO(Yoni, 1/1/2027): handle failures.
```

Additionally, line 91 asserts the constructor never reverts, confirming the OS has zero failure-handling for the deploy path: [3](#0-2) 

**Root cause 2 — Missing `IsReverted` guard in `execute_deploy_account_transaction`:**

Every other transaction type in the OS checks whether the sequencer has marked the transaction as reverted before executing. `execute_invoke_function_transaction` does this: [4](#0-3) 

`execute_l1_handler_transaction` does this: [5](#0-4) 

`execute_deploy_account_transaction` does **not**. It calls `deploy_contract` unconditionally: [6](#0-5) 

```cairo
with remaining_gas {
    cap_remaining_gas(max_gas=VALIDATE_MAX_SIERRA_GAS);
    let pre_constructor_gas = remaining_gas;
    let revert_log = init_revert_log();
    deploy_contract{revert_log=revert_log}(
        block_context=block_context, constructor_execution_context=constructor_execution_context
    );
}
```

There is no `%{ IsReverted %}` / `check_is_reverted` call before this. If the sequencer marks the `deploy_account` transaction as reverted (because the address already exists) and still includes it in the block to charge fees — exactly as it does for reverted invoke transactions — the OS will unconditionally call `deploy_contract`, hit the unsatisfiable assertion, and fail to produce a proof.

The contract address is computed deterministically: [7](#0-6) 

This means any observer can predict the address before the transaction is confirmed.

---

### Impact Explanation

If a block is produced containing a `deploy_account` transaction whose target address is already occupied, the OS Cairo program hits an unsatisfiable proof constraint at `assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH`. The STARK proof for that block cannot be generated. The block cannot be committed to L1. If the sequencer retries with the same transaction set, the proof fails again. This constitutes **total network shutdown** — the network cannot confirm new transactions until the invalid transaction is excluded and a new block is produced.

---

### Likelihood Explanation

The attack is reachable by any unprivileged user:

1. A victim submits a `deploy_account` transaction. Its parameters (salt, class_hash, constructor calldata) are visible in the mempool.
2. The attacker computes the deterministic contract address using the same inputs.
3. The attacker submits a transaction that deploys a contract to that address first (via the `deploy` syscall from an existing contract, or a competing `deploy_account`).
4. The sequencer orders the attacker's transaction before the victim's in the same block.
5. The victim's `deploy_account` is now a reverted transaction (address occupied). The sequencer includes it to collect fees.
6. The OS processes it without an `IsReverted` guard, calls `deploy_contract`, and fails the proof.

This is a direct analog to the ERC-4337 front-running DoS described in the external report, but with a more severe outcome: instead of a single transaction reverting, the entire block proof fails.

---

### Recommendation

**Short-term:** Add an `IsReverted` guard in `execute_deploy_account_transaction` before calling `deploy_contract`, matching the pattern used by `execute_invoke_function_transaction` and `execute_l1_handler_transaction`.

**Long-term:** Replace the hard `assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH` in `deploy_contract` with a graceful failure path that writes a failure response and returns, rather than violating a proof constraint. The existing TODO at line 28 already tracks this work.

---

### Proof of Concept

```
1. Victim broadcasts deploy_account(salt=S, class_hash=C, calldata=D).
   → Deterministic address A = hash(S, C, D, deployer=0) is publicly computable.

2. Attacker broadcasts deploy_account(salt=S, class_hash=C, calldata=D) with higher fee,
   OR calls deploy(class_hash=C, salt=S, calldata=D) from an existing contract.
   → Attacker's tx is sequenced first; contract at address A is now initialized.

3. Victim's deploy_account tx is sequenced second.
   → Sequencer blockifier detects address A is occupied; marks tx as reverted.
   → Sequencer includes the reverted tx in the block (to charge fee).

4. OS execute_deploy_account_transaction runs:
   → prepare_constructor_execution_context computes address A.
   → deploy_contract is called unconditionally (no IsReverted check).
   → assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH FAILS
     (class_hash = C, not UNINITIALIZED_CLASS_HASH = 0).

5. Cairo proof constraint is unsatisfiable.
   → Block proof generation fails.
   → Block cannot be committed to L1.
   → Network halts until the invalid transaction is excluded.
```

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L28-28)
```text
// TODO(Yoni, 1/1/2027): handle failures.
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L51-54)
```text
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}
    assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;
    assert state_entry.nonce = 0;
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L91-92)
```text
    assert is_reverted = 0;
    return (retdata_size=retdata_size, retdata=retdata);
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L338-358)
```text
    local is_reverted;
    %{ IsReverted %}
    check_is_reverted(is_reverted);
    if (is_reverted == FALSE) {
        // Execute only non-reverted transactions.
        with remaining_gas {
            cap_remaining_gas(max_gas=EXECUTE_MAX_SIERRA_GAS);
            non_reverting_select_execute_entry_point_func(
                block_context=block_context, execution_context=updated_tx_execution_context
            );
        }
    } else {
        // Align the stack with the `if` branch to avoid revoked references.
        tempvar range_check_ptr = range_check_ptr;
        tempvar remaining_gas = remaining_gas;
        tempvar builtin_ptrs = builtin_ptrs;
        tempvar contract_state_changes = contract_state_changes;
        tempvar contract_class_changes = contract_class_changes;
        tempvar outputs = outputs;
        tempvar _dummy_return_value: non_reverting_select_execute_entry_point_func.Return;
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L383-390)
```text
    %{ StartTx %}
    local is_reverted;
    %{ IsReverted %}
    // Skip the execution step for reverted transaction.
    if (is_reverted != FALSE) {
        %{ EndTx %}
        return ();
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L536-545)
```text
    let hash_ptr = builtin_ptrs.selectable.pedersen;
    with hash_ptr {
        let (contract_address) = get_contract_address(
            salt=contract_address_salt,
            class_hash=class_hash,
            constructor_calldata_size=constructor_calldata_size,
            constructor_calldata=constructor_calldata,
            deployer_address=0,
        );
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L638-646)
```text
    with remaining_gas {
        // The constructor entry point runs with a validate call context.
        cap_remaining_gas(max_gas=VALIDATE_MAX_SIERRA_GAS);
        let pre_constructor_gas = remaining_gas;
        let revert_log = init_revert_log();
        deploy_contract{revert_log=revert_log}(
            block_context=block_context, constructor_execution_context=constructor_execution_context
        );
    }
```
