### Title
Missing Declared-Class Membership Check in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the caller-supplied `class_hash` corresponds to a previously declared class. Any contract can replace its own class with an arbitrary, undeclared class hash. The OS accepts this silently, corrupting the contract's state entry. Any subsequent call to that contract causes proof generation to fail, permanently freezing all funds held by the contract.

---

### Finding Description

The vulnerability class from the reference report is **missing positive membership check**: a function validates that a parameter is not in a "bad" set, but omits the check that it is in the "valid" set.

The exact analog exists in `execute_replace_class` in `syscall_impls.cairo`:

```cairo
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

The code itself contains a developer TODO explicitly acknowledging the missing check. The function only verifies gas sufficiency, then unconditionally writes the caller-supplied `class_hash` into `contract_state_changes`. It never checks that `class_hash` has a corresponding entry in `contract_class_changes` (i.e., that it was previously declared via a `declare` transaction).

The same flaw exists in the deprecated path:

```cairo
func execute_replace_class{contract_state_changes: DictAccess*, revert_log: RevertLogEntry*}(
    contract_address, syscall_ptr: ReplaceClass*
) {
    alloc_locals;
    let class_hash = syscall_ptr.class_hash;
    // No membership check at all.
    ...
    dict_update{dict_ptr=contract_state_changes}(...);
}
```

The downstream consumer of the corrupted state is `execute_entry_point`:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash   // undeclared hash → returns 0
);
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    ...
    key=compiled_class_hash,           // key = 0, never present → proof fails
);
```

When `class_hash` was never declared, `dict_read` on `contract_class_changes` returns the default value `0`. `find_element` then searches for a compiled class with hash `0`, which never exists, causing proof generation to abort for any block that includes a call to the affected contract.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

A contract holding any ERC-20 tokens, ETH, or other assets can call `replace_class` with an arbitrary undeclared felt value as the new class hash. After the syscall succeeds:

1. The contract's `class_hash` in `contract_state_changes` is set to the undeclared value.
2. The class tree has no entry for this hash.
3. Every future call to the contract reaches `execute_entry_point`, which reads `compiled_class_hash = 0` from `contract_class_changes`, then fails at `find_element`.
4. The contract is permanently uncallable; all funds it holds are permanently frozen.

This matches the **Critical: Permanent freezing of funds** impact category.

---

### Likelihood Explanation

Any deployed contract can invoke the `replace_class` syscall — no privileged role, leaked key, or operator cooperation is required. The attacker only needs to control a contract (which they can deploy themselves) that holds funds, or to trick a victim contract into calling `replace_class` with a bad hash (e.g., via a malicious callback). The syscall is a standard, publicly accessible protocol primitive.

---

### Recommendation

In both `execute_replace_class` implementations, add a check that the supplied `class_hash` is present in `contract_class_changes` with a non-zero compiled class hash before updating the state:

```cairo
// Verify the class has been declared.
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=class_hash
);
with_attr error_message("Class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the fix recommended in the reference report: change from checking only the "not-in-bad-set" condition to also asserting "is-in-valid-set".

---

### Proof of Concept

1. Deploy contract `Victim` holding 1000 STRK.
2. From `Victim`, invoke the `replace_class` syscall with `class_hash = 0xdeadbeef` (any felt not present in `contract_class_changes`).
3. The OS executes `execute_replace_class` in `syscall_impls.cairo` lines 877–916: gas is deducted, `contract_state_changes[Victim].class_hash` is set to `0xdeadbeef`, no error is raised.
4. In the next block, any transaction calling `Victim` reaches `execute_entry_point` (`execute_entry_point.cairo` lines 154–166): `dict_read(contract_class_changes, 0xdeadbeef)` returns `0`; `find_element(..., key=0)` finds no match; proof generation aborts.
5. `Victim`'s 1000 STRK are permanently frozen.

**Relevant code locations:**

- Missing check (new syscall path): [1](#0-0) 
- Missing check (deprecated syscall path): [2](#0-1) 
- Downstream failure point (`find_element` on hash=0): [3](#0-2)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L896-915)
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
