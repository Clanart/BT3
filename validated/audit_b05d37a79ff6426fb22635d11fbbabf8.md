### Title
Missing Declared Class Hash Validation in `execute_replace_class` Allows Permanent Contract Freezing — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the supplied `class_hash` corresponds to a previously declared contract class. An unprivileged contract owner can call `replace_class` with an arbitrary, undeclared class hash. The OS accepts and commits this state change without validation, permanently rendering the contract uncallable and freezing any funds it holds.

---

### Finding Description

In `execute_replace_class`, after deducting gas, the OS reads the requested `class_hash` directly from the syscall request and writes it into `contract_state_changes` with no check that the hash has ever been declared:

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
```

The developer-acknowledged TODO at line 898 confirms this check is intentionally absent. The `contract_class_changes` dictionary (class_hash → compiled_class_hash) is never consulted to verify the new class exists.

After the block is proven and the state is committed, the contract's on-chain class hash is permanently set to the invalid value. Any subsequent transaction that calls this contract will reach `execute_entry_point`, which does:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash
);
// ...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    elm_size=CompiledClassFact.SIZE,
    n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
    key=compiled_class_hash,
);
```

`dict_read` returns 0 (the default for an undeclared class), and `find_element` with key=0 will fail to locate a matching compiled class fact, causing the OS proof for any block containing a call to that contract to be unprovable. The contract is permanently bricked.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any ERC-20 balance, ETH, or other assets held in the storage of the replaced contract become permanently inaccessible. Because the class hash is committed to the Merkle state trie, there is no protocol-level mechanism to recover from this state. The contract cannot be called, upgraded again, or self-destructed, since all entry points require a valid class lookup.

---

### Likelihood Explanation

**Medium-High.** The attack requires only that the attacker own or control a deployed contract (achievable by any unprivileged user via the `deploy` syscall or `DeployAccount` transaction). No privileged role, leaked key, or external dependency is needed. The missing check is explicitly acknowledged in the source with a TODO dated 2026, meaning it is a known gap that has not been closed. Any contract that holds third-party funds (multisig, vault, DEX pool) is a realistic target.

---

### Recommendation

Inside `execute_replace_class`, before writing the new `StateEntry`, verify that `class_hash` is present in `contract_class_changes` (i.e., has a non-zero compiled class hash):

```cairo
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the validation already performed implicitly in `execute_entry_point` and makes the OS the authoritative enforcer of the invariant rather than relying solely on the sequencer's off-chain checks.

---

### Proof of Concept

1. Attacker deploys a vault contract `V` that holds user funds.
2. Attacker submits an invoke transaction whose `__execute__` calls the `replace_class` syscall with `class_hash = 0xdeadbeef` (any value not present in `contract_class_changes`).
3. `execute_replace_class` in the OS accepts the call: gas is deducted, `contract_state_changes[V].class_hash` is set to `0xdeadbeef`, and the block is proven successfully (the invalid hash is only a problem for *future* calls, not the current one).
4. The state root is updated on L1 with `V`'s class hash = `0xdeadbeef`.
5. Any subsequent transaction calling `V` reaches `execute_entry_point`, which calls `dict_read(contract_class_changes, 0xdeadbeef)` → returns 0, then `find_element(..., key=0)` → no match → OS proof for that block fails.
6. The sequencer must permanently exclude all calls to `V`. All funds in `V`'s storage are frozen with no recovery path. [1](#0-0) [2](#0-1)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-916)
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
