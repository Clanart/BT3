### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Contract Freezing — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The OS-level implementation of the `replace_class` syscall accepts any arbitrary class hash as the new class for a contract without verifying that the supplied hash corresponds to a previously declared class. A contract can therefore replace its own class with a hash that has never been declared, rendering itself permanently uncallable and freezing all funds held in its storage.

---

### Finding Description

In `execute_replace_class` (lines 877–916 of `syscall_impls.cairo`), after deducting gas the function reads `request.class_hash` and immediately writes it into `contract_state_changes` with no check that the hash exists in the declared-class registry:

```cairo
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
``` [1](#0-0) 

The inline `TODO` comment explicitly acknowledges the missing check. The `contract_class_changes` dictionary (populated by `declare` transactions) maps class hashes to compiled class hashes. If a contract sets its class hash to a value absent from that dictionary, every subsequent call to the contract will fail at class-lookup time, and the state change is permanent once the enclosing transaction is committed.

The revert log entry written at line 912 only protects against intra-transaction reverts:

```cairo
assert [revert_log] = RevertLogEntry(selector=CHANGE_CLASS_ENTRY, value=state_entry.class_hash);
``` [2](#0-1) 

If the transaction itself succeeds (which it will, because `replace_class` does not fail on an undeclared hash), the bad class hash is committed to global state and no future mechanism can undo it without a separate upgrade transaction — which is impossible if the contract is already uncallable.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any ERC-20 balance, ETH, or other asset stored in the contract's storage slots becomes permanently inaccessible once the contract's class hash is set to an undeclared value. The contract cannot be called to transfer, withdraw, or otherwise move those assets. Because the OS commits the state update unconditionally on transaction success, there is no recovery path.

---

### Likelihood Explanation

The `replace_class` syscall is callable by any contract on itself — no privileged role is required. A malicious contract deployer can:

1. Deploy a contract that accepts user deposits.
2. Accumulate user funds.
3. Issue a single `replace_class` syscall with an arbitrary, never-declared hash (e.g., `felt 1`).
4. The transaction succeeds; the contract's class hash is permanently set to the undeclared value.
5. All deposited funds are frozen.

This is a one-transaction, zero-privilege attack. The entry path is a standard `invoke` transaction from any account.

---

### Recommendation

Inside `execute_replace_class`, before updating `contract_state_changes`, assert that `class_hash` is present in `contract_class_changes` (i.e., has been declared in the current or a prior block). Concretely, perform a `dict_read` on `contract_class_changes` keyed by `class_hash` and assert the returned compiled class hash is non-zero. This is exactly what the existing TODO describes and what analogous syscalls (e.g., `execute_deploy`) implicitly rely on through the class-fact validation performed in `validate_compiled_class_facts_post_execution`. [3](#0-2) 

---

### Proof of Concept

1. Declare class `A` (a valid Sierra class that accepts ETH deposits and exposes a `replace_class` entry point).
2. Deploy contract `C` with class `A`.
3. Users call `C` to deposit funds; storage slots in `C` accumulate balances.
4. Attacker sends an `invoke` transaction to `C` calling `replace_class(class_hash=0x1)`.
5. `execute_replace_class` in the OS reads `class_hash = 0x1`, skips the missing declared-class check, and writes `StateEntry(class_hash=0x1, ...)` into `contract_state_changes`.
6. The transaction succeeds and is included in a block; `state_update` commits the new class hash.
7. Any subsequent call to `C` fails at class-lookup time because `0x1` is not in the compiled-class registry.
8. All funds in `C`'s storage are permanently frozen. [1](#0-0) [4](#0-3)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L877-916)
```text
// Replaces the class.
func execute_replace_class{
    range_check_ptr,
    syscall_ptr: felt*,
    contract_state_changes: DictAccess*,
    revert_log: RevertLogEntry*,
}(contract_address: felt) {
    alloc_locals;
    let request = cast(syscall_ptr + RequestHeader.SIZE, ReplaceClassRequest*);

    // Reduce gas.
    let success = reduce_syscall_gas_and_write_response_header(
        total_gas_cost=REPLACE_CLASS_GAS_COST, request_struct_size=ReplaceClassRequest.SIZE
    );
    if (success == FALSE) {
        // Not enough gas to execute the syscall.
        return ();
    }

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

    assert [revert_log] = RevertLogEntry(selector=CHANGE_CLASS_ENTRY, value=state_entry.class_hash);
    let revert_log = &revert_log[1];

    return ();
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_syscalls.cairo (L195-203)
```text
    if (selector == REPLACE_CLASS_SELECTOR) {
        execute_replace_class(contract_address=execution_context.execution_info.contract_address);
        %{ OsLoggerExitSyscall %}
        return execute_syscalls(
            block_context=block_context,
            execution_context=execution_context,
            syscall_ptr_end=syscall_ptr_end,
        );
    }
```
