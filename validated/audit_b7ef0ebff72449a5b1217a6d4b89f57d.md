### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Arbitrary Class Hash Assignment, Permanently Freezing Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS does not validate that the class hash supplied to the `replace_class` syscall corresponds to a previously declared contract class. An unprivileged contract owner can call `replace_class` with an arbitrary, undeclared class hash. The OS immediately commits this invalid class hash to `contract_state_changes` without any existence check. Because the class switch is instantaneous and takes effect within the same transaction, subsequent calls to the contract will fail to resolve the class, permanently freezing any funds held in the contract's storage.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` (lines 877–916) explicitly acknowledges the missing check with a TODO comment:

```
// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
```

The function reads the requested class hash from the syscall pointer and immediately writes it into `contract_state_changes` via `dict_update`, with no lookup into `contract_class_changes` (the declared-class registry) and no hint-based existence check:

```cairo
let class_hash = request.class_hash;          // attacker-controlled
// ← no validation that class_hash is declared
local state_entry: StateEntry*;
%{ GetContractAddressStateEntry %}
tempvar new_state_entry = new StateEntry(
    class_hash=class_hash, ...
);
dict_update{dict_ptr=contract_state_changes}(...);
```

The same omission exists in the deprecated path at `deprecated_execute_syscalls.cairo` lines 307–329, where `execute_replace_class` also performs no class-existence check.

**Instantaneous effect within a single transaction.** The class switch is not deferred. In `execute_invoke_function_transaction` (lines 251–366 of `transaction_impls.cairo`):

1. `run_validate` executes `__validate__` using the original class hash (line 329).
2. `update_class_hash_in_execution_context` (line 334) re-reads the class hash from `contract_state_changes` — which now contains the attacker-supplied undeclared hash.
3. `__execute__` runs with the new, undeclared class hash (line 346).

This is the direct StarkNet analog of the minipool sandwiching pattern: a contract can validate under one class and execute under a completely different (and potentially undeclared) class, all within a single atomic transaction.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

When a contract calls `replace_class` with an undeclared class hash:

- The OS commits the invalid class hash to the state root via `contract_state_changes`.
- Every future transaction targeting that contract will attempt to resolve the class from the compiled-class facts. Because the hash was never declared, no compiled class exists for it.
- The contract becomes permanently unexecutable. Any ETH, ERC-20 tokens, or other assets held in the contract's storage are irrecoverably frozen.
- The state root itself encodes an invalid contract state, violating the protocol invariant that every contract's class hash must correspond to a declared class.

---

### Likelihood Explanation

- **Reachable by any unprivileged actor** who controls a deployed contract (or any contract whose logic calls `replace_class`).
- No special role, leaked key, or operator privilege is required.
- The TODO comment at line 898 is a developer acknowledgment that this check is intentionally absent from the current OS code, confirming the vulnerability is present in the production path.
- The deprecated syscall path (`deprecated_execute_syscalls.cairo` lines 307–329) is equally unprotected, widening the attack surface to legacy contracts.

---

### Recommendation

In `execute_replace_class` (both `syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`), add a validation step after reading `class_hash` that asserts the hash exists in the declared-class registry (`contract_class_changes` for same-block declarations, or via a hint-based lookup into the global class trie for previously declared classes). The check should be enforced at the Cairo/OS level so it is part of the proof, not merely a sequencer-side guard.

---

### Proof of Concept

1. **Deploy** a contract `C` that holds user funds and exposes a `replace_and_freeze` entry point.
2. **Invoke** `C.replace_and_freeze`, which internally calls the `replace_class` syscall with an arbitrary undeclared hash (e.g., `0xdeadbeef`).
3. The OS executes `execute_replace_class`:
   - `class_hash = 0xdeadbeef` is read from the syscall pointer.
   - No declared-class check is performed (line 898 TODO).
   - `contract_state_changes` is updated: `C` now has `class_hash = 0xdeadbeef`.
4. The state root is updated to reflect `C`'s new (invalid) class hash.
5. Any subsequent transaction targeting `C` fails at class resolution — the compiled class for `0xdeadbeef` does not exist in the block's `compiled_class_facts`.
6. All funds in `C`'s storage are permanently frozen.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-910)
```text
    let class_hash = request.class_hash;

    // TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}

    tempvar new_state_entry = new StateEntry(
        class_hash=class_hash, storage_ptr=state_entry.storage_ptr, nonce=state_entry.nonce
    );

    dict_update{dict_ptr=contract_state_changes}(
        key=contract_address,
        prev_value=cast(state_entry, felt),
        new_value=cast(new_state_entry, felt),
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo (L307-329)
```text
func execute_replace_class{contract_state_changes: DictAccess*, revert_log: RevertLogEntry*}(
    contract_address, syscall_ptr: ReplaceClass*
) {
    alloc_locals;
    let class_hash = syscall_ptr.class_hash;

    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}

    tempvar new_state_entry = new StateEntry(
        class_hash=class_hash, storage_ptr=state_entry.storage_ptr, nonce=state_entry.nonce
    );

    dict_update{dict_ptr=contract_state_changes}(
        key=contract_address,
        prev_value=cast(state_entry, felt),
        new_value=cast(new_state_entry, felt),
    );
    assert [revert_log] = RevertLogEntry(selector=CHANGE_CLASS_ENTRY, value=state_entry.class_hash);
    let revert_log = &revert_log[1];

    return ();
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L329-347)
```text
        run_validate(block_context=block_context, tx_execution_context=tx_execution_context);
    }
    let validate_gas_consumed = pre_validate_gas - remaining_gas;
    tempvar remaining_gas = initial_user_gas_bound - validate_gas_consumed;

    let updated_tx_execution_context = update_class_hash_in_execution_context(
        execution_context=tx_execution_context
    );

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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L94-107)
```text
func update_class_hash_in_execution_context{range_check_ptr, contract_state_changes: DictAccess*}(
    execution_context: ExecutionContext*
) -> ExecutionContext* {
    let (state_entry: StateEntry*) = dict_read{dict_ptr=contract_state_changes}(
        key=execution_context.execution_info.contract_address
    );
    return new ExecutionContext(
        entry_point_type=execution_context.entry_point_type,
        class_hash=state_entry.class_hash,
        calldata_size=execution_context.calldata_size,
        calldata=execution_context.calldata,
        execution_info=execution_context.execution_info,
        deprecated_tx_info=execution_context.deprecated_tx_info,
    );
```
