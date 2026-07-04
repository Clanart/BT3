### Title
Missing Class Hash Validation in `execute_replace_class` Allows Permanent Freezing of Contract Funds — (File: `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo`)

---

### Summary

The `execute_replace_class` syscall implementation in the StarkNet OS Cairo program accepts any arbitrary class hash without verifying it corresponds to a previously declared contract class. A contract can replace its own class hash with an undeclared value, permanently rendering itself inaccessible and freezing all funds it holds. The missing check is explicitly acknowledged by a TODO comment in the code.

---

### Finding Description

In `execute_replace_class`, the OS reads the requested new class hash from the syscall request and immediately writes it into `contract_state_changes` via `dict_update`, with no validation that the hash exists in the declared class registry:

```cairo
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

The state is then squashed and committed to the Patricia Merkle Tree at end-of-block via `state_update` → `compute_contract_state_commitment`, permanently recording the invalid class hash on-chain. [2](#0-1) 

In any subsequent block, when a call targets this contract, `execute_entry_point` reads the (now-invalid) class hash from the state entry and attempts to resolve it:

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

Since the undeclared hash is absent from `contract_class_changes`, `dict_read` returns 0. `find_element` then searches for a compiled class with hash 0, which does not exist, causing the OS execution to fail. The sequencer, detecting this during pre-execution, will permanently exclude all calls to the contract — making it forever inaccessible.

---

### Impact Explanation

**Critical. Permanent freezing of funds.**

Once a contract's class hash is replaced with an undeclared value and the block is committed, the state transition is irreversible. No future block can successfully execute a call to that contract because the OS cannot resolve the class hash to a compiled class. Any ERC-20 tokens, ETH, or other assets held in the contract's storage are permanently frozen with no recovery path.

---

### Likelihood Explanation

**Medium-High.** The `replace_class` syscall is a standard, permissionless operation available to any Sierra contract. The triggering path requires only that a contract (controlled by an unprivileged user) invoke `replace_class` with a hash that was never declared. This can occur:

- **Maliciously**: An attacker deploys a contract that appears to hold or manage funds, lures victims to deposit, then calls `replace_class(arbitrary_undeclared_hash)` to freeze all deposited funds — a direct analog to the honeypot pattern in the reference report.
- **Accidentally**: A buggy upgrade path in any contract could supply an incorrect hash.

No privileged role, leaked key, or operator cooperation is required.

---

### Recommendation

In `execute_replace_class`, before writing the new state entry, verify that `class_hash` is present in `contract_class_changes` with a non-zero compiled class hash (i.e., it has been declared in the current or a prior block). Concretely:

```cairo
// Verify the new class hash is declared.
let (compiled_class_hash) = dict_read{dict_ptr=contract_class_changes}(key=class_hash);
assert_not_zero(compiled_class_hash);
```

This mirrors the validation already performed implicitly in `execute_entry_point` and closes the gap noted by the existing TODO.

---

### Proof of Concept

1. **Deploy** a contract `Vault` that holds user funds and exposes a function `self_destruct()` which calls `replace_class(0xdeadbeef)` where `0xdeadbeef` is never declared.
2. **Lure** victims to deposit funds into `Vault` (e.g., by advertising yield or a service).
3. **Call** `Vault.self_destruct()` — the OS processes `execute_replace_class` at lines 878–916 of `syscall_impls.cairo` with no validation, updating the state entry's `class_hash` to `0xdeadbeef`.
4. **Block commits**: `state_update` squashes and commits the invalid class hash to the global state root via `compute_contract_state_commitment`.
5. **Future calls** to `Vault` reach `execute_entry_point` (lines 154–166), where `dict_read` returns 0 for `0xdeadbeef`, `find_element` fails to locate a compiled class, and the OS cannot prove any block containing such a call.
6. The sequencer permanently excludes all calls to `Vault`. All deposited funds are frozen with no recovery mechanism.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/state.cairo (L48-113)
```text
func state_update{poseidon_ptr: PoseidonBuiltin*, hash_ptr: HashBuiltin*, range_check_ptr}(
    os_state_update: OsStateUpdate, should_allocate_aliases: felt
) -> (squashed_os_state_update: SquashedOsStateUpdate*, state_update_output: CommitmentUpdate*) {
    alloc_locals;

    // Create PatriciaUpdateConstants struct for patricia update.
    let (local patricia_update_constants: PatriciaUpdateConstants*) = patricia_update_constants_new(
        );

    // (Maybe) allocate aliases and squash the final contract state tree.
    let (
        n_contract_state_changes, squashed_contract_state_changes_start
    ) = squash_state_changes_and_maybe_allocate_aliases(
        contract_state_changes_start=os_state_update.contract_state_changes_start,
        contract_state_changes_end=os_state_update.contract_state_changes_end,
        should_allocate_aliases=should_allocate_aliases,
    );

    // State is finalized.
    %{ ComputeCommitmentsOnFinalizedStateWithAliases %}

    // Compute the contract state commitment.
    let contract_state_tree_update_output = compute_contract_state_commitment(
        contract_state_changes_start=squashed_contract_state_changes_start,
        n_contract_state_changes=n_contract_state_changes,
        patricia_update_constants=patricia_update_constants,
    );

    // Squash the contract class tree.
    let (n_class_updates, squashed_class_changes) = squash_class_changes(
        class_changes_start=os_state_update.contract_class_changes_start,
        class_changes_end=os_state_update.contract_class_changes_end,
    );

    // Update the contract class tree.
    let (contract_class_tree_update_output) = compute_class_commitment(
        class_changes_start=squashed_class_changes,
        n_class_updates=n_class_updates,
        patricia_update_constants=patricia_update_constants,
    );

    // Compute the initial and final roots of the global state.
    let (local initial_global_root) = calculate_global_state_root(
        contract_state_root=contract_state_tree_update_output.initial_root,
        contract_class_root=contract_class_tree_update_output.initial_root,
    );
    let (local final_global_root) = calculate_global_state_root(
        contract_state_root=contract_state_tree_update_output.final_root,
        contract_class_root=contract_class_tree_update_output.final_root,
    );

    // Prepare the return values.
    tempvar squashed_os_state_update = new SquashedOsStateUpdate(
        contract_state_changes=squashed_contract_state_changes_start,
        n_contract_state_changes=n_contract_state_changes,
        contract_class_changes=squashed_class_changes,
        n_class_updates=n_class_updates,
    );

    tempvar state_update_output = new CommitmentUpdate(
        initial_root=initial_global_root, final_root=final_global_root
    );

    return (
        squashed_os_state_update=squashed_os_state_update, state_update_output=state_update_output
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
