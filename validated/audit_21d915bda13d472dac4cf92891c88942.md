### Title
Missing Declared Class Hash Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not validate that the new class hash supplied by a contract is a previously declared class. A malicious contract owner can call `replace_class` with an arbitrary, undeclared class hash. The OS accepts the state update unconditionally. Once committed, the contract's class hash in the global state points to a non-existent class, making the contract permanently uncallable and locking all funds held within it.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function reads the requested class hash directly from the syscall request and writes it into `contract_state_changes` without any check that the hash corresponds to a declared class:

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

The developer-inserted TODO comment explicitly acknowledges the missing guard. [1](#0-0) 

By contrast, the normal class declaration path in `execute_declare_transaction` enforces `prev_value=0` (a class can only be declared once) and asserts `compiled_class_hash != 0`, providing a proper guard: [2](#0-1) 

When a subsequent transaction attempts to call the contract whose class hash was replaced with an undeclared value, `execute_entry_point` performs:

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
``` [3](#0-2) 

`dict_read` returns `0` for an undeclared class hash (no entry in `contract_class_changes`). `find_element` then searches for a compiled class with hash `0`. If none exists in the bundle (which it won't for an arbitrary attacker-chosen hash), the Cairo VM panics, causing the entire block proof to fail. The sequencer therefore permanently excludes any transaction targeting that contract, making the contract's funds irrecoverable.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is overwritten with an undeclared value:
- The contract's storage (and any token balances it holds) remains in the global state tree.
- No transaction can ever successfully call the contract again: the OS cannot resolve the class hash to a compiled class, so the sequencer rejects all such calls.
- There is no recovery path: calling `replace_class` again requires executing the contract, which is impossible because the class hash is already invalid.
- Any ERC-20 balances, NFTs, or protocol-level funds held by the contract are permanently frozen.

---

### Likelihood Explanation

**Medium.**

The attack requires a contract that:
1. Holds other users' funds (e.g., a vault, DEX pool, or escrow).
2. Exposes a code path (directly or via an upgrade mechanism) that calls `replace_class` with an attacker-controlled hash.

A malicious contract owner can deploy a contract that appears legitimate, attract user deposits, and then invoke `replace_class` with an arbitrary undeclared felt value. The `replace_class` syscall itself does not revert, so the sequencer includes the transaction. The block is proved successfully (the invalid class hash is not exercised in that block). In all subsequent blocks, the contract is permanently bricked.

---

### Recommendation

In `execute_replace_class`, add a validation step that asserts the requested `class_hash` exists in `contract_class_changes` (i.e., has a non-zero compiled class hash), mirroring the guard already present in `execute_declare_transaction`:

```cairo
// Validate that the new class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This resolves the acknowledged TODO and closes the state-overwrite-without-guard pattern.

---

### Proof of Concept

1. Attacker deploys `VaultContract` (class hash `C1`, declared) that holds user ETH/STRK deposits and exposes an `upgrade(new_hash)` function that calls `replace_class(new_hash)`.
2. Users deposit funds into `VaultContract`.
3. Attacker calls `upgrade(0xdeadbeef)` where `0xdeadbeef` is an arbitrary undeclared felt.
4. `execute_replace_class` writes `class_hash=0xdeadbeef` into `contract_state_changes` with no validation. [4](#0-3) 
5. The block is proved successfully; `VaultContract`'s state now has `class_hash=0xdeadbeef`.
6. Any future call to `VaultContract` causes `execute_entry_point` to call `dict_read` on `0xdeadbeef` → returns `0` → `find_element` on compiled class hash `0` → element not found → OS panic. [3](#0-2) 
7. The sequencer permanently excludes all calls to `VaultContract`. All user funds are frozen with no recovery path.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-914)
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

```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L815-819)
```text
    // Note that prev_value=0 enforces that a class may be declared only once.
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L154-167)
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
    local compiled_class: CompiledClass* = compiled_class_fact.compiled_class;
```
