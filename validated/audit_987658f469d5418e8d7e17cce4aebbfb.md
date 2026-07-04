### Title
Missing Declared-Class Membership Check in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS accepts any arbitrary `class_hash` value from the caller and writes it directly into `contract_state_changes` without verifying that the supplied hash corresponds to a previously declared class. This is structurally identical to the M-06 pattern: a missing membership/eligibility check before a consequential state mutation. The result is that a contract's class hash can be permanently set to an undeclared value, making the contract permanently uncallable and freezing all funds held within it.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function reads the requested `class_hash` from the syscall request and immediately writes it to the contract's `StateEntry` in `contract_state_changes`:

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

The developer-acknowledged TODO comment confirms the check is absent. No assertion is made that `class_hash` exists as a key in `contract_class_changes` (i.e., that it was previously declared via a `declare` transaction).

When any subsequent call is made to this contract — in the same block or any future block — `execute_entry_point` performs:

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
```

Because the class was never declared, `dict_read` returns 0 (the default for an uninitialized dict entry). `find_element` is then called with `key=0`. Unlike `search_sorted_optimistic`, `find_element` is not failure-tolerant: if no compiled class with hash 0 exists in the bundle, the hint cannot satisfy the assertion and proof generation fails. Even if the sequencer's blockifier catches the call failure and excludes the transaction, the contract's class hash in the committed state remains set to the undeclared value. The contract is permanently uncallable, and all funds held in its storage are permanently frozen.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once `replace_class` is called with an undeclared `class_hash`:

1. The OS writes the invalid class hash into `contract_state_changes` and commits it to the state root.
2. Every future call to the contract resolves to `compiled_class_hash = 0` via `dict_read`.
3. `find_element` with `key=0` fails (no compiled class with hash 0 exists), making any block containing a call to this contract unprovable.
4. The sequencer's blockifier will simulate the failure and exclude such calls, but the contract's state is already corrupted — it can never be called again.
5. All ERC-20 balances, NFTs, or protocol-controlled value stored in the contract's storage are permanently inaccessible.

---

### Likelihood Explanation

Any deployed contract can issue the `replace_class` syscall. An attacker who controls a contract (e.g., a malicious DeFi protocol that attracts user deposits, or a contract with a logic bug exploitable via reentrancy) can call `replace_class` with an arbitrary undeclared felt value. No privileged role, leaked key, or external dependency is required. The entry path is a standard user-initiated transaction.

---

### Recommendation

Before writing the new `class_hash` to `contract_state_changes`, verify that the hash exists as a declared class by asserting it is present in `contract_class_changes`:

```cairo
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the check that `execute_entry_point` already performs implicitly (via `find_element`) but enforces it eagerly at the point of mutation, consistent with the developer's own TODO.

---

### Proof of Concept

1. Attacker deploys Contract A (e.g., a token vault) and attracts user deposits.
2. Attacker calls Contract A, which internally issues `replace_class(0xdeadbeef)` — an arbitrary felt that was never declared.
3. The OS `execute_replace_class` handler accepts the call. No membership check is performed. `contract_state_changes` is updated: `A.class_hash = 0xdeadbeef`.
4. The state root is updated and the block is proved successfully (the `replace_class` transaction itself is valid from the OS's perspective).
5. In the next block, any user attempts to withdraw funds by calling Contract A.
6. `execute_entry_point` does `dict_read(contract_class_changes, 0xdeadbeef)` → returns 0.
7. `find_element(..., key=0)` fails — no compiled class with hash 0 exists.
8. The block cannot be proved; the sequencer excludes the call.
9. No call to Contract A can ever succeed. All deposited funds are permanently frozen.

---

**Root cause:** [1](#0-0) 

**Downstream failure point:** [2](#0-1)

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
