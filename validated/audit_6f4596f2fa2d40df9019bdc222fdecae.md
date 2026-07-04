### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the new class hash supplied by a contract is a previously declared class. This is structurally identical to the H-02 pattern: a critical validation check is applied in one code path (`execute_declare_transaction`) but is absent in a related, mutually dependent path (`execute_replace_class`). An unprivileged contract deployer can exploit this to permanently freeze funds held by any contract they control, by replacing the contract's class hash with an arbitrary undeclared value.

---

### Finding Description

In `execute_declare_transaction`, the OS rigorously validates that a class hash corresponds to a real Sierra class before recording it in `contract_class_changes`: [1](#0-0) 

The class hash is verified against a computed `finalize_class_hash` result, and only then is it written into `contract_class_changes` (the registry of declared classes): [2](#0-1) 

By contrast, `execute_replace_class` — which updates a live contract's active class hash in `contract_state_changes` — performs **no such check**. The code contains an explicit TODO acknowledging the missing validation: [3](#0-2) 

The function reads `request.class_hash` and unconditionally writes it into the contract's `StateEntry` without consulting `contract_class_changes` or any other declared-class registry. There is no assertion that `class_hash` was ever declared.

---

### Impact Explanation

When a contract's `StateEntry.class_hash` is set to an undeclared value:

1. The OS state is committed with the invalid class hash.
2. Any future transaction that calls the affected contract requires the OS to look up the compiled class for that hash. Since the class was never declared, no compiled class facts exist for it.
3. The sequencer's simulation of any such call fails unconditionally — the transaction can never be included in a block.
4. All funds held in the contract's storage become permanently inaccessible: no `__execute__`, no withdrawal, no upgrade path can succeed.

This constitutes **permanent freezing of funds** — a Critical-severity impact under the allowed scope.

---

### Likelihood Explanation

- The `replace_class` syscall is callable by any contract during execution — it requires no privileged role.
- The attacker's entry path is fully unprivileged: deploy a contract, deposit victim funds (or attract deposits via a legitimate-looking interface), then call `replace_class` with an arbitrary felt value as the class hash.
- The TODO comment at line 898 confirms the development team is aware the check is absent, meaning the missing validation is not a design choice but an unfinished implementation.
- The `execute_replace_class` function is reachable from `execute_syscalls` via the `REPLACE_CLASS_SELECTOR` branch: [4](#0-3) 

---

### Recommendation

Inside `execute_replace_class`, after reading `class_hash` from the request, add a lookup into `contract_class_changes` (or the committed class state) to assert that `class_hash` maps to a non-zero compiled class hash. This mirrors the invariant enforced in `execute_declare_transaction`:

```cairo
// After: let class_hash = request.class_hash;
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This ensures `replace_class` can only target classes that have been properly declared and validated by the OS, closing the asymmetry between the two code paths.

---

### Proof of Concept

1. Attacker deploys `VaultContract` holding user funds (e.g., via the `deploy` syscall or `execute_deploy_account_transaction`).
2. Users deposit tokens into `VaultContract`.
3. Attacker calls a function on `VaultContract` that internally issues the `replace_class` syscall with `class_hash = 0xdeadbeef` (an arbitrary undeclared felt).
4. The OS processes `execute_replace_class`:
   - Gas is deducted.
   - `contract_state_changes[vault_address].class_hash` is set to `0xdeadbeef`.
   - The revert log records the old class hash.
   - No validation of `0xdeadbeef` occurs.
5. The block is committed. The vault's class hash is now `0xdeadbeef` on-chain.
6. Any subsequent transaction attempting to call `VaultContract` (withdraw, transfer, etc.) requires the OS to resolve compiled class facts for `0xdeadbeef`. No such facts exist.
7. The sequencer's simulation rejects every such transaction. No withdrawal is ever included in a block.
8. All funds in `VaultContract` are permanently frozen. [5](#0-4)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L738-743)
```text
        let expected_class_hash = finalize_class_hash(
            contract_class_component_hashes=contract_class_component_hashes
        );
        with_attr error_message("Invalid class hash pre-image.") {
            assert [class_hash_ptr] = expected_class_hash;
        }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L816-819)
```text
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L878-916)
```text
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
