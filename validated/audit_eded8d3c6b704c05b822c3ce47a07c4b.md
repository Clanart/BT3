### Title
Missing Validation of `class_hash` in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS accepts any arbitrary `class_hash` value — including zero or any undeclared class hash — without validating that the hash corresponds to a declared contract class. A contract can exploit this to permanently brick itself, freezing any funds it holds, with no OS-level enforcement preventing it.

---

### Finding Description

In `execute_replace_class` (lines 877–916 of `syscall_impls.cairo`), after gas is deducted, the function unconditionally writes the caller-supplied `class_hash` into `contract_state_changes` with no validation:

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

The developer TODO comment at line 898 explicitly acknowledges the missing check. There is no assertion that:
- `class_hash != 0`
- The class hash exists in `contract_class_changes` (i.e., has been declared)

When a future call is made to a contract whose `class_hash` was replaced with an undeclared value, `execute_entry_point` performs:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash   // undeclared hash → returns 0
);
...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    elm_size=CompiledClassFact.SIZE,
    n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
    key=compiled_class_hash,           // key=0, not in facts → OS assertion failure
);
```

`dict_read` returns 0 (the default) for an undeclared class hash, and `find_element` with `key=0` will fail if 0 is not present in the compiled class facts bundle, causing the OS proof to be unprovable for any block containing a call to that contract.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

A contract that holds user funds can call `replace_class(0)` (or any undeclared hash). The OS commits this state without objection. All future calls to that contract will fail at the OS level (the block cannot be proven if such a call is included). The sequencer will refuse to include transactions targeting the bricked contract, making the funds permanently inaccessible. There is no recovery path once the state is committed on-chain.

---

### Likelihood Explanation

**Low.** The attacker must control a contract (deployer role, which is unprivileged on StarkNet) and either:
- Intentionally deploy a honeypot contract that accepts deposits and then calls `replace_class(0)`, or
- Accidentally trigger this via a buggy contract.

No privileged role, leaked key, or operator cooperation is required. The syscall is reachable by any contract execution.

---

### Recommendation

Add the following validations inside `execute_replace_class` before updating `contract_state_changes`:

1. Assert `class_hash != 0` (analogous to the M-02 `merkleRoot == ""` check).
2. Assert that `class_hash` exists in `contract_class_changes` (i.e., has been declared in the current or a prior block), analogous to the M-02 enforcement of maximum mintable units.

```cairo
// Proposed fix:
assert_not_zero(class_hash);
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);  // class must be declared
```

---

### Proof of Concept

1. Attacker deploys `HoneypotContract` which exposes a `deposit()` function and an `admin_brick()` function that calls `replace_class(0)`.
2. Users call `deposit()` and transfer funds to `HoneypotContract`.
3. Attacker calls `admin_brick()`. The OS processes `execute_replace_class` with `class_hash=0` — no validation fires, gas is deducted, and `contract_state_changes[HoneypotContract].class_hash = 0` is committed.
4. Any subsequent transaction targeting `HoneypotContract` causes `execute_entry_point` to call `dict_read(contract_class_changes, key=0)` → returns 0, then `find_element(compiled_class_facts, key=0)` → OS assertion failure.
5. The sequencer permanently excludes all calls to `HoneypotContract`. Deposited funds are frozen with no recovery path. [1](#0-0) [2](#0-1)

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
