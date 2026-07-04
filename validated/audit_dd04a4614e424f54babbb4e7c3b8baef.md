### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Contract Bricking — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the new class hash supplied by a contract is actually declared in `contract_class_changes`. The missing check is explicitly acknowledged by a TODO comment in the production code. An attacker who can influence the class hash argument of a `replace_class` call (e.g., via a contract with an upgradeable pattern that accepts user-supplied class hashes) can set a contract's class hash to an arbitrary undeclared value, permanently bricking the contract and freezing any funds it holds.

---

### Finding Description

In `execute_replace_class`, the OS reads the caller-supplied `class_hash` from the syscall request and writes it directly into `contract_state_changes` without checking whether that hash exists in `contract_class_changes` (the declared-class registry):

```cairo
func execute_replace_class{
    ...
    contract_state_changes: DictAccess*,
    ...
}(contract_address: felt) {
    ...
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
    ...
}
``` [1](#0-0) 

The `dict_update` call modifies `contract_state_changes` (the per-contract state dict), but there is no corresponding lookup or assertion against `contract_class_changes` (the class declaration dict). The TODO comment on line 898 explicitly documents this as a known missing check in production code. [2](#0-1) 

By contrast, the `execute_declare_transaction` function correctly enforces `prev_value=0` when writing to `contract_class_changes`, ensuring a class can only be declared once: [3](#0-2) 

No equivalent guard exists in `execute_replace_class` to confirm the incoming `class_hash` is present in `contract_class_changes`.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is set to an undeclared value and the block is committed, the contract's entry in the state tree permanently references a class that does not exist in the class tree. Any subsequent transaction that attempts to call this contract will fail at the OS level when it tries to resolve the class for execution. The contract becomes permanently uncallable. Any ERC-20 tokens, ETH, or other assets held by the contract are irretrievably frozen with no recovery path.

---

### Likelihood Explanation

The `REPLACE_CLASS_SELECTOR` syscall is reachable by any contract execution via `execute_syscalls`: [4](#0-3) 

Any contract that implements an upgrade pattern where the new class hash is passed as a parameter (a common pattern in StarkNet) is directly exploitable. An attacker submits a transaction calling the upgrade function with an arbitrary undeclared felt value as the class hash. The OS executes the syscall, writes the invalid class hash to state, and commits the block. No privileged access is required beyond the ability to call the target contract's upgrade entry point.

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, assert that the hash exists in `contract_class_changes` by performing a `dict_read` on `contract_class_changes` keyed by `class_hash` and asserting the result is non-zero (i.e., a compiled class hash has been registered for it). This mirrors the invariant already enforced in `execute_declare_transaction` and closes the gap identified by the TODO comment.

---

### Proof of Concept

1. Attacker deploys contract `V` that holds user funds and exposes `upgrade(new_class_hash: felt)` which internally calls `replace_class(new_class_hash)`.
2. Attacker calls `upgrade(0xdeadbeef)` where `0xdeadbeef` is never declared.
3. The OS executes `execute_replace_class` — no check against `contract_class_changes` is performed.
4. `dict_update` writes `StateEntry { class_hash: 0xdeadbeef, ... }` into `contract_state_changes`.
5. Block is committed; the state tree now records `V`'s class hash as `0xdeadbeef`.
6. Any future call to `V` causes the OS to look up class `0xdeadbeef`, find nothing, and fail.
7. All funds in `V` are permanently frozen.

### Citations

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
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
