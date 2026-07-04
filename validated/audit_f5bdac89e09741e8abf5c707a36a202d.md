### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Contract Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the new class hash supplied by a contract is a previously declared class. The OS commits the updated contract state — including the arbitrary, undeclared class hash — to the global state root without any cross-reference against `contract_class_changes`. Any contract that calls `replace_class` with an undeclared hash becomes permanently unexecutable, freezing all funds held in its storage.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function processes the `replace_class` syscall:

```cairo
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

The explicit `TODO` at line 898 acknowledges the missing check. The function writes `class_hash` (attacker-controlled) directly into `contract_state_changes` without verifying that `class_hash` has a corresponding entry in `contract_class_changes` (the declared-class registry). The same omission exists in the deprecated path:

```cairo
func execute_replace_class{contract_state_changes: DictAccess*, revert_log: RevertLogEntry*}(
    contract_address, syscall_ptr: ReplaceClass*
) {
    ...
    let class_hash = syscall_ptr.class_hash;
    // No declared-class check here either.
    ...
}
```

When a contract is subsequently called, the OS looks up its class hash in `contract_class_changes`:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
```

Because `contract_class_changes` is initialized with default value `0` (`dict_new()`), an undeclared class hash returns `0`. The subsequent `find_element` call searching for a compiled class with hash `0` will fail (no such compiled class exists), making it impossible to produce a valid OS proof for any future block that calls the frozen contract.

The state commitment produced by `state_update` does not cross-reference `contract_state_changes` class hashes against `contract_class_changes`, so the block containing the `replace_class` call produces a valid proof and is accepted on L1 — permanently locking the contract's storage.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once the state root containing `class_hash = <undeclared>` is committed to L1, the contract's storage (and any ERC-20 balances or other assets it holds) becomes permanently inaccessible. No valid OS proof can ever be generated for a block that calls the frozen contract, because the class lookup will always fail. The funds are irrecoverably frozen.

---

### Likelihood Explanation

Any deployed contract can call `replace_class` on itself. An attacker deploys a contract that accepts deposits (or is a shared protocol contract), waits for funds to accumulate, then calls `replace_class` with an arbitrary felt value as the class hash. The OS accepts the transaction and commits the invalid state. No privileged access is required — only the ability to deploy and call a contract.

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, verify that it exists in `contract_class_changes` (i.e., was declared in the current or a prior block). Concretely, perform a `dict_read` on `contract_class_changes` with `key=class_hash` and assert the result is non-zero. This is exactly what the existing TODO comment calls for. The same fix must be applied to the deprecated path in `deprecated_execute_syscalls.cairo`.

---

### Proof of Concept

1. Attacker deploys contract `C` with an entrypoint that calls `replace_class(0xdeadbeef)`.
2. Users deposit funds into `C`'s storage (e.g., via an ERC-20 transfer).
3. Attacker sends a transaction invoking the malicious entrypoint.
4. The OS executes `execute_replace_class`:
   - `class_hash = 0xdeadbeef` (not in `contract_class_changes`)
   - No declared-class check is performed (line 898 TODO)
   - `contract_state_changes[C].class_hash` is set to `0xdeadbeef`
5. `state_update` squashes and commits the state; the block proof is valid and accepted on L1.
6. In any subsequent block, a call to `C` causes `dict_read(contract_class_changes, 0xdeadbeef)` to return `0`, and `find_element(..., key=0)` fails — no valid proof can be produced.
7. All funds in `C`'s storage are permanently frozen.

---

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
