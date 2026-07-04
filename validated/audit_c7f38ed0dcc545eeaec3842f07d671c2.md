### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Contract Bricking — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS accepts an arbitrary `class_hash` value and writes it directly into the contract state without verifying that the hash corresponds to a declared class in `contract_class_changes`. This is an acknowledged missing check (explicit TODO at line 898). Any contract — including one holding user funds — can be permanently bricked by replacing its class with an undeclared hash, making all future calls to it unprovable and freezing any funds it holds.

---

### Finding Description

**Vulnerability class:** State-transition bypass — the OS accepts an invalid state transition (writing an undeclared class hash into contract state) without enforcing the invariant that only declared classes may be used.

In `syscall_impls.cairo`, `execute_replace_class` reads the caller-supplied `class_hash` from the syscall request and writes it unconditionally into `contract_state_changes`:

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

The same missing check exists in the deprecated path: [2](#0-1) 

When a subsequent call is made to the bricked contract, `execute_entry_point` performs:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    elm_size=CompiledClassFact.SIZE,
    n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
    key=compiled_class_hash,
);
``` [3](#0-2) 

If `class_hash` was set to an undeclared value, `dict_read` returns 0 (the Cairo dict default), and `find_element` with `key=0` will fail to locate a compiled class fact, making the block unprovable for any transaction that touches this contract.

By contrast, the `execute_declare_transaction` path correctly enforces that the class hash is a valid Sierra class hash pre-image and that `compiled_class_hash != 0` before writing to `contract_class_changes`: [4](#0-3) 

The `replace_class` path has no equivalent guard.

---

### Impact Explanation

**Impact: Critical — Permanent freezing of funds.**

Any contract that holds user funds (e.g., a multisig wallet, an ERC-20 token contract, a vault) and calls `replace_class` with an undeclared class hash — whether due to a bug in the contract or a malicious internal call — will have its class hash set to an undeclared value in the committed state. All future invocations of that contract will be unprovable at the OS level. The sequencer cannot include any transaction that calls the bricked contract in a valid block. Funds held by the contract are permanently inaccessible with no recovery path, since the state transition that bricked the contract is already committed.

---

### Likelihood Explanation

**Likelihood: Medium.**

The `replace_class` syscall is a standard StarkNet syscall available to any deployed contract. A contract that exposes an upgrade function callable by an external party (e.g., a DAO-governed proxy, a contract with a public `upgrade` entrypoint) can be triggered by an unprivileged transaction sender. The attacker only needs to supply an arbitrary felt value as the new class hash. No privileged access, key compromise, or network-level attack is required. The missing check is explicitly acknowledged in the codebase as a TODO, confirming the developers are aware the invariant is not enforced.

---

### Recommendation

In `execute_replace_class` (both `syscall_impls.cairo` and `deprecated_execute_syscalls.cairo`), before writing the new `class_hash` to `contract_state_changes`, verify that the hash exists as a key in `contract_class_changes` (i.e., that it was previously declared). This is exactly what the TODO at line 898 of `syscall_impls.cairo` calls for. The check should assert `compiled_class_hash != 0` after reading from `contract_class_changes`, mirroring the guard used in `execute_declare_transaction`.

---

### Proof of Concept

1. Deploy a contract `VaultContract` that holds user funds and exposes an `upgrade(new_class_hash: felt)` function that calls the `replace_class` syscall with the provided argument.
2. An attacker (unprivileged transaction sender) calls `VaultContract.upgrade(0xdeadbeef)` where `0xdeadbeef` is never declared as a class.
3. The OS's `execute_replace_class` in `syscall_impls.cairo` (line 896–910) writes `class_hash=0xdeadbeef` into `contract_state_changes` for `VaultContract` without any validation.
4. The state is committed with `VaultContract.class_hash = 0xdeadbeef`.
5. Any subsequent transaction invoking `VaultContract` reaches `execute_entry_point` (line 154–166 of `execute_entry_point.cairo`), which does `dict_read(key=0xdeadbeef)` on `contract_class_changes`, returning 0 (undeclared), then calls `find_element(key=0)` on the compiled class facts array, which fails because no compiled class with hash 0 exists.
6. No valid proof can be generated for any block containing a call to `VaultContract`. All funds held by `VaultContract` are permanently frozen.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L814-819)
```text
    // Declare the class hash.
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```
