### Title
Missing Validation of Class Hash in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS accepts an arbitrary caller-supplied class hash and writes it directly into the contract's state entry without verifying that the hash corresponds to a previously declared class. This is structurally identical to the external report's root cause: an unvalidated address/hash is accepted as a trusted reference and used in a privileged state-mutating operation. The result is that any contract can permanently replace its own class hash with an undeclared value, making itself permanently uncallable and freezing any funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, `execute_replace_class` reads `request.class_hash` directly from the syscall request and writes it into `contract_state_changes` with no check that the hash exists in the set of declared classes:

```cairo
// syscall_impls.cairo lines 896–914
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

The TODO comment is an explicit in-code acknowledgment that this check is missing.

When a subsequent transaction calls the affected contract, `execute_entry_point` looks up the class hash from state and then calls `find_element` to locate the corresponding `CompiledClassFact`:

```cairo
// execute_entry_point.cairo lines 154–166
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

If `class_hash` was set to an undeclared value, `dict_read` returns 0 (the default for an absent key), and `find_element` with key=0 (or any undeclared hash) will fail with an assertion error because no matching `CompiledClassFact` exists. This makes the contract permanently unexecutable: no honest sequencer can include a call to it in any future block and produce a valid proof.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any funds (tokens, ETH bridged via L1→L2, NFTs, etc.) held in the storage of a contract whose class hash has been replaced with an undeclared value are permanently inaccessible. The contract cannot be called to transfer, withdraw, or otherwise move those assets. Because the invalid class hash is committed to the Merkle state root, no future block can legitimately execute that contract, and the freeze is irreversible without a protocol-level upgrade.

---

### Likelihood Explanation

**Medium.**

- The attack surface is any contract that exposes a path to `replace_class` — either intentionally (a malicious deployer) or accidentally (a buggy upgrade mechanism).
- The syscall is available to all deployed contracts; no privileged role is required.
- The missing validation is explicitly flagged with a TODO, confirming it is a known gap that has not yet been closed.
- A realistic scenario: a DeFi vault or multisig contract with a flawed upgrade function allows an attacker to supply an arbitrary class hash, triggering the freeze of all deposited user funds.

---

### Recommendation

Inside `execute_replace_class`, before writing the new class hash to state, verify that the supplied hash exists in `contract_class_changes` (i.e., it was declared in the current or a prior block). Concretely, perform a `dict_read` on `contract_class_changes` with `key=class_hash` and assert the returned compiled class hash is non-zero. This mirrors the check already performed implicitly in `execute_entry_point` but must be enforced eagerly at the point of replacement to prevent invalid state from being committed.

---

### Proof of Concept

1. Attacker deploys `VaultContract` holding user funds (e.g., 1000 STRK deposited by users).
2. `VaultContract` exposes an `upgrade(new_class_hash)` function that calls the `replace_class` syscall.
3. Attacker calls `upgrade(0xdeadbeef)` where `0xdeadbeef` is never declared on-chain.
4. The OS processes `execute_replace_class`:
   - Reads `class_hash = 0xdeadbeef` from the request.
   - Skips the missing declared-class check (the TODO).
   - Writes `StateEntry(class_hash=0xdeadbeef, ...)` into `contract_state_changes`.
   - State root is updated; the invalid class hash is now canonical.
5. In any subsequent block, a user attempts to call `VaultContract.withdraw()`.
6. `execute_entry_point` reads `class_hash=0xdeadbeef` from state, calls `dict_read` on `contract_class_changes` → returns 0, calls `find_element(..., key=0)` → assertion failure.
7. No honest sequencer can include this call; the contract is permanently frozen with all user funds locked inside. [1](#0-0) [2](#0-1)

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L153-167)
```text
    alloc_locals;
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
