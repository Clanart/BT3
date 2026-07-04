### Title
Missing Declared-Class Validation in `execute_replace_class` Enables Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS Cairo program does not verify that the new class hash supplied by a contract corresponds to a previously declared class. An unprivileged contract can call `replace_class` with an arbitrary, undeclared felt value as the class hash. Once committed to state, any future call to that contract will fail irrecoverably inside the OS prover, permanently freezing all funds held by the contract.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function reads the requested class hash directly from the syscall request and writes it into `contract_state_changes` without any membership check against `contract_class_changes` (the dictionary of declared class hashes):

```cairo
// Replaces the class.
func execute_replace_class{...}(contract_address: felt) {
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
```

The developer-acknowledged TODO at line 898 confirms the check is intentionally absent. [1](#0-0) 

Contrast this with `execute_entry_point`, which resolves a contract's class hash by looking it up in `contract_class_changes` and then calling `find_element` over the compiled class facts bundle:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    ...
    key=compiled_class_hash,
);
``` [2](#0-1) 

If `class_hash` was never declared, `dict_read` returns 0, and `find_element` will fail to locate a compiled class with hash 0, causing the OS proof to be unprovable for any block that subsequently calls the affected contract.

---

### Impact Explanation

**Impact: Critical — Permanent Freezing of Funds.**

Once a contract's state entry is updated with an undeclared class hash, the contract becomes permanently non-executable within the OS. Any block that includes a call to that contract cannot be proven. Because the state root has already committed the invalid class hash, the contract's storage (and any token balances or assets it holds) is irrecoverably frozen. There is no upgrade path: `replace_class` itself requires the contract to be callable, which it no longer is.

---

### Likelihood Explanation

**Likelihood: Medium.**

The attack requires a contract whose code path calls `replace_class` with an attacker-controlled or invalid hash. This is reachable by:

1. A malicious contract author who deploys a contract accepting user funds and then calls `replace_class(0xdeadbeef...)` to freeze them.
2. A legitimate contract with a logic bug or reentrancy path that allows an external caller to trigger `replace_class` with an arbitrary argument.

No privileged role, leaked key, or network-level capability is required. Any unprivileged transaction sender can deploy such a contract and trigger the syscall.

---

### Recommendation

Before committing the new class hash to `contract_state_changes`, verify that `class_hash` exists as a key in `contract_class_changes` (i.e., it has been declared in the current or a prior block). The check should mirror the lookup already performed in `execute_entry_point`:

```cairo
// Verify the class has been declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This resolves the acknowledged TODO at line 898 of `syscall_impls.cairo`. [3](#0-2) 

---

### Proof of Concept

1. **Deploy a malicious contract** whose `__execute__` function calls the `replace_class` syscall with an arbitrary felt (e.g., `0x1337`) that was never declared.
2. **Send funds** (e.g., ERC-20 tokens) to the contract address.
3. **Invoke the contract** — the OS executes `execute_replace_class`, which writes `class_hash=0x1337` into `contract_state_changes` with no validation. [4](#0-3) 
4. **State is committed** with the invalid class hash in the contract's `StateEntry`.
5. **Any subsequent call** to the contract reaches `execute_entry_point`, which does `dict_read(contract_class_changes, key=0x1337)` → returns 0, then `find_element(..., key=0)` → fails. The block cannot be proven. [2](#0-1) 
6. **Result**: All funds held by the contract are permanently frozen; no recovery mechanism exists within the OS.

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
