### Title
Missing Declared Class Existence Check in `execute_replace_class` Allows Permanent Freezing of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS accepts an arbitrary caller-supplied class hash and writes it directly into the contract's state entry without verifying that the hash corresponds to a declared (compiled) class. An explicit `TODO` comment in the code acknowledges this missing check. Because the OS is the proof boundary, any contract can call `replace_class` with an undeclared hash, permanently bricking itself and freezing any funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` (lines 877–916) reads `class_hash` directly from the syscall request and updates the contract's `StateEntry` without any existence validation:

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

After the state is updated with the undeclared hash, any subsequent call to the contract reaches `execute_entry_point`, which performs:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash   // the undeclared hash
);
// compiled_class_hash == 0 (default for unknown key)

let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    elm_size=CompiledClassFact.SIZE,
    n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
    key=compiled_class_hash,           // key == 0, not found → prover panic
);
``` [2](#0-1) 

`find_element` is a Cairo primitive that panics (hard assertion failure) when the key is absent. Because no compiled class with hash `0` exists in a normal block, the OS cannot produce a valid proof for any block containing a call to the bricked contract. The sequencer is forced to permanently exclude all transactions targeting that contract address.

This is the direct analog of the external report's pattern: a critical operation (here, class replacement) proceeds without verifying that the target resource (the declared class) actually exists, leading to an irrecoverable bad state.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any ERC-20 balance, NFT, or protocol-level asset stored in the storage of a contract whose class hash has been replaced with an undeclared value becomes permanently inaccessible. No withdrawal, transfer, or administrative call can ever be included in a provable block again, because every such call causes a prover-level panic in `execute_entry_point`. The state transition is committed on-chain (the `replace_class` transaction itself is valid and provable), but all subsequent interactions are unprovable, making the freeze irreversible.

---

### Likelihood Explanation

Any contract that exposes a `replace_class` upgrade path — whether intentionally (upgradeable proxies, account contracts) or inadvertently — is vulnerable. A malicious contract author can deploy a contract that holds user deposits and then call `replace_class` with `0xdeadbeef` or any other undeclared felt, permanently locking all deposited funds. The OS provides zero protection at the syscall boundary, as the TODO comment explicitly confirms the check is absent. The attack requires only a standard user transaction; no privileged role is needed.

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, verify that the hash exists in `contract_class_changes` (i.e., it was previously declared via a `declare` transaction). Concretely, perform a `dict_read` on `contract_class_changes` with `key=class_hash` and assert the returned `compiled_class_hash` is non-zero. This mirrors the short-term recommendation in the external report: check existence before performing the state-mutating operation.

---

### Proof of Concept

1. Attacker deploys contract `V` (a vault) that accepts user deposits and exposes an `upgrade(new_class_hash)` entry point that calls the `replace_class` syscall.
2. Users deposit funds into `V`; `V`'s storage now holds balances.
3. Attacker calls `upgrade(0xdeadbeef)` — an arbitrary felt that has never been declared.
4. `execute_replace_class` writes `class_hash=0xdeadbeef` into `V`'s `StateEntry` with no validation. The transaction is provable and committed on-chain. [3](#0-2) 
5. Any user who now calls `V.withdraw(...)` causes the OS to execute `execute_entry_point` for `V`. `dict_read` on `contract_class_changes` with key `0xdeadbeef` returns `0`. `find_element` with key `0` finds no compiled class and panics. [4](#0-3) 
6. The sequencer cannot include any transaction targeting `V` in a provable block. All user funds in `V` are permanently frozen.

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
