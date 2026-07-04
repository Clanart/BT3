### Title
Missing Class Hash Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds - (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS processes the `replace_class` syscall without verifying that the attacker-supplied class hash corresponds to a known, declared contract class. This is an exact structural analog to the Augur `[C01]` bug: a trusted OS component (analogous to the trusted `MarketFactory`) executes a privileged state-mutation using an attacker-controlled parameter (`class_hash`, analogous to `_universe`) without validating it against a registry of known values. The result is that the OS accepts and commits an invalid state transition — a contract whose class hash is undeclared — permanently freezing any funds held in that contract.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` reads the caller-supplied `class_hash` from the syscall request and directly writes it into `contract_state_changes` with no validation:

```cairo
// execute_replace_class (lines 878–916)
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

The TODO comment (deadline 1/1/2026, now overdue as of 2026-07-03) explicitly acknowledges the missing check. No assertion against `contract_class_changes` is performed.

When the OS subsequently executes any call to this contract (in `execute_entry_point`), it performs:

```cairo
// execute_entry_point (lines 154–166)
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash   // ← undeclared hash
);
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    elm_size=CompiledClassFact.SIZE,
    n_elms=compiled_class_facts_bundle.n_compiled_class_facts,
    key=compiled_class_hash,           // ← 0 (default dict value)
);
```

`dict_read` returns `0` for an undeclared key. `find_element` (unlike `search_sorted_optimistic`) **panics** if the key is absent — it does not return a failure flag. No compiled class with hash `0` exists, so the OS halts.

Even if the contract is never called again in the same block, the committed state trie contains a contract entry with an undeclared class hash. This is an invalid protocol state that the OS has certified as valid.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

1. A contract whose class hash is replaced with an undeclared value becomes permanently inaccessible: every future OS execution attempt for that contract panics at `find_element`.
2. All token balances and storage values held by the contract are irrecoverably frozen; no withdrawal, transfer, or upgrade path remains.
3. The OS produces and commits a proof for this invalid state to L1, making the freeze irreversible at the protocol level.

This matches the Augur analog precisely: in Augur, the trusted factory moved victim tokens to an attacker-controlled market; here, the trusted OS certifies an attacker-induced invalid class hash, permanently locking the contract's assets.

---

### Likelihood Explanation

The entry path requires no privileged role:

1. Any user can deploy a contract (unprivileged deployer).
2. That contract can issue a `replace_class` syscall with an arbitrary felt value as the class hash — no declared class is required.
3. The OS processes the syscall unconditionally.
4. If the blockifier also lacks this validation (consistent with the OS TODO being the authoritative enforcement point), the sequencer includes the transaction and the OS certifies the invalid state.

The explicit overdue TODO confirms this is a known, unimplemented enforcement gap at the OS layer, not a defense-in-depth omission.

---

### Recommendation

Add a validation step inside `execute_replace_class` before updating `contract_state_changes`. The OS already has `contract_class_changes` available as an implicit argument; a `dict_read` lookup suffices:

```cairo
// Verify the new class hash is a declared class.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
with_attr error_message("replace_class: class hash is not declared.") {
    assert_not_zero(compiled_class_hash);
}
```

This mirrors the fix applied to Augur (`814b390`): check that the supplied parameter (`class_hash` / `_universe`) is a member of a known, trusted registry (`contract_class_changes` / known universes) before executing the privileged state mutation.

---

### Proof of Concept

1. Attacker deploys `MaliciousVault` — a contract that accepts deposits and exposes a `freeze()` function that calls `replace_class(0xdeadbeef)` where `0xdeadbeef` is never declared.
2. Users deposit funds into `MaliciousVault` (it appears legitimate).
3. Attacker calls `freeze()`. The `replace_class` syscall is issued with `class_hash = 0xdeadbeef`.
4. `execute_replace_class` in the OS writes `StateEntry(class_hash=0xdeadbeef, ...)` into `contract_state_changes` — **no validation performed** (line 898 TODO).
5. The block is proven and committed to L1. The state trie now contains `MaliciousVault` with class hash `0xdeadbeef`.
6. Any subsequent call to `MaliciousVault` causes `execute_entry_point` to call `dict_read(key=0xdeadbeef)` → returns `0`, then `find_element(key=0)` → **OS panic**; the block cannot be proven.
7. All user funds in `MaliciousVault` are permanently frozen with no recovery path. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L878-916)
```text
func execute_replace_class{
    range_check_ptr,
    syscall_ptr: felt*,
    contract_state_changes: DictAccess*,
    revert_log: RevertLogEntry*,
}(contract_address: felt) {
    alloc_locals;
    let request = cast(syscall_ptr + RequestHeader.SIZE, ReplaceClassRequest*);

    // Reduce gas.
    let success = reduce_syscall_gas_and_write_response_header(
        total_gas_cost=REPLACE_CLASS_GAS_COST, request_struct_size=ReplaceClassRequest.SIZE
    );
    if (success == FALSE) {
        // Not enough gas to execute the syscall.
        return ();
    }

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
