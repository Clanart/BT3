### Title
Missing Class Hash Validation in `execute_replace_class` Enables Permanent Freezing of Contract Funds — (`execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not verify that the requested new class hash corresponds to a previously declared contract class. Any contract can call `replace_class` with an arbitrary, undeclared hash. Once committed to state, the contract becomes permanently inaccessible, freezing all funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, the function `execute_replace_class` (lines 877–916) reads the requested class hash directly from the syscall request and writes it into `contract_state_changes` without any check that the hash is a declared class:

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

The TODO comment at line 898 explicitly acknowledges this missing enforcement. [2](#0-1) 

When a future transaction targets the upgraded contract, `execute_entry_point` performs:

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
``` [3](#0-2) 

If `class_hash` is undeclared, `dict_read` returns 0 (the default for an absent key in `contract_class_changes`), and `find_element` will fail to locate a compiled class fact for hash `0`. The contract becomes permanently unreachable at the OS level, with no recovery path.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any contract that holds assets (tokens, ETH bridged via L1→L2, NFTs, etc.) and whose class hash is replaced with an undeclared value will have those assets permanently locked. The state transition is committed to the proven block output; there is no rollback mechanism at the OS level once the block is finalized on L1.

---

### Likelihood Explanation

The `replace_class` syscall is the StarkNet analog of UUPS upgrades. Proxy contracts and upgradeable DeFi contracts routinely expose upgrade entry points. An attacker who can invoke such an entry point (e.g., via a missing access-control check, a reentrancy path, or a contract that intentionally allows public upgrades) can supply an arbitrary class hash. Because the OS imposes no constraint on the hash value, the attack succeeds unconditionally at the protocol layer regardless of what the user contract does. Any unprivileged transaction sender can also deploy their own contract and self-inflict this to demonstrate the missing OS-level guard.

---

### Recommendation

Inside `execute_replace_class`, before writing the new `StateEntry`, assert that `class_hash` exists as a key in `contract_class_changes` (i.e., it has a non-zero compiled class hash mapping). This mirrors the check already performed in `execute_entry_point` when resolving a class for execution, and closes the gap identified by the existing TODO comment.

---

### Proof of Concept

1. Deploy contract `A` that holds funds and exposes a public `upgrade(new_class_hash)` function which calls `replace_class(new_class_hash)` without validating the argument.
2. An attacker calls `upgrade(0xdeadbeef)` — an undeclared hash.
3. `execute_replace_class` writes `class_hash=0xdeadbeef` into `contract_state_changes` for contract `A`'s address with no OS-level rejection. [4](#0-3) 
4. The block is proven and finalized on L1.
5. Any subsequent call to contract `A` reaches `execute_entry_point`, which performs `dict_read` on `contract_class_changes` for key `0xdeadbeef`, receives `0`, then calls `find_element` searching for compiled class hash `0` — which does not exist — causing OS execution to abort. [5](#0-4) 
6. All funds in contract `A` are permanently frozen with no recovery path.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_entry_point.cairo (L154-176)
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
    let (success, compiled_class_entry_point: CompiledClassEntryPoint*) = get_entry_point(
        compiled_class=compiled_class, execution_context=execution_context
    );

    if (success == 0) {
        %{ ExitCall %}
        let (retdata: felt*) = alloc();
        assert retdata[0] = ERROR_ENTRY_POINT_NOT_FOUND;
        return (is_reverted=1, retdata_size=1, retdata=retdata);
```
