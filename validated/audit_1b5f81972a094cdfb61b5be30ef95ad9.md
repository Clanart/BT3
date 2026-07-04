### Title
Missing Declared Class Validation in `execute_replace_class` Allows Permanent Contract Fund Freezing — (`File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall handler in the StarkNet OS does not validate that the caller-supplied `class_hash` is a declared class before permanently overwriting the contract's class hash in state. An attacker-controlled contract can invoke `replace_class` with an arbitrary undeclared hash, permanently bricking the contract and freezing any funds it holds.

---

### Finding Description

In `execute_replace_class` (lines 877–916 of `syscall_impls.cairo`), the OS reads `class_hash` directly from the syscall request and writes it into the contract's `StateEntry` without any check that the hash corresponds to a declared class:

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

The in-code `TODO` at line 898 is an explicit developer acknowledgment that this validation is absent. [2](#0-1) 

Contrast this with `execute_entry_point`, which is the code path that runs whenever a contract is subsequently called. It performs a `dict_read` on `contract_class_changes` keyed by the contract's `class_hash`, then calls `find_element` to locate the compiled class in the block's `compiled_class_facts_bundle`:

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

If `class_hash` was set to an undeclared value via `replace_class`, the `dict_read` returns 0 (the default for an uninitialized entry), and `find_element` will fail to locate a compiled class with hash 0, causing the OS execution to abort. The contract becomes permanently inaccessible.

The `StateEntry` struct that stores the class hash is defined in `commitment.cairo`:

```cairo
struct StateEntry {
    class_hash: felt,
    storage_ptr: DictAccess*,
    nonce: felt,
}
``` [4](#0-3) 

Once the state is committed (via `compute_contract_state_commitment` → Patricia tree update → L1 proof verification), the invalid `class_hash` is permanently encoded in the global state root. There is no on-chain mechanism to recover from this. [5](#0-4) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Any ERC-20 token balance, ETH, or STRK held by a contract whose class hash is replaced with an undeclared value becomes permanently inaccessible. The contract's state entry is committed to the L1-verified global state root with an invalid class hash. All future calls to the contract fail at the OS level (the `find_element` call in `execute_entry_point` aborts), and no withdrawal, transfer, or recovery function can ever execute again.

---

### Likelihood Explanation

The `replace_class` syscall is callable by any contract on itself. The realistic attack surface is:

1. **Upgradeable/proxy contracts** that accept a caller-supplied class hash and forward it to `replace_class` — a common pattern in StarkNet. An attacker who can influence the class hash argument (e.g., through a reentrancy path, a missing access-control check, or a social-engineering attack on an admin) can supply an undeclared hash.
2. **Malicious contract deployment**: An attacker deploys a contract that holds victim funds (e.g., a shared escrow or liquidity pool clone) and then calls `replace_class` with an undeclared hash before the victim can withdraw.

The missing check is confirmed by the developer's own `TODO` comment, indicating the OS was shipped without this guard.

---

### Recommendation

Inside `execute_replace_class`, before writing the new `StateEntry`, verify that `class_hash` maps to a non-zero compiled class hash in `contract_class_changes`:

```cairo
let class_hash = request.class_hash;

// Validate that the class hash is declared.
let (compiled_class_hash: felt) = dict_read{dict_ptr=contract_class_changes}(
    key=class_hash
);
if (compiled_class_hash == 0) {
    write_failure_response(remaining_gas=remaining_gas, failure_felt=ERROR_INVALID_ARGUMENT);
    return ();
}
```

This mirrors the lookup already performed in `execute_entry_point` and ensures only declared classes can be set as a contract's class hash.

---

### Proof of Concept

1. Attacker deploys `VictimEscrow`, a contract that holds user funds and exposes an `upgrade(class_hash: felt)` function that calls `replace_class(class_hash)` with insufficient access control.
2. Attacker calls `VictimEscrow.upgrade(0xdeadbeef)` where `0xdeadbeef` is not a declared class hash.
3. The OS executes `execute_replace_class`:
   - Reads `class_hash = 0xdeadbeef` from the request. [6](#0-5) 
   - Skips the missing declared-class check (line 898 TODO). [2](#0-1) 
   - Writes `StateEntry(class_hash=0xdeadbeef, ...)` into `contract_state_changes`. [7](#0-6) 
4. The block is proven and the state (with `class_hash=0xdeadbeef` for `VictimEscrow`) is committed to L1.
5. Any subsequent call to `VictimEscrow` reaches `execute_entry_point`, which does `dict_read(contract_class_changes, key=0xdeadbeef)` → returns 0, then `find_element(..., key=0)` → aborts. [3](#0-2) 
6. All funds in `VictimEscrow` are permanently frozen.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L25-29)
```text
struct StateEntry {
    class_hash: felt,
    storage_ptr: DictAccess*,
    nonce: felt,
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/commitment.cairo (L76-111)
```text
func compute_contract_state_commitment{hash_ptr: HashBuiltin*, range_check_ptr}(
    contract_state_changes_start: DictAccess*,
    n_contract_state_changes: felt,
    patricia_update_constants: PatriciaUpdateConstants*,
) -> CommitmentUpdate {
    alloc_locals;

    // Hash the entries of the contract state changes to prepare the input for the commitment tree
    // multi-update.
    let (local hashed_state_changes: DictAccess*) = alloc();
    compute_contract_state_commitment_inner(
        state_changes=contract_state_changes_start,
        n_contract_state_changes=n_contract_state_changes,
        hashed_state_changes=hashed_state_changes,
        patricia_update_constants=patricia_update_constants,
    );

    // Compute the initial and final roots of the contracts' state tree.
    local initial_root;
    local final_root;

    %{ SetPreimageForStateCommitments %}

    // Call patricia_update_using_update_constants() instead of patricia_update()
    // in order not to repeat globals_pow2 calculation.
    patricia_update_using_update_constants(
        patricia_update_constants=patricia_update_constants,
        update_ptr=hashed_state_changes,
        n_updates=n_contract_state_changes,
        height=MERKLE_HEIGHT,
        prev_root=initial_root,
        new_root=final_root,
    );

    return (CommitmentUpdate(initial_root=initial_root, final_root=final_root));
}
```
