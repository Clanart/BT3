### Title
Missing Validation of Declared Class Hash in `execute_replace_class` Allows Permanent Contract Freezing - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not validate that the new class hash supplied by a contract is actually a declared class. An explicit `TODO` in the code acknowledges this missing check. As a result, any contract can replace its own class hash with an arbitrary, undeclared value. Once this happens, the contract becomes permanently unexecutable, and any funds it holds are irreversibly frozen.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function reads the requested new class hash directly from the syscall request and writes it into `contract_state_changes` without any check that the hash corresponds to a previously declared class: [1](#0-0) 

The comment at line 898 explicitly acknowledges the missing guard:

```cairo
// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
``` [2](#0-1) 

When a future transaction later calls the affected contract, `execute_entry_point` performs:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash   // now the invalid/undeclared hash
);
// ...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    ...
    key=compiled_class_hash,           // resolves to 0 for undeclared hash
);
``` [3](#0-2) [4](#0-3) 

`dict_read` on an undeclared key returns the default value `0`. `find_element` then searches for a compiled class with hash `0`, which does not exist in the block's `compiled_class_facts_bundle`. In Cairo, `find_element` is a hint-backed assertion; failure to locate the element causes a hard proof failure — not a graceful transaction revert.

The `replace_class` syscall is dispatched from `execute_syscalls` without any pre-validation: [5](#0-4) 

The declare path enforces `prev_value=0` to prevent re-declaration, but there is no symmetric enforcement preventing `replace_class` from writing an undeclared hash: [6](#0-5) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is overwritten with an undeclared value via `replace_class`, the contract is permanently unexecutable. The sequencer cannot include any transaction that invokes the broken contract without causing a hard proof failure. All ERC-20 tokens, ETH, or other assets held in the contract's storage become permanently inaccessible. There is no recovery path: class declarations are one-way (`prev_value=0` enforces uniqueness), and there is no `undeclare` or `restore_class` mechanism in the OS.

---

### Likelihood Explanation

The `replace_class` syscall is callable by any contract from within its own execution context — no privileged role is required. Realistic trigger paths include:

1. A shared DeFi protocol (DEX, lending pool, vault) exposes an upgrade function that calls `replace_class`. An attacker exploits a missing access-control check in that function to supply an undeclared hash, permanently freezing all user deposits.
2. A contract developer makes a programming error and passes an uninitialized or zero-valued class hash to `replace_class`, accidentally self-destructing the contract.
3. A malicious contract developer deploys a contract, attracts user funds, then calls `replace_class(0)` to freeze withdrawals.

All three paths are reachable by an unprivileged transaction sender with no special keys or operator access.

---

### Recommendation

Inside `execute_replace_class`, before updating `contract_state_changes`, add a validation step that reads `contract_class_changes` for the supplied `class_hash` and asserts the result is non-zero (i.e., the class has been declared):

```cairo
// Verify the new class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the existing guard in `execute_declare_transaction` (`assert_not_zero(compiled_class_hash)`) and closes the gap noted in the TODO comment. [7](#0-6) 

---

### Proof of Concept

1. Attacker deploys contract `VaultAttack` that holds user ETH and exposes a public `freeze()` function containing:
   ```
   replace_class(class_hash=0xdeadbeef)   // 0xdeadbeef is never declared
   ```
2. Users deposit funds into `VaultAttack` (attracted by yield incentives).
3. Attacker calls `freeze()`. The OS executes `execute_replace_class` with `class_hash=0xdeadbeef`, writes it to `contract_state_changes` without validation, and the transaction succeeds.
4. Any subsequent `call_contract` or direct invocation of `VaultAttack` causes `execute_entry_point` to call `dict_read(key=0xdeadbeef)` → returns `0` → `find_element(key=0)` → hard proof failure.
5. The sequencer cannot include any transaction touching `VaultAttack`. All deposited funds are permanently frozen.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L154-166)
```text
    let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
        key=execution_context.class_hash
    );

    // The key must be at offset 0.
    static_assert CompiledClassFact.hash == 0;
    let compiled_class_facts_bundle = block_context.os_global_context.compiled_class_facts_bundle;
    let (compiled_class_fact: CompiledClassFact*) = find_element(
        array_ptr=compiled_class_facts_bundle.compiled_class_facts,
        elm_size=CompiledClassFact.SIZE,
        n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
        key=compiled_class_hash,
    );
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
