### Title
`replace_class` Syscall Accepts Non-Existent Class Hash, Permanently Freezing Contract Funds — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` function in the StarkNet OS Cairo program does not verify that the new class hash supplied by a contract actually corresponds to a declared class. A contract can replace its own class with an arbitrary, non-existent class hash. After this state change is committed, any future call to that contract will cause the OS to fail when looking up the compiled class, making the contract permanently uncallable and freezing any funds it holds.

---

### Finding Description

In `syscall_impls.cairo`, the `execute_replace_class` function processes the `replace_class` syscall:

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

The TODO comment at line 898 explicitly acknowledges the missing check. The OS unconditionally writes `class_hash` (which can be any arbitrary felt value) into `contract_state_changes` without verifying it exists in `contract_class_changes`. [1](#0-0) 

The same missing check exists in the deprecated path: [2](#0-1) 

When any subsequent transaction calls the affected contract, `execute_entry_point` runs:

```cairo
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=execution_context.class_hash   // <-- the non-existent class hash
);
...
let (compiled_class_fact: CompiledClassFact*) = find_element(
    array_ptr=compiled_class_facts_bundle.compiled_class_facts,
    ...
    key=compiled_class_hash,           // <-- 0 or garbage; not in the bundle
);
```

`dict_read` on an undeclared class hash returns 0 (the default). `find_element` then asserts the key exists in the compiled class facts array. Since 0 is not a valid compiled class hash, this assertion fails, making the block unprovable. [3](#0-2) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once a contract's class hash is replaced with a non-existent value, the contract becomes permanently uncallable at the OS level. Any block that includes a transaction targeting that contract will fail to produce a valid proof. All tokens, NFTs, or other assets held by the contract are frozen forever with no recovery path, because:

1. The state change is committed to the Merkle tree.
2. No future block can execute any entry point of the contract (the OS proof fails).
3. There is no administrative override or escape hatch in the OS.

---

### Likelihood Explanation

The attack is reachable by any unprivileged contract deployer:

1. Attacker deploys a contract that accepts user deposits (e.g., a fake yield vault or escrow).
2. Users deposit funds into the contract.
3. Attacker calls a function in the contract that internally issues `replace_class(arbitrary_felt)` where `arbitrary_felt` is any value not corresponding to a declared class.
4. The OS accepts the syscall without validation.
5. The contract is permanently bricked; all deposited funds are frozen.

No privileged access, leaked keys, or external dependencies are required. The `replace_class` syscall is a standard user-accessible syscall callable by any contract on itself.

---

### Recommendation

Before writing the new class hash into `contract_state_changes`, verify that it exists as a declared class by checking `contract_class_changes`:

```cairo
// Verify the new class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

This mirrors the fix recommended for M-10: validate existence of the referenced entity before accepting the operation. The TODO comment at line 898 already identifies this as a known gap. [4](#0-3) 

---

### Proof of Concept

1. Declare class `A` and deploy contract `C` with class `A`. Contract `C` has a function `brick()` that calls `replace_class(0xdeadbeef)` where `0xdeadbeef` is never declared.
2. Users send funds to contract `C`.
3. Attacker calls `C.brick()`. The OS processes `execute_replace_class`, writes `class_hash=0xdeadbeef` into `contract_state_changes` for address `C` — no existence check is performed.
4. In the next block, any transaction calling contract `C` reaches `execute_entry_point`:
   - `dict_read(contract_class_changes, key=0xdeadbeef)` → returns `0` (undeclared).
   - `find_element(..., key=0)` → assertion failure; block is unprovable.
5. All funds in `C` are permanently frozen.

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
