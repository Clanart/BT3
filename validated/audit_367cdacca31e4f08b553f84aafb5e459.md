### Title
Missing Declared-Class Validation in `execute_replace_class` Allows Permanent Contract Freezing — (`crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS program does not verify that the caller-supplied class hash corresponds to a previously declared contract class before committing the state update. An acknowledged `TODO` comment marks the missing check. This is directly analogous to M-17: just as the ECG protocol failed to check gauge status before allowing weight decrement, the OS fails to check class declaration status before allowing class replacement. The result is that a contract can permanently replace its own class hash with an arbitrary, undeclared value, rendering itself permanently uncallable and freezing any funds it holds.

---

### Finding Description

In `execute_replace_class` (lines 878–916 of `syscall_impls.cairo`), the OS reads `request.class_hash` from the syscall buffer and immediately writes it into `contract_state_changes` without any verification that the hash exists in `contract_class_changes` (i.e., was previously declared via a `declare` transaction):

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

The `TODO` comment at line 898 explicitly acknowledges the missing check. By contrast, `execute_declare_transaction` in `transaction_impls.cairo` enforces that a class can only be declared once by using `prev_value=0` in `dict_update` on `contract_class_changes`:

```cairo
assert_not_zero(compiled_class_hash);
dict_update{dict_ptr=contract_class_changes}(
    key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
);
``` [2](#0-1) 

When `execute_entry_point` is later called for a contract whose class hash is undeclared, it performs:

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

A `dict_read` on an undeclared key returns 0 (the default). `find_element` with key=0 will panic if no compiled class with hash 0 is present in the bundle, causing the OS program to abort. Even if the sequencer reverts the subsequent call, the contract's class hash in committed state is permanently set to the undeclared value, making the contract permanently uncallable.

---

### Impact Explanation

**Critical — Permanent Freezing of Funds.**

Once a contract's class hash is replaced with an undeclared hash:
1. The state diff commits `class_hash = undeclared_value` for that contract address.
2. Every future call to the contract causes `execute_entry_point` to look up `undeclared_value` in `contract_class_changes`, receive 0, then fail to find a compiled class — either panicking the OS or causing a hard revert.
3. The contract is permanently uncallable. Any ERC-20 tokens, ETH, or other assets held in the contract's storage are permanently frozen with no recovery path.

---

### Likelihood Explanation

The attack is reachable by any unprivileged transaction sender:

1. An attacker deploys a contract (or exploits an existing contract with an exposed `replace_class` path).
2. The attacker submits a transaction that calls `replace_class(arbitrary_undeclared_hash)`.
3. The OS processes the syscall, finds no check, and commits the state update.
4. The contract is permanently bricked.

Contracts that expose `replace_class` to governance votes, timelocks, or multi-sig mechanisms are particularly at risk: an attacker who can influence the governance input (e.g., via a flash-loan vote or a social-engineering attack on a multi-sig) can supply an undeclared hash and freeze all protocol funds.

---

### Recommendation

Before committing the class hash update in `execute_replace_class`, verify that the new class hash exists in `contract_class_changes` (i.e., has a non-zero compiled class hash entry):

```cairo
// Verify the new class hash has been declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("replace_class: class hash not declared") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the invariant already enforced in `execute_declare_transaction` and closes the gap acknowledged by the existing `TODO` comment.

---

### Proof of Concept

1. Deploy contract `VaultA` holding 1000 STRK.
2. `VaultA` exposes an `upgrade(new_class_hash: felt)` function that calls `replace_class(new_class_hash)`.
3. Attacker calls `VaultA.upgrade(0xdeadbeef)` where `0xdeadbeef` was never declared.
4. The OS executes `execute_replace_class`: no check is performed; `contract_state_changes[VaultA_address].class_hash` is set to `0xdeadbeef`.
5. In any subsequent block, any call to `VaultA` causes `execute_entry_point` to do `dict_read(contract_class_changes, 0xdeadbeef)` → returns 0 → `find_element(..., key=0)` → panic or hard revert.
6. `VaultA` is permanently uncallable. The 1000 STRK are permanently frozen.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-913)
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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L816-819)
```text
    assert_not_zero(compiled_class_hash);
    dict_update{dict_ptr=contract_class_changes}(
        key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
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
